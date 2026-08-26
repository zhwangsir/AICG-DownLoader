"""M21.3 LongVideoPlanner：完整剧本 → 长视频 Chunk 智能拆分规划。

三项核心分析（全部基于剧本结构化元数据的确定性规则，零 LLM 调用、零额外延迟）：

1. 场景识别（scene recognition）
   按 episode + 地点指纹把相邻场景聚成场景组；地点指纹从 description
   命中地点词库提取，未命中记 "unknown"。同组场景共享空间语境，适合并入
   同一 Chunk 让帧链在同场景内延续。

2. 镜头切换检测（shot switch detection）
   逐对相邻场景比较 shot_type / camera_movement / episode / 地点指纹，
   标记切换点与切换类型。切换点是 Chunk 边界的天然候选——帧链续写在
   镜头切换处断开，观众感知跳变最小。

3. 语义连贯性分析（semantic coherence）
   四信号加权（各 0.25）：同集 / 同地点 / 情绪相容（同情绪族）/ 叙事节拍
   衔接（hook→escalation→reversal→cliffhanger 等正典链）。输出 0-1 分，
   高分对（连续动作/情绪递进）不拆，低分对优先作边界。

切块策略：按剧本顺序贪心装填（累计时长 ≤ chunk_seconds）；跨集强制边界；
单场景超过 chunk_seconds 时拆成续写子块（后续子块 prompt 加 continuation
指令，帧链保证画面延续）。产出 LongVideoPlan 供 pipeline_orchestrator
长视频模式直接消费（chunk.prompt → LongVideoService.chunk_prompts，
chunk.intent → DriftMetricsService.chunk_intents）。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.models.schemas import Scene, Script

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 分析词库与权重
# ---------------------------------------------------------------------------
# 地点词库：命中 description 的第一个词作为场景地点指纹（按词长降序优先匹配长词）
_LOCATION_LEXICON = (
    "便利店", "办公室", "咖啡厅", "咖啡馆", "地铁站", "火车站", "停车场",
    "地下室", "天台", "街道", "巷子", "医院", "学校", "教室", "公园",
    "车站", "车内", "酒吧", "餐厅", "酒店", "警局", "法庭", "海边",
    "森林", "山上", "家中", "家里", "客厅", "卧室", "厨房", "楼道",
    "电梯", "仓库", "工厂", "实验室", "机场", "码头", "桥上", "广场",
)
_UNKNOWN_SETTING = "unknown"

# 情绪族：同族视为情绪相容（情感连续性信号）
_EMOTION_FAMILIES: tuple[frozenset[str], ...] = (
    frozenset({"calm", "neutral", "peaceful", "平静", "安静", "克制"}),
    frozenset({"tense", "tension", "nervous", "anxious", "紧张", "警觉", "不安"}),
    frozenset({"fear", "afraid", "scared", "panic", "恐惧", "惊惧", "害怕"}),
    frozenset({"anger", "angry", "furious", "愤怒", "暴怒"}),
    frozenset({"sad", "sadness", "sorrow", "悲伤", "难过", "低落"}),
    frozenset({"happy", "joy", "joyful", "喜悦", "开心", "欣喜"}),
    frozenset({"determined", "resolute", "defiant", "决绝", "坚定", "无畏"}),
    frozenset({"surprise", "surprised", "shocked", "惊讶", "震惊"}),
)

# 叙事节拍正典链（M12 VALID_NARRATIVE_BEATS）：cur = prev+1 视为顺滑衔接
_BEAT_ORDER = (
    "hook", "escalation", "reversal", "cliffhanger", "emotional_beat", "transition",
)

_W_EPISODE = 0.25
_W_SETTING = 0.25
_W_EMOTION = 0.25
_W_BEAT = 0.25

# 跨集连贯性封顶：集是短剧最大叙事结构单元，跨集帧链必然断开（planner 强制
# 拆块），即便同地点/同情绪/同节拍，连贯分也不得高于此值（如实反映结构断裂）
_CROSS_EPISODE_CAP = 0.45

# 多场景合并进同一块时的 prompt 连接符（时间顺承，帧链天然延续）
_SCENE_JOINER = " Then, "
# 单场景超时长拆块的续写指令（后续子块首帧 = 前块末帧）
_CONTINUATION_PREFIX = "The shot continues smoothly from the previous moment: "


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class ShotSwitch:
    """相邻场景间的镜头切换点（after_scene_id 之后发生切换）。"""

    after_scene_id: int
    before_scene_id: int
    kinds: list[str] = field(default_factory=list)  # episode/setting/shot_type/camera


@dataclass
class ChunkPlan:
    """单个视频 Chunk 规划：直接映射 LongVideoService.generate 的单块输入。"""

    chunk_index: int
    scene_ids: list[int]
    prompt: str  # 多块场景 prompt 顺承合并；续写子块带 continuation 前缀
    intent: str  # 中文创作意图（情感一致性评估基准）
    estimated_seconds: float
    boundary_before: str = "start"  # start/episode/scene/shot/continuous/continuation
    coherence_to_prev: float = 1.0  # 与前一块的语义连贯性（0-1）


@dataclass
class LongVideoPlan:
    """长视频整体规划产物。"""

    chunks: list[ChunkPlan] = field(default_factory=list)
    total_estimated_seconds: float = 0.0
    scene_coverage: float = 0.0  # 被规划覆盖的剧本场景比例
    shot_switches: list[ShotSwitch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunks": [
                {
                    "chunk_index": c.chunk_index,
                    "scene_ids": c.scene_ids,
                    "prompt": c.prompt,
                    "intent": c.intent,
                    "estimated_seconds": c.estimated_seconds,
                    "boundary_before": c.boundary_before,
                    "coherence_to_prev": round(c.coherence_to_prev, 3),
                }
                for c in self.chunks
            ],
            "total_estimated_seconds": self.total_estimated_seconds,
            "scene_coverage": round(self.scene_coverage, 3),
            "shot_switches": [
                {
                    "after_scene_id": s.after_scene_id,
                    "before_scene_id": s.before_scene_id,
                    "kinds": s.kinds,
                }
                for s in self.shot_switches
            ],
            "warnings": self.warnings,
        }


@dataclass
class _Unit:
    """装填最小单位：一个场景或场景拆出的续写子块。"""

    scene: Scene
    prompt: str
    seconds: float
    is_continuation: bool = False


# ---------------------------------------------------------------------------
# 规划器
# ---------------------------------------------------------------------------
class LongVideoPlanner:
    """剧本 → Chunk 规划（纯确定性规则，无外部服务依赖）。"""

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def plan(
        self,
        script: Script,
        *,
        chunk_seconds: int | None = None,
        max_chunks: int | None = None,
    ) -> LongVideoPlan:
        """把完整剧本拆成 Chunk 序列。

        chunk_seconds / max_chunks 缺省取 settings.long_video_*。
        剧本无场景抛 ValueError（调用方应先行校验剧本完整性）。
        """
        if not script.scenes:
            raise ValueError("剧本无场景，无法规划长视频")
        # 显式 None 判断：chunk_seconds=0 等非法值必须暴露而非被 or 静默回退
        if chunk_seconds is None:
            chunk_seconds = settings.long_video_chunk_seconds
        if max_chunks is None:
            max_chunks = settings.long_video_max_chunks
        if chunk_seconds <= 0:
            raise ValueError(f"chunk_seconds 非法: {chunk_seconds}")

        warnings: list[str] = []
        scenes = list(script.scenes)

        # 镜头切换检测（全剧本相邻对，供报告与边界分类共用）
        switches = self.detect_shot_switches(scenes)
        switch_map = {(s.after_scene_id, s.before_scene_id): s.kinds for s in switches}

        # 场景 → 装填单位（超时长场景拆续写子块）
        units: list[_Unit] = []
        for scene in scenes:
            units.extend(self._scene_to_units(scene, chunk_seconds, warnings))

        # 贪心装填 + 边界分类 + 连贯性打分
        chunks: list[ChunkPlan] = []
        cur_units: list[_Unit] = []
        cur_seconds = 0.0
        prev_last_scene: Scene | None = None  # 前一块末场景（连贯性打分基准）

        def _close_chunk() -> None:
            if not cur_units:
                return  # pragma: no cover — 三处调用点均有前置守卫/收尾必非空，防御不可达
            chunks.append(self._build_chunk(
                len(chunks), cur_units, prev_last_scene, switch_map,
            ))

        for unit in units:
            # 跨集强制边界：关闭当前块
            if cur_units and unit.scene.episode != cur_units[-1].scene.episode:
                _close_chunk()
                prev_last_scene = cur_units[-1].scene
                cur_units, cur_seconds = [], 0.0
            # 容量满：关闭当前块
            if cur_units and cur_seconds + unit.seconds > chunk_seconds:
                _close_chunk()
                prev_last_scene = cur_units[-1].scene
                cur_units, cur_seconds = [], 0.0
            cur_units.append(unit)
            cur_seconds += unit.seconds
        _close_chunk()

        # max_chunks 截断（覆盖度随之下降，记入 warnings）
        truncated = False
        if len(chunks) > max_chunks:
            chunks = chunks[:max_chunks]
            truncated = True

        covered = {sid for c in chunks for sid in c.scene_ids}
        total_scenes = {s.scene_id for s in scenes}
        coverage = len(covered & total_scenes) / max(1, len(total_scenes))
        if truncated:
            warnings.append(
                f"规划块数超过 max_chunks={max_chunks}，已截断；场景覆盖度 {coverage:.0%}"
            )

        return LongVideoPlan(
            chunks=chunks,
            total_estimated_seconds=sum(c.estimated_seconds for c in chunks),
            scene_coverage=coverage,
            shot_switches=switches,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # 场景识别：地点指纹
    # ------------------------------------------------------------------
    @staticmethod
    def setting_fingerprint(scene: Scene) -> str:
        """从场景描述提取地点指纹（词库首命中，长词优先）。"""
        desc = scene.description or ""
        for word in _LOCATION_LEXICON:  # 词库已按长词在前排列
            if word in desc:
                return word
        return _UNKNOWN_SETTING

    def recognize_scene_groups(self, scenes: list[Scene]) -> list[list[int]]:
        """按 (episode, 地点指纹) 把相邻场景聚成组，返回场景 ID 分组。"""
        groups: list[list[int]] = []
        cur_key: tuple[int, str] | None = None
        for scene in scenes:
            key = (scene.episode, self.setting_fingerprint(scene))
            if key != cur_key:
                groups.append([])
                cur_key = key
            groups[-1].append(scene.scene_id)
        return groups

    # ------------------------------------------------------------------
    # 镜头切换检测
    # ------------------------------------------------------------------
    def detect_shot_switches(self, scenes: list[Scene]) -> list[ShotSwitch]:
        """逐对相邻场景检测切换点，返回全部切换（含类型标注）。"""
        switches: list[ShotSwitch] = []
        for prev, cur in zip(scenes, scenes[1:]):
            kinds: list[str] = []
            if cur.episode != prev.episode:
                kinds.append("episode")
            if self.setting_fingerprint(cur) != self.setting_fingerprint(prev):
                kinds.append("setting")
            if (cur.shot_type or "") != (prev.shot_type or ""):
                kinds.append("shot_type")
            if (cur.camera_movement or "") != (prev.camera_movement or ""):
                kinds.append("camera")
            if kinds:
                switches.append(ShotSwitch(
                    after_scene_id=prev.scene_id,
                    before_scene_id=cur.scene_id,
                    kinds=kinds,
                ))
        return switches

    # ------------------------------------------------------------------
    # 语义连贯性分析（0-1，四信号等权）
    # ------------------------------------------------------------------
    def coherence(self, prev: Scene, cur: Scene) -> float:
        """相邻场景语义连贯性评分：同集/同地点/情绪相容/节拍衔接 各 0.25。

        跨集封顶 _CROSS_EPISODE_CAP：集边界是强制拆块点，连贯分如实报低。
        """
        score = 0.0
        if cur.episode == prev.episode:
            score += _W_EPISODE
        if self.setting_fingerprint(cur) == self.setting_fingerprint(prev):
            score += _W_SETTING
        score += _W_EMOTION * self._emotion_score(prev.emotion, cur.emotion)
        score += _W_BEAT * self._beat_score(prev.narrative_beat, cur.narrative_beat)
        if cur.episode != prev.episode:
            score = min(score, _CROSS_EPISODE_CAP)
        return min(1.0, score)

    @staticmethod
    def _emotion_score(prev: str, cur: str) -> float:
        p, c = (prev or "").strip().lower(), (cur or "").strip().lower()
        if p == c:
            return 1.0
        for family in _EMOTION_FAMILIES:
            if p in family and c in family:
                return 0.8
        return 0.2

    @staticmethod
    def _beat_score(prev: str, cur: str) -> float:
        p, c = (prev or "").strip(), (cur or "").strip()
        if not p and not c:
            return 0.5  # 双空：无信号，给中性分
        if not p or not c:
            return 0.4  # 单空：弱信号
        if p == c:
            return 0.6  # 同节拍：平续
        if p in _BEAT_ORDER and c in _BEAT_ORDER:
            if _BEAT_ORDER.index(c) == _BEAT_ORDER.index(p) + 1:
                return 1.0  # 正典链顺承
            return 0.3  # 逆序/跨级：转折感强
        return 0.4  # 非法值：弱信号

    # ------------------------------------------------------------------
    # 内部：场景拆单位 / 组装块
    # ------------------------------------------------------------------
    def _scene_to_units(
        self, scene: Scene, chunk_seconds: int, warnings: list[str],
    ) -> list[_Unit]:
        """场景 → 1..n 个装填单位；超 chunk_seconds 拆续写子块。"""
        prompt = (scene.prompt or "").strip()
        if not prompt:
            prompt = (scene.description or "").strip() or f"scene {scene.scene_id}"
            warnings.append(f"场景 {scene.scene_id} 无 prompt，已用描述兜底")
        duration = float(scene.duration_seconds or 0)
        if duration <= 0:
            duration = float(chunk_seconds)
            warnings.append(f"场景 {scene.scene_id} 时长非法，已按块长 {chunk_seconds}s 计")

        if duration <= chunk_seconds:
            return [_Unit(scene=scene, prompt=prompt, seconds=duration)]

        n = math.ceil(duration / chunk_seconds)
        warnings.append(
            f"场景 {scene.scene_id} 时长 {duration:.0f}s 超块长 {chunk_seconds}s，"
            f"拆为 {n} 个续写子块"
        )
        units: list[_Unit] = []
        remaining = duration
        for k in range(n):
            sec = min(float(chunk_seconds), remaining)
            units.append(_Unit(
                scene=scene,
                prompt=prompt if k == 0 else _CONTINUATION_PREFIX + prompt,
                seconds=sec,
                is_continuation=k > 0,
            ))
            remaining -= sec
        return units

    def _build_chunk(
        self,
        index: int,
        units: list[_Unit],
        prev_last_scene: Scene | None,
        switch_map: dict[tuple[int, int], list[str]],
    ) -> ChunkPlan:
        """把一组单位组装成 ChunkPlan（合并 prompt/intent，分类边界，打连贯分）。"""
        prompt = _SCENE_JOINER.join(u.prompt for u in units)
        intent = "；".join(
            f"{(u.scene.emotion or 'neutral')}:{(u.scene.description or '')[:15]}"
            for u in units
        )
        seconds = sum(u.seconds for u in units)
        first = units[0]

        boundary = "start"
        coherence = 1.0
        if prev_last_scene is not None:
            if first.is_continuation and first.scene.scene_id == prev_last_scene.scene_id:
                boundary = "continuation"
                coherence = 1.0  # 同场景续写：完全连贯
            else:
                kinds = switch_map.get(
                    (prev_last_scene.scene_id, first.scene.scene_id), [],
                )
                if "episode" in kinds:
                    boundary = "episode"
                elif "setting" in kinds:
                    boundary = "scene"
                elif "shot_type" in kinds or "camera" in kinds:
                    boundary = "shot"
                else:
                    boundary = "continuous"
                # 语义连贯性：前块末场景 × 当前块首场景，四信号加权
                coherence = self.coherence(prev_last_scene, first.scene)

        return ChunkPlan(
            chunk_index=index,
            scene_ids=[u.scene.scene_id for u in units],
            prompt=prompt,
            intent=intent,
            estimated_seconds=seconds,
            boundary_before=boundary,
            coherence_to_prev=coherence,
        )


# 全局单例（与 video_agent 等模块级单例风格一致）
long_video_planner = LongVideoPlanner()
