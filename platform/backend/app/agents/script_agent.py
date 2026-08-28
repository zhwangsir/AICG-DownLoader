"""剧本 Agent — 一句话创意 → JSON 结构化剧本。

主 LLM 走 spark02 qwen3.6-uncensored（字段名 exo_model_glm52 仅为兼容，非 EXO GLM）。
默认关闭 thinking（spark/qwen 思考链会烧掉数分钟），联网搜索默认关闭、按需开启。
输出符合 §4.8 规范的剧本 JSON。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import json_repair

from app.agents.ai_optimizer import web_search
from app.agents.base import BaseAgent
from app.config import settings
from app.models.schemas import AgentResponse, Script, ScriptRequest
from app.services.rag_service import rag_service
from app.services.style_anchor import (
    resolve_style_anchor,
    sanitize_style_conflicts,
    style_prompt_clause,
)

logger = logging.getLogger(__name__)

# ---- 短剧结构校验白名单 ----
VALID_SHOT_TYPES = {"大特写", "特写", "近景", "中景", "全景", "远景"}
SHOT_TYPE_EN_TO_ZH = {
    "extreme close-up": "大特写",
    "close-up": "特写",
    "medium close-up": "近景",
    "medium shot": "中景",
    "full shot": "全景",
    "wide shot": "远景",
    "long shot": "远景",
}
VALID_CAMERA_MOVEMENTS = {"static", "pan", "tilt", "zoom", "dolly", "tracking", "handheld"}
CAMERA_MOVEMENT_ZH_TO_EN = {
    "固定": "static",
    "摇": "pan",
    "摇镜": "pan",
    "俯仰": "tilt",
    "推": "dolly",
    "拉": "dolly",
    "推拉": "zoom",
    "变焦": "zoom",
    "跟": "tracking",
    "跟拍": "tracking",
    "手持": "handheld",
}
VALID_NARRATIVE_BEATS = {"hook", "escalation", "reversal", "cliffhanger", "emotional_beat", "transition"}
# 强情绪节拍（情绪刺激点）：transition 之外的所有节拍
STRONG_BEATS = VALID_NARRATIVE_BEATS - {"transition"}
# 每集总时长校验区间（目标 50-70 秒，留容差）
EPISODE_DURATION_MIN = 40
EPISODE_DURATION_MAX = 80
# 首镜（hook）最大时长：黄金 3 秒原则
HOOK_MAX_DURATION = 3
# 情绪点密度：连续弱节拍（transition/缺失）累计时长上限，超过即观众流失风险
DENSITY_MAX_WEAK_SECONDS = 15
# IAP 付费剧首充卡点集区间（行业惯例：第 8-12 集）
IAP_PAYWALL_EPISODE_RANGE = (8, 12)
MAX_REPAIR_ATTEMPTS = 2


def _normalize_scene_terms(scene: dict[str, Any]) -> None:
    """归一化景别/运镜用语（英文→中文、中文→英文），原地修改。"""
    shot = str(scene.get("shot_type", "")).strip()
    if shot and shot not in VALID_SHOT_TYPES:
        mapped = SHOT_TYPE_EN_TO_ZH.get(shot.lower())
        if mapped:
            scene["shot_type"] = mapped

    movement = str(scene.get("camera_movement", "")).strip()
    if movement and movement not in VALID_CAMERA_MOVEMENTS:
        mapped = CAMERA_MOVEMENT_ZH_TO_EN.get(movement)
        if mapped:
            scene["camera_movement"] = mapped
        else:
            scene["camera_movement"] = movement.lower()


def validate_script_scenes(
    scenes: list[dict[str, Any]],
    episodes: int,
    scenes_per_episode: int,
    monetization_mode: str = "iaa",
) -> list[str]:
    """校验剧本分镜是否符合短剧结构规范，返回问题列表（空列表 = 通过）。

    规则来源：2025-2026 竖屏短剧行业惯例（单集 50-70s、首镜强钩子且 ≤3s、
    结尾悬念、景别不连三、单镜时长 1-8s、运镜/景别白名单、
    每 15s 至少一个情绪刺激点、IAP 模式第 8-12 集设付费卡点）。
    """
    issues: list[str] = []
    if not scenes:
        return ["剧本没有任何分镜"]

    # 按集分组（保持 scenes 内顺序）
    by_episode: dict[int, list[dict[str, Any]]] = {}
    for s in scenes:
        by_episode.setdefault(int(s.get("episode", 1)), []).append(s)

    for ep in range(1, episodes + 1):
        ep_scenes = by_episode.get(ep, [])
        label = f"第{ep}集"
        if not ep_scenes:
            issues.append(f"{label}缺少分镜")
            continue

        # 分镜数量
        if len(ep_scenes) != scenes_per_episode:
            issues.append(f"{label}分镜数为 {len(ep_scenes)}，应为 {scenes_per_episode}")

        # 集总时长
        total = 0
        for s in ep_scenes:
            try:
                total += int(s.get("duration_seconds", 0))
            except (TypeError, ValueError):
                issues.append(f"{label} scene_id={s.get('scene_id')} 时长不是整数: {s.get('duration_seconds')!r}")
        if total < EPISODE_DURATION_MIN or total > EPISODE_DURATION_MAX:
            issues.append(f"{label}总时长 {total}s，应在 {EPISODE_DURATION_MIN}-{EPISODE_DURATION_MAX}s 之间（目标 50-70s）")

        # 首镜强钩子
        first_beat = str(ep_scenes[0].get("narrative_beat", "")).strip()
        if first_beat != "hook":
            issues.append(f"{label}首镜 narrative_beat 为 {first_beat or '缺失'}，必须为 hook（强钩子）")

        # 首镜时长：黄金 3 秒原则
        try:
            first_dur = int(ep_scenes[0].get("duration_seconds", 0))
            if first_dur > HOOK_MAX_DURATION:
                issues.append(f"{label}首镜时长 {first_dur}s 超过 {HOOK_MAX_DURATION}s（黄金 3 秒原则：hook 镜须 1-{HOOK_MAX_DURATION}s）")
        except (TypeError, ValueError):
            pass  # 时长非整数的问题在单镜校验中统一记录

        # 末镜悬念（最后一集允许 emotional_beat 收尾）
        last_beat = str(ep_scenes[-1].get("narrative_beat", "")).strip()
        allowed_last = {"cliffhanger", "emotional_beat"} if ep == episodes else {"cliffhanger"}
        if last_beat not in allowed_last:
            issues.append(f"{label}末镜 narrative_beat 为 {last_beat or '缺失'}，必须为 {'/'.join(sorted(allowed_last))}")

        # 单镜校验 + 景别连续性
        prev_shots: list[str] = []
        for s in ep_scenes:
            sid = s.get("scene_id", "?")
            shot = str(s.get("shot_type", "")).strip()
            if shot not in VALID_SHOT_TYPES:
                issues.append(f"{label} scene_id={sid} 景别 {shot or '缺失'} 不在白名单: {'/'.join(sorted(VALID_SHOT_TYPES))}")
            movement = str(s.get("camera_movement", "")).strip()
            if movement not in VALID_CAMERA_MOVEMENTS:
                issues.append(f"{label} scene_id={sid} 运镜 {movement or '缺失'} 不在白名单: {'/'.join(sorted(VALID_CAMERA_MOVEMENTS))}")
            try:
                dur = int(s.get("duration_seconds", 0))
                if dur < 1 or dur > 8:
                    issues.append(f"{label} scene_id={sid} 单镜时长 {dur}s，应在 1-8s")
            except (TypeError, ValueError):
                pass  # 上面已记录
            beat = str(s.get("narrative_beat", "")).strip()
            if beat and beat not in VALID_NARRATIVE_BEATS:
                issues.append(f"{label} scene_id={sid} narrative_beat {beat} 不在白名单: {'/'.join(sorted(VALID_NARRATIVE_BEATS))}")
            dialogue = str(s.get("dialogue", "") or "")
            if len(dialogue) > 40:
                issues.append(f"{label} scene_id={sid} 台词 {len(dialogue)} 字，超过 40 字上限")

            prev_shots.append(shot)
            if len(prev_shots) >= 3 and len(set(prev_shots[-3:])) == 1:
                issues.append(f"{label} scene_id={sid} 处连续 3 镜景别相同（{shot}），违反景别交替规则")

        # 情绪点密度：连续弱节拍（transition/缺失）累计时长不得超过 15s
        weak_run_seconds = 0
        weak_run_start: Any = None
        for s in ep_scenes:
            beat = str(s.get("narrative_beat", "")).strip()
            try:
                dur = int(s.get("duration_seconds", 0))
            except (TypeError, ValueError):
                dur = 0
            if beat in STRONG_BEATS:
                weak_run_seconds = 0
                weak_run_start = None
                continue
            if weak_run_start is None:
                weak_run_start = s.get("scene_id", "?")
            weak_run_seconds += dur
            if weak_run_seconds > DENSITY_MAX_WEAK_SECONDS:
                issues.append(
                    f"{label} scene_id={weak_run_start} 起连续 {weak_run_seconds}s 无情绪刺激点"
                    f"（全为 transition/缺失节拍），须每 {DENSITY_MAX_WEAK_SECONDS}s 内安排一个强节拍"
                    f"（hook/escalation/reversal/cliffhanger/emotional_beat）"
                )
                weak_run_seconds = 0
                weak_run_start = None

    # IAP 付费剧：第 8-12 集须设首充卡点（该区间内至少一集含 reversal 反转）
    if monetization_mode == "iap" and episodes >= IAP_PAYWALL_EPISODE_RANGE[0]:
        zone_end = min(IAP_PAYWALL_EPISODE_RANGE[1], episodes)
        zone_has_reversal = any(
            str(s.get("narrative_beat", "")).strip() == "reversal"
            for ep in range(IAP_PAYWALL_EPISODE_RANGE[0], zone_end + 1)
            for s in by_episode.get(ep, [])
        )
        if not zone_has_reversal:
            issues.append(
                f"IAP 付费卡点缺失：第 {IAP_PAYWALL_EPISODE_RANGE[0]}-{zone_end} 集"
                "（首充卡点区间）须至少一集包含 reversal 反转并配合末镜 cliffhanger 引爆付费"
            )

    return issues

SYSTEM_PROMPT = """你是专业短剧编剧。根据用户的一句话创意，生成结构化 JSON 剧本。

输出 JSON 必须严格遵循以下格式：
{
  "title": "剧名",
  "synopsis": "一句话剧情简介（30-60字，含核心悬念）",
  "characters": [
    {
      "character_id": "char_001",
      "name": "角色名",
      "role": "主角/配角/反派",
      "age": 25,
      "description": "外貌特征详细描述（用于图像生成，含五官、发型、服装、气质）",
      "personality": "性格特征"
    }
  ],
  "scenes": [
    {
      "scene_id": 1,
      "episode": 1,
      "shot_type": "大特写/特写/近景/中景/全景/远景",
      "location": "场景地点（如：便利店内部/街道/卧室）",
      "description": "画面描述（中文，详细到可以画出来）",
      "prompt": "English positive prompt for image generation",
      "negative_prompt": "English negative prompt",
      "character_actions": "角色动作描述",
      "dialogue": "台词",
      "emotion": "tension/romantic/happy/sad/mysterious",
      "duration_seconds": 3,
      "camera_movement": "static/pan/tilt/zoom/dolly/tracking/handheld",
      "narrative_beat": "hook/escalation/reversal/cliffhanger/emotional_beat/transition"
    }
  ]
}

【爆款短剧结构公式 — 必须严格遵守】
A. 单集结构：每集总时长 50-70 秒，{scenes_per_episode} 个分镜，共 {episodes} 集
B. 每集叙事节拍（narrative_beat）必须按以下公式推进：
   1. 第 1 镜 = hook（强钩子）：0-3 秒内抛出危机/羞辱/误会/绝境，禁止平淡开场
      钩子类型枚举（择优选用）：死亡倒计时 / 身份揭露 / 打脸预告 / 反常画面 / 绝境冲突
   2. 前 15 秒内完成 escalation（冲突升级）：矛盾加码、对手施压
   3. 30 秒左右出现 reversal（反转）：预期违背、身份揭露、局势翻盘
   4. 每集最后 1 镜 = cliffhanger（悬念）：强制追更钩子（最后一集可为 emotional_beat 收尾）
   5. 其余镜头用 transition/emotional_beat 衔接
   6. 情绪点密度：任意连续 15 秒内必须出现至少一个强节拍（hook/escalation/reversal/cliffhanger/emotional_beat），
      禁止连续 15 秒以上全是 transition 过渡（观众流失红线）
C. 镜头时长规范（duration_seconds，必须为整数）：
   - hook 镜 1-3 秒；高潮快剪 1-2 秒；常规对话 2-3 秒
   - 情绪落点 3-6 秒；空镜/过渡 2-4 秒
   - 单镜最长不超过 8 秒
D. 景别纪律：
   - 以小景别为主（特写/近景/中景占 70% 以上），全景/远景仅用于交代环境
   - 任意连续 3 个镜头的 shot_type 不得完全相同
   - 高潮段近景+特写快切，铺垫段可中景慢推
E. 每镜只使用一种主运镜（camera_movement 白名单：static/pan/tilt/zoom/dolly/tracking/handheld）
F. 变现模式结构模板（{monetization_mode}）：
   - iaa（免费剧/红果模式）：每集末尾钩子驱动完播，重整体节奏密度与每集 cliffhanger
   - iap（付费剧）：前 3 集免费引流节奏更快；第 8-12 集为首充卡点区间，
     该区间至少一集须安排全剧最强 reversal 反转 + 末镜 cliffhanger 组合引爆付费；
     第 26-30 集（如集数足够）设大额充值引爆点

【通用要求】
1. 角色描述要详细到可以生成定妆照（五官、发型、服装、气质）
2. 画面描述要具体（场景、光线、构图、人物状态）
3. **英文 prompt 必须完整翻译 description 中的核心视觉元素**，包含：
   {style_clause}
   - 镜头语言：shot_type 对应的英文（extreme close-up/close-up/medium shot/full shot/wide shot）
   - 场景细节：location、关键道具、光线氛围（如 dim yellow lighting, neon signs）
   - 角色外观：出场角色的核心特征（发色、服装、表情），与 characters.description 一致
   - 角色动作：character_actions 的英文翻译
   - 情绪氛围：emotion 对应的英文氛围词（eerie/tense/romantic）
   - 彩色风格：默认生成彩色画面，禁止黑白（除非剧情明确要求）
   - 反面示例（禁止）："cinematic 8k detailed store interior" （过于简略，丢失核心剧情元素）
   - 正面示例："cinematic medium shot, late-night convenience store interior, dim yellow fluorescent lighting, young Chinese female clerk with shoulder-length black wavy hair in white t-shirt and denim jacket standing behind checkout counter, staring at surveillance monitor showing her own doppelganger waving back, eerie tension, 8k, highly detailed"
4. 台词要口语化、有张力，单条不超过 30 字
5. 竖屏 9:16 构图思维：人物特写优先，重要信息放画面中下部
6. JSON 字符串值中的双引号必须用 \\" 转义，不要使用未转义的英文双引号
7. 如果需要引用文字，请用中文引号「」或单引号
8. negative_prompt 默认包含：{style_negative_terms}
9. synopsis 必填，30-60字概括全剧核心悬念

直接输出纯 JSON，不要用 markdown 代码块包裹，不要输出任何解释性文字。
"""


class ScriptAgent(BaseAgent):
    """剧本 Agent：spark/qwen 生成结构化剧本（默认关闭 thinking）。"""

    def __init__(self):
        super().__init__("script_agent")

    async def execute(self, request: ScriptRequest) -> AgentResponse:
        start = time.time()
        try:
            # AI 优化 step 1：联网搜索同题材参考资料（默认关闭，避免每次剧本多一轮外网）
            reference = ""
            if request.web_search or settings.script_web_search_enabled:
                search_query = f"短剧 {request.genre} {request.premise[:30]} 剧情设计 角色塑造"
                reference = await web_search(search_query, max_results=3)
                if reference:
                    logger.info("剧本 Agent 搜索到参考资料: %d 字符", len(reference))

            # M15.1 画风锚定：将用户画风解析为英文风格关键词，注入系统提示词，
            # 替代原硬编码 photorealistic（避免与角色定妆照/分镜关键帧画风脱节）
            # M16.1：风格词与外貌词权重分离（场景 prompt 是风格词下游传递的源头，
            # 必填收窄为风格名，KB 整串降可选，避免与场景内角色外貌描述争权重）
            anchor = resolve_style_anchor(request.style)
            realism_tail = ", photorealistic" if anchor.is_realistic else ""
            style_clause = style_prompt_clause(anchor, target="全剧画面") + (
                f"\n   - 画质关键词：cinematic, 8k, highly detailed{realism_tail}"
            )
            conflict_neg = anchor.negative_en or (
                "cartoon, anime" if anchor.is_realistic else "realistic, photorealistic, live action"
            )
            style_negative_terms = (
                "black and white, monochrome, blurry, low quality, deformed, "
                f"{conflict_neg}, text, watermark, extra fingers, bad anatomy"
            )
            system = (
                SYSTEM_PROMPT
                .replace("{episodes}", str(request.episodes))
                .replace("{scenes_per_episode}", str(request.scenes_per_episode))
                .replace("{monetization_mode}", request.monetization_mode)
                .replace("{style_clause}", style_clause)
                .replace("{style_negative_terms}", style_negative_terms)
            )
            user_msg = (
                f"创意：{request.premise}\n"
                f"题材：{request.genre}\n"
                f"画风：{request.style}\n"
                f"集数：{request.episodes}\n"
                f"每集分镜数：{request.scenes_per_episode}\n"
                f"变现模式：{request.monetization_mode}\n"
                f"画幅：9:16（竖屏短剧）\n"
            )
            if reference:
                user_msg += f"\n参考资料（联网搜索，供借鉴剧情节奏和角色设计手法）：\n{reference}\n"
            user_msg += "\n请生成完整的 JSON 剧本。"

            content = await self.call_llm(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                model=settings.exo_model_glm52,
                temperature=0.85,
                max_tokens=16000,
                response_format_json=True,
                disable_thinking=True,
            )

            script_data = self._parse_llm_json(content)

            if not isinstance(script_data, dict):
                logger.error("剧本 LLM 返回无法解析为 JSON 对象，原始内容前 1000 字：%s", content[:1000])
                raise RuntimeError(f"剧本 LLM 返回格式异常: {type(script_data)}")

            raw_chars = script_data.get("characters", [])
            clean_chars = [
                c for c in raw_chars
                if isinstance(c, dict) and c.get("character_id") and c.get("name")
            ]

            raw_scenes = script_data.get("scenes", [])
            clean_scenes = self._clean_scenes(raw_scenes)

            # 结构校验 + LLM 自动返修闭环（短剧结构公式：钩子/反转/悬念/景别/时长/情绪密度/付费卡点）
            issues = validate_script_scenes(
                clean_scenes, request.episodes, request.scenes_per_episode, request.monetization_mode
            )
            for attempt in range(1, MAX_REPAIR_ATTEMPTS + 1):
                if not issues:
                    break
                logger.info(
                    "剧本结构校验发现 %d 个问题，第 %d/%d 轮返修，示例: %s",
                    len(issues), attempt, MAX_REPAIR_ATTEMPTS, issues[:3],
                )
                repaired = await self._repair_scenes(clean_scenes, issues, request)
                if repaired is None:
                    break
                clean_scenes = repaired
                issues = validate_script_scenes(
                    clean_scenes, request.episodes, request.scenes_per_episode, request.monetization_mode
                )
            if issues:
                logger.warning("剧本结构校验未完全通过（%d 个问题），放行: %s", len(issues), issues)

            # RAG 增强：基于知识库优化每个场景的生成提示词（M15.1：style_hint 用画风而非题材）
            if settings.rag_optimize_enabled:
                await self._rag_enhance_scenes(clean_scenes, request.style)

            # M15.4 画风冲突清洗：LLM/RAG 产出的场景提示词常自带与目标画风互斥的
            # 风格词（如请求国漫却写 hyperrealistic、负面词反向排斥 anime）。
            # 在源头清洗正文；风格锚定尾仍由下游（分镜/视频）各自追加，避免重复。
            for scene in clean_scenes:
                if scene.get("prompt"):
                    scene["prompt"] = sanitize_style_conflicts(scene["prompt"], anchor)
                if scene.get("negative_prompt"):
                    scene["negative_prompt"] = sanitize_style_conflicts(
                        scene["negative_prompt"], anchor, negative=True
                    )

            script = Script(
                project_id=str(uuid.uuid4()),
                title=script_data.get("title", "未命名"),
                genre=request.genre,
                aspect_ratio="9:16",
                total_episodes=request.episodes,
                characters=clean_chars,
                scenes=clean_scenes,
            )

            return AgentResponse(
                success=True,
                data=script.model_dump(),
                elapsed_seconds=time.time() - start,
            )
        except Exception as e:
            return AgentResponse(
                success=False,
                error=f"剧本生成失败: {e}",
                elapsed_seconds=time.time() - start,
            )

    @staticmethod
    def _clean_scenes(raw_scenes: list[Any]) -> list[dict[str, Any]]:
        """清洗 LLM 返回的分镜列表：补 scene_id、丢无效项、归一化术语。"""
        clean: list[dict[str, Any]] = []
        for i, s in enumerate(raw_scenes):
            if not isinstance(s, dict):
                continue
            if "scene_id" not in s:
                s["scene_id"] = i + 1
            if "description" not in s:
                continue
            _normalize_scene_terms(s)
            clean.append(s)
        return clean

    async def _repair_scenes(
        self,
        scenes: list[dict[str, Any]],
        issues: list[str],
        request: ScriptRequest,
    ) -> list[dict[str, Any]] | None:
        """把结构校验问题回灌 LLM 返修，返回修复后的分镜列表；失败返回 None。"""
        fix_msg = (
            "以下短剧分镜未通过结构校验，请修复后重新输出。\n\n"
            "【校验问题】\n" + "\n".join(f"- {i}" for i in issues) + "\n\n"
            f"【结构要求】每集 {request.scenes_per_episode} 个分镜、每集总时长 50-70 秒；"
            "每集首镜 narrative_beat=hook（强钩子，时长 1-3s），末镜=cliffhanger（最后一集可为 emotional_beat）；"
            "景别白名单：大特写/特写/近景/中景/全景/远景，任意连续 3 镜景别不得相同；"
            "运镜白名单：static/pan/tilt/zoom/dolly/tracking/handheld；"
            "单镜时长为 1-8 的整数秒；单条台词不超过 30 字；"
            "任意连续 15 秒内须至少一个强节拍（禁止连续 15s+ 全 transition）；"
            f"变现模式 {request.monetization_mode}（iap 时第 8-12 集须至少一集含 reversal 付费卡点）。\n\n"
            "【当前分镜 JSON】\n" + json.dumps(scenes, ensure_ascii=False) + "\n\n"
            "请输出修复后的完整 JSON：{\"scenes\": [...]}，尽量保持 description/prompt/dialogue 等创作内容不变，"
            "只修复校验问题涉及的字段。直接输出纯 JSON，不要 markdown 代码块。"
        )
        try:
            content = await self.call_llm(
                messages=[{"role": "user", "content": fix_msg}],
                model=settings.exo_model_glm52,
                temperature=0.3,
                max_tokens=16000,
                response_format_json=True,
                disable_thinking=True,
            )
            data = self._parse_llm_json(content)
            if not isinstance(data, dict) or not isinstance(data.get("scenes"), list):
                logger.warning("剧本返修返回结构异常: %s", type(data))
                return None
            return self._clean_scenes(data["scenes"])
        except Exception as e:
            logger.warning("剧本返修 LLM 调用失败: %s", e)
            return None

    @staticmethod
    def _parse_llm_json(content: str) -> Any:
        """多层容错解析 LLM 返回的 JSON。

        1. 先尝试标准 json.loads
        2. 失败则用 json_repair 修复
        3. 若 json_repair 返回字符串（双重转义 / 含多余文本），尝试二次解析
        4. 仍失败时从文本中提取 { ... } / [ ... ] 片段再解析
        """
        if not content or not content.strip():
            return None

        cleaned = content.strip()

        # 1) 标准解析
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 2) json_repair 修复
        try:
            parsed = json_repair.loads(cleaned)
        except Exception as e:
            logger.warning("json_repair 解析失败: %s", e)
            parsed = None

        # 3) 处理双重转义字符串
        if isinstance(parsed, str):
            second = parsed.strip()
            try:
                return json.loads(second)
            except json.JSONDecodeError:
                pass
            try:
                return json_repair.loads(second)
            except Exception as e:
                logger.warning("二次 json_repair 解析失败: %s", e)
                parsed = second

        # 4) 如果还是字符串，尝试从文本中截取 JSON 片段
        if isinstance(parsed, str):
            # 找第一个 { 或 [ 到最后一个 } 或 ]
            start_idx = -1
            for ch in ("{", "["):
                idx = parsed.find(ch)
                if idx != -1 and (start_idx == -1 or idx < start_idx):
                    start_idx = idx
            end_idx = -1
            for ch in ("}", "]"):
                idx = parsed.rfind(ch)
                if idx != -1 and idx > end_idx:
                    end_idx = idx
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                snippet = parsed[start_idx : end_idx + 1]
                try:
                    return json.loads(snippet)
                except json.JSONDecodeError:
                    try:
                        return json_repair.loads(snippet)
                    except Exception as e:
                        logger.warning("JSON 片段解析失败: %s", e)

        return parsed

    async def _rag_enhance_scenes(self, scenes: list[dict[str, Any]], genre: str) -> None:
        """使用 RAG 优化每个场景的正向/负向提示词。"""
        for scene in scenes:
            description = scene.get("description", "").strip()
            if not description:
                continue
            try:
                result = await rag_service.optimize_prompt(
                    user_prompt=description,
                    domain="video",
                    style_hint=genre or None,
                    extra_instruction="根据短剧场景描述生成高质量英文图像/视频生成提示词",
                )
                if result.get("optimized_positive"):
                    scene["prompt"] = result["optimized_positive"]
                if result.get("optimized_negative"):
                    scene["negative_prompt"] = result["optimized_negative"]
            except Exception as e:
                logger.warning("场景 %s RAG 优化失败，保留原提示词: %s", scene.get("scene_id"), e)


script_agent = ScriptAgent()
