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

from app.agents.base import BaseAgent
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

    def _fallback_check(self, request: QualityCheckRequest) -> QualityCheckResult:
        """LLM 超时时的降级规则质检：字幕错别字 + 敏感词基础检测。"""
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

            # 3. 构建带图片的 VLM 消息
            content: list[dict[str, Any]] = [
                {"type": "text", "text": VISUAL_QUALITY_PROMPT_TEMPLATE.format(
                    title=request.title or "未命名视频",
                    check_types=", ".join(request.check_types),
                )}
            ]
            for timestamp, frame_path in frames:
                encoded = base64.b64encode(frame_path.read_bytes()).decode("utf-8")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{encoded}", "detail": "low"},
                })

            # 4. 调用 VLM（使用独立的 VLM 客户端，指向 workstation Qwen3-VL 服务）
            client = self._get_vlm_client()
            resp = await client.chat.completions.create(
                model=settings.visual_model_name,
                messages=[{"role": "user", "content": content}],
                temperature=0.3,
                max_tokens=2000,
                stream=False,
            )
            raw = resp.choices[0].message.content or ""

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
            result = QualityVisualResult(
                project_id=request.project_id,
                title=request.title,
                scene_id=request.scene_id,
                score=int(data.get("score", 80)),
                summary=data.get("summary", ""),
                issues=[
                    QualityVisualItem(**item)
                    for item in data.get("issues", [])
                    if isinstance(item, dict)
                ],
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
