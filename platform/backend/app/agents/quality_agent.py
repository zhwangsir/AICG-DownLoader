"""质检 Agent — 剧本/字幕/视频 → 结构化质检报告。

P4a 阶段实现文本质检（台词一致性、剧情逻辑、敏感词）。
P4b 阶段扩展视觉质检（视频抽帧 + VLM 检查）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import json_repair
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

from app.agents.base import BaseAgent, strip_think_tags
from app.agents.script_agent import validate_script_scenes
from app.config import settings
from app.models.schemas import (
    AgentResponse,
    QualityCheckItem,
    QualityCheckRequest,
    QualityCheckResult,
    QualityVisualItem,
    QualityVisualRequest,
    QualityVisualResult,
    SubtitleFixItem,
    SubtitleFixRequest,
    SubtitleFixResult,
    SubtitleResult,
    SubtitleSegment,
)


QUALITY_PROMPT_TEMPLATE = """你是一名短剧剧本质检专家。请对以下剧本和字幕进行质量检查，只输出 JSON。

检查维度：
1. consistency（角色一致性）：角色姓名、年龄、性格、关系是否前后一致。
2. logic（剧情逻辑）：场景转换、时间线、因果是否合理。
3. sensitive（敏感内容）：是否涉及暴力、色情、政治敏感、歧视等违规内容。
4. subtitle（字幕质量）：字幕与台词是否匹配，是否存在错别字或时间戳异常。

严重级别定义：
- info：轻微建议
- warning：可能影响观感
- critical：必须修改

输入剧本：
标题：{title}
角色：{characters}
场景：{scenes}
字幕：{subtitles}
要求检查类型：{check_types}

请输出严格 JSON：
{{
  "score": 0-100 的综合质量分,
  "summary": "整体评价，50字以内",
  "issues": [
    {{
      "category": "consistency|logic|sensitive|subtitle",
      "severity": "info|warning|critical",
      "scene_id": 可选的场景ID或null,
      "message": "问题描述",
      "suggestion": "修改建议"
    }}
  ]
}}

只输出 JSON，不要 markdown 代码块，不要解释。
"""


class QualityAgent(BaseAgent):
    """质检 Agent：文本质检（P4a）。"""

    def __init__(self):
        super().__init__("quality_agent")

    async def execute(self, request: QualityCheckRequest) -> AgentResponse:
        start = time.time()
        try:
            prompt = QUALITY_PROMPT_TEMPLATE.format(
                title=request.title or "未命名短剧",
                characters=self._serialize_characters(request.characters),
                scenes=self._serialize_scenes(request.scenes),
                subtitles=self._serialize_subtitles(request.subtitles),
                check_types=", ".join(request.check_types),
            )

            # GLM-5.2 思考模式下 response_format={"type":"json_object"} 会导致无限等待，
            # 必须移除该约束，仅靠提示词约束输出格式。
            # 同时设置 300s 超时，超时后降级为基于规则的质检。
            try:
                raw = await asyncio.wait_for(
                    self.call_llm(
                        messages=[{"role": "user", "content": prompt}],
                        response_format_json=False,
                        temperature=0.3,
                        max_tokens=8000,
                    ),
                    timeout=480.0,
                )
            except asyncio.TimeoutError:
                result = self._fallback_check(request)
                result.summary = f"LLM 质检超时，已降级为规则质检。{result.summary}"
                return AgentResponse(
                    success=True,
                    data=result.model_dump(),
                    elapsed_seconds=time.time() - start,
                )

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = json_repair.loads(raw)
            # json_repair 对纯文本输入可能返回 str / list, 此时应触发 JSON 解析失败分支
            if not isinstance(data, dict):
                raise json.JSONDecodeError(
                    f"质检结果非 JSON 对象: {type(data).__name__}",
                    raw,
                    0,
                )
            issues = self._parse_issues(data.get("issues", []))
            # 并入确定性规则检查（剧本结构 + 高风险镜头），LLM 可能漏检
            issues.extend(self._structure_issues(request))
            issues.extend(self._high_risk_scene_issues(request))

            result = QualityCheckResult(
                project_id=request.project_id,
                title=request.title,
                score=int(data.get("score", 80)),
                summary=data.get("summary", ""),
                issues=issues,
            )

            return AgentResponse(
                success=True,
                data=result.model_dump(),
                elapsed_seconds=time.time() - start,
            )
        except json.JSONDecodeError as e:
            return AgentResponse(
                success=False,
                error=f"质检结果 JSON 解析失败: {e}",
                elapsed_seconds=time.time() - start,
            )
        except Exception as e:
            return AgentResponse(
                success=False,
                error=f"质检失败: {e}",
                elapsed_seconds=time.time() - start,
            )

    def _parse_issues(self, raw_issues: list[Any]) -> list[QualityCheckItem]:
        """容错解析 issues 列表：LLM 可能返回字符串数组而非对象数组。"""
        items: list[QualityCheckItem] = []
        for item in raw_issues:
            if isinstance(item, str):
                items.append(QualityCheckItem(
                    category="logic",
                    severity="warning",
                    message=item,
                    suggestion="",
                ))
                continue
            if not isinstance(item, dict):
                continue
            scene_id = item.get("scene_id")
            if isinstance(scene_id, str):
                match = re.search(r"\d+", scene_id)
                scene_id = int(match.group()) if match else None
            try:
                items.append(QualityCheckItem(
                    category=item.get("category", "logic"),
                    severity=item.get("severity", "info"),
                    scene_id=scene_id,
                    message=item.get("message", ""),
                    suggestion=item.get("suggestion", ""),
                ))
            except Exception:
                continue
        return items

    def _structure_issues(self, request: QualityCheckRequest) -> list[QualityCheckItem]:
        """确定性剧本结构检查：复用剧本层 validate_script_scenes 校验器。

        scenes_per_episode 取各集实际分镜数的最大值（数量校验在剧本生成阶段已做，
        此处聚焦 hook/cliffhanger/时长/景别/情绪密度等质量维度）。
        """
        if not request.scenes:
            return []
        scene_dicts = [s.model_dump() for s in request.scenes]
        episodes = max(int(s.get("episode", 1)) for s in scene_dicts)
        counts: dict[int, int] = {}
        for s in scene_dicts:
            ep = int(s.get("episode", 1))
            counts[ep] = counts.get(ep, 0) + 1
        raw_issues = validate_script_scenes(scene_dicts, episodes, max(counts.values()))
        return [
            QualityCheckItem(
                category="structure",
                severity="warning",
                message=f"剧本结构问题: {msg}",
                suggestion="回到剧本节点修正分镜结构（可重新生成触发自动返修）",
            )
            for msg in raw_issues
        ]

    def _high_risk_scene_issues(self, request: QualityCheckRequest) -> list[QualityCheckItem]:
        """高风险镜头自动打标（AI 生成崩坏高发场景，建议人工复核）：

        1. 多人同框（>5 名角色或人群关键词）：特征"串味"、六指穿模高发
        2. 极端视角（大特写+手持 / 仰拍 / 俯拍 / 背面）：错误率显著高于常规镜头
        3. 小配角跨集召回（出场 ≤3 镜但跨集）：跨集服饰一致性崩坏率高
        """
        issues: list[QualityCheckItem] = []
        if not request.scenes:
            return issues

        crowd_keywords = ("人群", "众人", "围观", "群演", "人群之中")
        char_names = [c.name for c in request.characters if c.name]
        # 统计每个角色出场的集数与镜头数（用于小配角跨集召回判断）
        char_episodes: dict[str, set[int]] = {n: set() for n in char_names}
        char_scene_count: dict[str, int] = {n: 0 for n in char_names}

        for s in request.scenes:
            mentioned = [n for n in char_names if n in s.description or n in s.character_actions]
            for n in mentioned:
                char_episodes[n].add(s.episode)
                char_scene_count[n] += 1

            risk_reasons: list[str] = []
            if len(mentioned) > 5 or any(k in s.description for k in crowd_keywords):
                risk_reasons.append("多人同框（角色特征串味/手部穿模高发）")
            extreme_keywords = ("仰拍", "俯拍", "鸟瞰", "背面转正", "大角度")
            if (s.shot_type == "大特写" and s.camera_movement == "handheld") or any(
                k in s.description for k in extreme_keywords
            ):
                risk_reasons.append("极端视角（跨镜一致性错误率高）")
            if risk_reasons:
                issues.append(QualityCheckItem(
                    category="visual_risk",
                    severity="warning",
                    scene_id=s.scene_id,
                    message=f"高风险镜头: {'；'.join(risk_reasons)}",
                    suggestion="建议人工复核该镜头关键帧与视频，必要时提高生成质量档位或重抽",
                ))

        for name, eps in char_episodes.items():
            if len(eps) >= 2 and char_scene_count[name] <= 3:
                issues.append(QualityCheckItem(
                    category="visual_risk",
                    severity="info",
                    message=f"小配角跨集召回: 角色「{name}」仅出场 {char_scene_count[name]} 镜但跨 {len(eps)} 集，服饰一致性崩坏率高",
                    suggestion="建议在角色资产库中锁定该角色服装细节卡，跨集生成时强制引用",
                ))
        issues.extend(self._multishot_group_issues(request.scenes))
        return issues

    def _multishot_group_issues(self, scenes) -> list[QualityCheckItem]:
        """M12.3 多镜联合生成组自动标注（H3 独有失败模式：跨镜角色漂移）。

        与 video_agent.group_scenes_for_multishot 同规则（同集相邻、场景数/总时长
        双上限）模拟分组；≥2 场景成组时逐场景打 info 标，提示人工抽查组内首尾帧
        角色一致性（一次推理生成多镜，漂移无法靠单镜重抽修复）。
        """
        if settings.video_backend.lower() != "h3" or not settings.h3_multishot_enabled:
            return []
        max_scenes = settings.h3_multishot_max_scenes
        max_seconds = settings.h3_multishot_max_seconds

        groups: list[list] = []
        current: list = []
        current_seconds = 0.0
        current_episode: int | None = None
        for s in scenes:
            duration = float(s.duration_seconds)
            if (
                current
                and s.episode == current_episode
                and len(current) < max_scenes
                and current_seconds + duration <= max_seconds
            ):
                current.append(s)
                current_seconds += duration
            else:
                if current:
                    groups.append(current)
                current = [s]
                current_seconds = duration
                current_episode = s.episode
        if current:
            groups.append(current)

        return [
            QualityCheckItem(
                category="visual_risk",
                severity="info",
                scene_id=s.scene_id,
                message=f"多镜联合生成组（{len(group)} 镜一次推理）: 跨镜角色漂移风险",
                suggestion="建议抽查该组首尾帧角色一致性；漂移时整组重抽或改逐场景生成（h3_multishot_enabled=False）",
            )
            for group in groups
            if len(group) >= 2
            for s in group
        ]

    def _fallback_check(self, request: QualityCheckRequest) -> QualityCheckResult:
        """LLM 超时时的降级规则质检：字幕错别字 + 敏感词基础检测 + 结构/高风险镜头。"""
        issues: list[QualityCheckItem] = []
        # 字幕错别字检测（字幕文本与台词对比）
        for sub in request.subtitles:
            for seg in sub.segments:
                # 检测常见错别字模式（同音字替换等）
                if any(w in seg.text for w in ["錄", "叶", "他她", "她他"]):
                    issues.append(QualityCheckItem(
                        category="subtitle",
                        severity="warning",
                        scene_id=sub.scene_id,
                        message=f"字幕疑似存在错别字: {seg.text[:50]}",
                        suggestion="校对字幕中的同音字错误",
                    ))
        # 敏感词基础检测
        sensitive_words = ["暴力", "色情", "毒品", "赌博"]
        for scene in request.scenes:
            combined = f"{scene.description} {scene.dialogue}"
            for word in sensitive_words:
                if word in combined:
                    issues.append(QualityCheckItem(
                        category="sensitive",
                        severity="warning",
                        scene_id=scene.scene_id,
                        message=f"场景中检测到敏感词: {word}",
                        suggestion="确认是否需要修改相关内容",
                    ))
        # 确定性规则检查：剧本结构 + 高风险镜头
        issues.extend(self._structure_issues(request))
        issues.extend(self._high_risk_scene_issues(request))
        score = 85 if not issues else 70
        return QualityCheckResult(
            project_id=request.project_id,
            title=request.title,
            score=score,
            summary="基于规则的降级质检完成" if not issues else f"发现 {len(issues)} 个问题",
            issues=issues,
        )

    def _serialize_characters(self, characters: list[Any]) -> str:
        if not characters:
            return "无"
        lines = []
        for c in characters:
            lines.append(
                f"- {c.name}（{c.role}，{c.age or '?'}岁）: {c.description}"
            )
        return "\n".join(lines)

    def _serialize_scenes(self, scenes: list[Any]) -> str:
        if not scenes:
            return "无"
        lines = []
        for s in scenes:
            lines.append(
                f"场景 {s.scene_id} [{s.shot_type}]: {s.description}\n"
                f"  台词: {s.dialogue}\n"
                f"  情绪: {s.emotion}, 时长: {s.duration_seconds}s"
            )
        return "\n".join(lines)

    def _serialize_subtitles(self, subtitles: list[Any]) -> str:
        if not subtitles:
            return "无"
        lines = []
        for st in subtitles:
            preview = " / ".join(seg.text for seg in st.segments[:3])
            lines.append(f"场景 {st.scene_id}: {preview}")
        return "\n".join(lines)


# === 字幕回写修正（P1-2 字幕闭环） ===

# 匹配 'X'应为'Y' / "X"应为"Y" / 「X」应为「Y」 / X→Y / X->Y
# 限制长度 1-20 字符，避免贪婪匹配整句
_CORRECTION_PATTERNS = [
    re.compile(r"['’“\"]([^'’\"”\n]{1,20}?)['’”\"]\s*应为\s*['’“\"]([^'’\"”\n]{1,20}?)['’”\"]"),
    # 箭头模式：→ / -> 排除出捕获组，避免吞入分隔符；贪婪匹配到下一个分隔符
    re.compile(r"([^\s,，。;；:：\n()（）→]{1,20})\s*(?:→|->)\s*([^\s,，。;；:：\n()（）→]{1,20})"),
]


def _extract_subtitle_corrections(
    issues: list[QualityCheckItem],
) -> list[tuple[str, str]]:
    """从质检 issues 中提取字幕修正对 (wrong, right)。

    解析 message / suggestion 中的 'X'应为'Y' 与 X→Y 模式。
    仅处理 category=subtitle 的问题，保证不会误改非字幕内容。
    """
    corrections: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        if issue.category != "subtitle":
            continue
        for text in (issue.message, issue.suggestion):
            for pattern in _CORRECTION_PATTERNS:
                for m in pattern.finditer(text):
                    wrong = m.group(1).strip("'’”“\"「」")
                    right = m.group(2).strip("'’”“\"「」")
                    # 过滤单字以下、相同值、明显噪声
                    if len(wrong) < 1 or len(right) < 1:
                        continue
                    if wrong == right:
                        continue
                    if (wrong, right) in seen:
                        continue
                    seen.add((wrong, right))
                    corrections.append((wrong, right))
    return corrections


def _format_srt_timestamp(seconds: float) -> str:
    """SRT 时间戳 HH:MM:SS,mmm。"""
    if seconds < 0:
        seconds = 0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _build_srt_from_segments(segments: list[SubtitleSegment]) -> str:
    """由 SubtitleSegment 列表重建 SRT 文本。

    跳过空文本段，并对保留段从 1 开始连续编号（符合 SRT 规范）。
    """
    lines = []
    idx = 0
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        idx += 1
        lines.append(str(idx))
        lines.append(f"{_format_srt_timestamp(seg.start)} --> {_format_srt_timestamp(seg.end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).strip()


def apply_subtitle_fixes(request: SubtitleFixRequest) -> SubtitleFixResult:
    """基于质检 issues 自动修正字幕 ASR 错别字，可选回写 SRT 文件。

    闭环逻辑：
    1. 从 issues 提取 (wrong→right) 修正对（仅 category=subtitle）
    2. 对每段字幕文本执行字符串替换
    3. 重建 SRT 文本
    4. 若 persist=True，覆盖写入 output/subtitle/subtitle_scene_{id}.srt
    """
    corrections = _extract_subtitle_corrections(request.issues)
    # 同时把修正对回填为 [{wrong, right}] 供前端展示
    corrections_dicts = [{"wrong": w, "right": r} for w, r in corrections]

    fixed_subtitles: list[SubtitleResult] = []
    details: list[SubtitleFixItem] = []
    fixed_count = 0

    for sub in request.subtitles:
        new_segments: list[SubtitleSegment] = []
        sub_details: list[SubtitleFixItem] = []
        seg_fixed = False

        for seg in sub.segments:
            new_text = seg.text
            applied: list[dict[str, str]] = []
            for wrong, right in corrections:
                if wrong in new_text:
                    new_text = new_text.replace(wrong, right)
                    applied.append({"wrong": wrong, "right": right})
            if new_text != seg.text:
                seg_fixed = True
                sub_details.append(SubtitleFixItem(
                    scene_id=sub.scene_id,
                    original_text=seg.text,
                    fixed_text=new_text,
                    applied=applied,
                ))
            new_segments.append(SubtitleSegment(start=seg.start, end=seg.end, text=new_text))

        if seg_fixed:
            fixed_count += 1

        new_srt = _build_srt_from_segments(new_segments)
        fixed_sub = SubtitleResult(
            scene_id=sub.scene_id,
            srt_content=new_srt,
            segments=new_segments,
            language=sub.language,
        )
        fixed_subtitles.append(fixed_sub)
        details.extend(sub_details)

    # 回写 SRT 文件
    persisted_files: list[str] = []
    if request.persist:
        output_dir = Path(__file__).resolve().parent.parent.parent / "output" / "subtitle"
        output_dir.mkdir(parents=True, exist_ok=True)
        for sub in fixed_subtitles:
            srt_path = output_dir / f"subtitle_scene_{sub.scene_id}.srt"
            try:
                srt_path.write_text(sub.srt_content, encoding="utf-8")
                persisted_files.append(str(srt_path))
            except OSError as e:
                logger.warning("回写 SRT 失败 scene=%s: %s", sub.scene_id, e)

    return SubtitleFixResult(
        fixed_subtitles=fixed_subtitles,
        corrections=corrections_dicts,
        fixed_count=fixed_count,
        details=details,
        persisted_files=persisted_files,
    )


VISUAL_QUALITY_PROMPT_TEMPLATE = """你是一名短剧视频质检专家。以下是从一个短视频片段中均匀抽取的几帧画面，请检查视频质量，只输出 JSON。

检查维度：
1. visual_consistency（角色一致性）：同一角色在不同帧中是否长相、服装、妆容一致。
2. coherence（画面连贯性）：帧与帧之间动作、场景、光线是否自然连贯，是否存在跳变/闪烁。
3. anomaly（异常画面）：是否存在变形、模糊、水印、伪影、肢体扭曲等异常。
4. subtitle（字幕质量）：若画面中有字幕，是否清晰、无错别字、与画面协调。

严重级别定义：
- info：轻微建议
- warning：可能影响观感
- critical：必须修改

视频标题：{title}
检查类型：{check_types}

请输出严格 JSON：
{{
  "score": 0-100 的综合视觉质量分,
  "summary": "整体评价，50字以内",
  "issues": [
    {{
      "category": "visual_consistency|coherence|anomaly|subtitle",
      "severity": "info|warning|critical",
      "timestamp": 问题大致发生的时间戳（秒）或 null,
      "message": "问题描述",
      "suggestion": "修改建议"
    }}
  ]
}}

只输出 JSON，不要 markdown 代码块，不要解释。
"""

# M13 角色一致性对照专用 prompt（独立第二次 VLM 调用，与主画质检查分离）。
# 实测教训：漂移指令拼接在长画质 prompt 末尾会被"画质检查"心智框架稀释，
# 模型直接忽略（极端换人也漏报）；独立简单 prompt 裸测 100% 命中。
DRIFT_CHECK_PROMPT = """前 {ref_count} 张图是角色定妆参考图（三视图：正面/侧面/背面），最后 1 张是视频抽帧。
请判断视频帧中的角色是否与参考图为同一角色。判定规则：
- 第一步先判断参考角色是否在帧中出镜：帧为 POV 主观镜头/空镜/仅道具特写，或帧中仅出现与参考角色明显不同的背景路人 → character_present 填 false，此种情况一律不算漂移；
- 性别/年龄段/画风（卡通 vs 写实）/人种明显不同，或明显不是同一人 → 判定漂移；
- 确认是同一角色后，不同视角（正面/侧面/背面）之间的外观差异（如背面看不到面部）→ 不算漂移；
- 帧模糊、角色占比过小或被遮挡到无法辨认 → 不算漂移，details 填写"无法辨认"；
- 同视角下发型、服装款式、妆容与参考图明显不同 → 判定漂移。

输出要求：只输出一个 JSON 对象，包含三个字段：
- character_present：布尔值，参考角色是否在帧中出镜（无法辨认视为出镜；仅当帧中完全没有参考角色时填 false）；
- drift_detected：布尔值，判定漂移为 true，否则为 false（character_present 为 false 时必须填 false）；
- details：字符串。判定漂移时，必须根据图片实际内容描述具体差异（如"服装颜色不同：帧为红色T恤，参考图为蓝色T恤"）；参考角色未出镜时填写"参考角色未出镜"；无漂移时填空字符串。
严禁照抄本说明中的示例文字。

不要 markdown 代码块，不要解释。"""


class VisualQualityAgent(BaseAgent):
    """视觉质检 Agent：视频 → 抽帧 → VLM 检查。"""

    def __init__(self):
        super().__init__("visual_quality_agent")
        self._vlm_client: AsyncOpenAI | None = None

    def _get_vlm_client(self) -> AsyncOpenAI:
        if self._vlm_client is None:
            self._vlm_client = AsyncOpenAI(
                base_url=settings.visual_model_url,
                api_key="not-needed",
                http_client=self.http,
            )
        return self._vlm_client

    async def execute(self, request: QualityVisualRequest) -> AgentResponse:
        start = time.time()
        try:
            # 未配置视觉模型时降级为提示信息
            if not settings.visual_model_url:
                result = QualityVisualResult(
                    project_id=request.project_id,
                    title=request.title,
                    scene_id=request.scene_id,
                    score=0,
                    summary="视觉模型未部署（Qwen3-VL 未就绪），未进行视觉质检。请在配置中设置 visual_model_url 后重试。",
                    issues=[
                        QualityVisualItem(
                            category="system",
                            severity="info",
                            message="视觉质检模型未配置",
                            suggestion="部署 Qwen3-VL 并在 .env 中设置 VISUAL_MODEL_URL",
                        )
                    ],
                )
                return AgentResponse(
                    success=True,
                    data=result.model_dump(),
                    elapsed_seconds=time.time() - start,
                )

            # 1. 下载视频
            video_path = await self._download_video(request.video_url)

            # 2. 抽帧
            frames = await self._extract_frames(video_path, request.max_frames)

            # 3. M13 角色一致性对照：下载参考图（失败的单张跳过，不阻断主检查）
            ref_paths: list[Path] = []
            if request.reference_image_urls:
                downloaded = await asyncio.gather(
                    *(self._download_reference_image(u) for u in request.reference_image_urls)
                )
                ref_paths = [p for p in downloaded if p is not None]

            # 4. 主画质检查：仅视频帧（不混入参考图——实测参考图+长 prompt 会让
            # 漂移指令被画质检查框架稀释，极端换人都漏报；漂移走独立调用）
            prompt_text = VISUAL_QUALITY_PROMPT_TEMPLATE.format(
                title=request.title or "未命名视频",
                check_types=", ".join(request.check_types),
            )
            content: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]
            # M13 调优：detail 必须从 low 提到 high —— low 模式下 vLLM 把图缩到极小，
            # VLM 看不清直接编答案（实测幻觉出参考图不存在的"红色T恤"）
            for timestamp, frame_path in frames:
                encoded = base64.b64encode(frame_path.read_bytes()).decode("utf-8")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{encoded}", "detail": "high"},
                })

            # 5. 调用 VLM（使用独立的 VLM 客户端，指向 workstation Qwen3-VL 服务）
            # Nemotron 推理模型对多帧画面会产生超长思考链（>8000 token 仍被截断），
            # 通过 chat_template_kwargs.enable_thinking=False 关闭推理模式，
            # 直接输出 JSON（实测 8000 token 耗尽 → 81 token 完成）。
            client = self._get_vlm_client()
            resp = await client.chat.completions.create(
                model=settings.visual_model_name,
                messages=[{"role": "user", "content": content}],
                temperature=0.3,
                max_tokens=2000,
                stream=False,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            raw = resp.choices[0].message.content or ""
            if not raw:
                raw = getattr(resp.choices[0].message, "reasoning_content", "") or ""

            # 剥离推理标记（与 BaseAgent.call_llm 相同的容错逻辑，
            # 保留以兼容不支持 enable_thinking 的 VLM 服务）
            raw = strip_think_tags(raw)
            if raw.startswith("```"):
                lines = raw.split("\n")[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = json_repair.loads(raw)
            if not isinstance(data, dict):
                raise json.JSONDecodeError(
                    f"视觉质检结果非 JSON 对象: {type(data).__name__}",
                    raw,
                    0,
                )
            issues = [
                QualityVisualItem(**item)
                for item in data.get("issues", [])
                if isinstance(item, dict)
            ]
            summary = data.get("summary", "")

            # 6. M13 独立漂移对照：有参考图时单独调用 VLM（专用简单 prompt，
            # 实测命中率远高于拼接式）；检出漂移时追加 critical issue 并修订 summary
            drift_detected = False
            if ref_paths:
                drift_detected, drift_detail = await self._drift_check(ref_paths, frames)
                if drift_detected:
                    issues.append(QualityVisualItem(
                        category="visual_consistency",
                        severity="critical",
                        timestamp=None,
                        message=f"角色漂移：{drift_detail or '视频角色与参考图不一致'}",
                        suggestion="参考角色资产库三视图重抽该场景，或检查 ref 注入工作流",
                    ))
                    summary = f"检测到角色漂移（{drift_detail}）。" if drift_detail else "检测到角色漂移。"

            result = QualityVisualResult(
                project_id=request.project_id,
                title=request.title,
                scene_id=request.scene_id,
                score=int(data.get("score", 80)),
                summary=summary,
                issues=issues,
                drift_detected=drift_detected,
            )

            return AgentResponse(
                success=True,
                data=result.model_dump(),
                elapsed_seconds=time.time() - start,
            )
        except json.JSONDecodeError as e:
            return AgentResponse(
                success=False,
                error=f"视觉质检结果 JSON 解析失败: {e}",
                elapsed_seconds=time.time() - start,
            )
        except Exception as e:
            return AgentResponse(
                success=False,
                error=f"视觉质检失败: {e}",
                elapsed_seconds=time.time() - start,
            )

    async def _download_video(self, video_url: str) -> Path:
        """下载视频到临时目录，返回本地路径。"""
        from urllib.parse import urlparse

        parsed = urlparse(video_url)
        suffix = Path(parsed.path).suffix or ".mp4"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.close()
        dest = Path(tmp.name)

        # 本地静态资源直接复用
        if parsed.hostname in ("localhost", "127.0.0.1") and "/static/video/" in video_url:
            local_dir = Path(__file__).resolve().parent.parent.parent / "output" / "video"
            candidate = local_dir / Path(parsed.path).name
            if candidate.exists():
                return candidate

        async with self.http.stream("GET", video_url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
        return dest

    async def _drift_check(
        self, ref_paths: list[Path], frames: list[tuple[float, Path]]
    ) -> tuple[bool, str]:
        """M13 独立漂移对照：逐帧并发判定，任一帧漂移即整体漂移。

        实测教训：4+ 张图同时输入会让 Nemotron 产生跨图干扰幻觉（把卡通帧
        描述成"写实成年女性"）；逐帧调用（参考图 + 单帧）判定与描述均准确。
        返回 (drift_detected, details)。任何异常兜底 (False, "")，不阻断主检查。
        """
        try:
            results = await asyncio.gather(*(
                self._drift_check_single_frame(ref_paths, frame_path)
                for _timestamp, frame_path in frames
            ))
            drift_hits = [detail for is_drift, detail in results if is_drift]
            if drift_hits:
                # 聚合去重漂移细节（多帧可能报同一差异）
                uniq = list(dict.fromkeys(d.strip() for d in drift_hits if d.strip()))
                return True, "；".join(uniq[:3])
            return False, ""
        except Exception as e:  # noqa: BLE001 —— 漂移检测失败不阻断主画质检查
            logger.warning("漂移对照检测失败，按无漂移兜底: %s", e)
            return False, ""

    async def _drift_check_single_frame(
        self, ref_paths: list[Path], frame_path: Path
    ) -> tuple[bool, str]:
        """单帧漂移判定：参考图 + 1 帧，返回 (drift_detected, details)。"""
        try:
            content: list[dict[str, Any]] = [{
                "type": "text",
                "text": DRIFT_CHECK_PROMPT.format(ref_count=len(ref_paths)),
            }]
            for ref_path in ref_paths:
                encoded = base64.b64encode(ref_path.read_bytes()).decode("utf-8")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{encoded}", "detail": "high"},
                })
            encoded = base64.b64encode(frame_path.read_bytes()).decode("utf-8")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}", "detail": "high"},
            })

            client = self._get_vlm_client()
            resp = await client.chat.completions.create(
                model=settings.visual_model_name,
                messages=[{"role": "user", "content": content}],
                temperature=0.1,
                max_tokens=500,
                stream=False,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            raw = resp.choices[0].message.content or ""
            if not raw:
                raw = getattr(resp.choices[0].message, "reasoning_content", "") or ""
            raw = strip_think_tags(raw)
            if raw.startswith("```"):
                lines = raw.split("\n")[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = json_repair.loads(raw)
            if not isinstance(data, dict):
                return False, ""
            # M16.3 未出镜豁免：参考角色不在帧中（POV 主观镜头/空镜/仅背景路人），
            # 无论 drift_detected 为何均不算漂移 —— 程序兜底，防止 VLM 拿背景路人
            # 与参考图比对误判（core E2E pipeline-7470e3e104d9 scene 1 真实缺陷）
            if data.get("character_present") is False:
                logger.info("参考角色未出镜，漂移判定豁免: %s", data.get("details", ""))
                return False, ""
            return bool(data.get("drift_detected", False)), str(data.get("details", ""))
        except Exception as e:  # noqa: BLE001 —— 单帧失败按无漂移兜底，不拖垮整组
            logger.warning("单帧漂移判定失败，按无漂移兜底: %s", e)
            return False, ""

    async def _download_reference_image(self, url: str) -> Path | None:
        """下载角色定妆参考图，返回本地路径；失败返回 None（调用方跳过，不阻断主检查）。

        本地静态资源（/static/character/xxx.png）直接映射到 output 目录免下载。
        """
        from urllib.parse import urlparse

        url = (url or "").strip()
        if not url:
            return None
        try:
            parsed = urlparse(url)
            # 本地静态资源直接复用（角色定妆照存于 output/character/）
            if parsed.hostname in ("localhost", "127.0.0.1") and parsed.path.startswith("/static/"):
                parts = parsed.path.strip("/").split("/")
                if len(parts) >= 3:
                    local_dir = Path(__file__).resolve().parent.parent.parent / "output" / parts[1]
                    candidate = local_dir / parts[-1]
                    if candidate.exists():
                        return candidate

            suffix = Path(parsed.path).suffix or ".png"
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            tmp.close()
            dest = Path(tmp.name)
            async with self.http.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)
            return dest
        except Exception as e:
            logger.warning("参考图下载失败（跳过）: %s err=%s", url, e)
            return None

    async def _extract_frames(
        self,
        video_path: Path,
        max_frames: int,
    ) -> list[tuple[float, Path]]:
        """使用 FFmpeg 从视频中均匀抽取帧，返回 (时间戳, 帧路径) 列表。"""
        duration = await self._probe_duration(video_path)
        if duration <= 0:
            duration = 1.0

        frame_dir = Path(tempfile.mkdtemp(prefix="vq_frames_"))
        step = duration / (max_frames + 1)
        frames: list[tuple[float, Path]] = []

        for i in range(1, max_frames + 1):
            timestamp = i * step
            frame_path = frame_dir / f"frame_{i:03d}.jpg"
            cmd = [
                "ffmpeg",
                "-y",
                "-ss", str(timestamp),
                "-i", str(video_path),
                "-vframes", "1",
                "-q:v", "2",
                "-f", "image2",
                str(frame_path),
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if frame_path.exists():
                frames.append((timestamp, frame_path))

        if not frames:
            raise RuntimeError("未能从视频中抽取任何帧")
        return frames

    async def _probe_duration(self, video_path: Path) -> float:
        """使用 ffprobe 获取视频时长。"""
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        try:
            return float(stdout.decode().strip())
        except ValueError:
            return 0.0


visual_quality_agent = VisualQualityAgent()
quality_agent = QualityAgent()
