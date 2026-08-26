"""M21.2 拉长验证实机测试 —— 4 块 × 14s = 56s 帧链 + 角色漂移累积观测。

对应 2026-08-10 长视频调研路线 A 的拉长验证环节：
- 配置 4 个连续测试块，每块 14s（H3 单块训练上限），总时长目标 56-58s
- 三项量化指标逐块采集：角色特征保持度 / 情感一致性 / 行为连贯性
  （由 DriftMetricsService 提供，VLM=spark02 qwen3.6-uncensored）
- 产出详细测试报告（JSON 原始数据 + Markdown 趋势/异常/建议）落盘 reports/

运行方式（workstation H3 :8195 + spark02 VLM 在线，约 40-60 分钟）：
    cd platform/backend
    .venv/bin/python -m pytest tests/integration/test_long_video_drift_56s.py -m slow --no-cov -s
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from app.agents.character_agent import CharacterAgent
from app.agents.video_agent import VideoAgent
from app.config import settings
from app.models.schemas import Character, CharacterRequest
from app.services.drift_metrics_service import DriftMetricsService, render_markdown_report
from app.services.long_video_service import LongVideoService, probe_video_duration

# 报告落盘：项目根 reports/M21.2/（测试报告为本里程碑交付物，需持久化）
REPORT_DIR = Path(__file__).resolve().parents[3] / "reports" / "M21.2"

CHUNK_SECONDS = 14
N_CHUNKS = 4
TARGET_MIN_S, TARGET_MAX_S = 56.0, 58.0

# 单一角色 + 情绪递进四幕（情感一致性观测基准：平静→警觉→惊惧→对峙）
CHUNK_PROMPTS = [
    (
        "Cinematic vertical shot, rainy neon street at night: a young woman with long "
        "straight black hair in a red trench coat stands still under a flickering sign, "
        "calm expression, rain falling softly, camera slowly pushes in, wet pavement reflections."
    ),
    (
        "She senses someone following her, glances back over her shoulder with alert "
        "tension in her eyes, neon light shifts across her face, rain intensifies, "
        "handheld camera subtle shake, same woman, same red trench coat."
    ),
    (
        "She breaks into a run down the narrow alley, fear on her face, hair and red "
        "coat flowing with motion, puddles splashing, camera tracking alongside, "
        "motion blur, same woman, continuous rainy night."
    ),
    (
        "She stops abruptly, turns to face her pursuer, breathing hard, resolute and "
        "defiant expression, dramatic rim light from neon sign behind her, camera "
        "slowly circles to her front, same woman, same red trench coat, rain easing."
    ),
]
# 情感观测基准（中文创作意图，供 VLM 情绪匹配打分）
CHUNK_INTENTS = [
    "平静：雨夜街头静立，氛围安静克制",
    "警觉：察觉被跟踪，紧张感上升",
    "惊惧：奔跑逃离，恐惧情绪高点",
    "对峙：停下转身面对，决绝无畏",
]

CHARACTER_PROMPT = (
    "photorealistic portrait of a young East Asian woman, long straight black hair, "
    "sharp eyebrows, amber eyes, wearing a red trench coat over black turtleneck, "
    "cinematic lighting, film photography, upper body front view, plain dark background"
)
CHARACTER_NEGATIVE = "blurry, extra limbs, watermark, text, lowres, deformed face"


@pytest.fixture
def real_settings(monkeypatch):
    """指向真实集群：H3 视频 + LB 图像 + spark02 VLM；开启长视频 14s×4。"""
    monkeypatch.setattr(settings, "video_backend", "h3")
    monkeypatch.setattr(settings, "h3_comfyui_url", "http://192.168.71.127:8195")
    monkeypatch.setattr(settings, "h3_result_timeout", 1800.0)
    monkeypatch.setattr(settings, "h3_multishot_enabled", False)
    monkeypatch.setattr(settings, "h3_turbo_enabled", False)  # 拉长验证走原生高质量路径
    monkeypatch.setattr(settings, "storyboard_keyframe_anchor_enabled", False)
    monkeypatch.setattr(settings, "long_video_enabled", True)
    monkeypatch.setattr(settings, "long_video_chunk_seconds", CHUNK_SECONDS)
    monkeypatch.setattr(settings, "long_video_max_chunks", N_CHUNKS)
    monkeypatch.setattr(settings, "image_backend", "sdxl")
    # 覆盖 conftest 全局占位地址。直连 workstation :8189 后端（而非 LB :8188）：
    # animagine 动漫 ckpt 全集群缺失（实测 2026-08-10），majicMIX 仅 workstation/pc01
    # 持有，LB 路由到 pc02 会 400；直连确定性最高，漂移验证不受路由随机性干扰
    monkeypatch.setattr(settings, "comfyui_image_hq", "http://192.168.71.127:8189")
    monkeypatch.setattr(settings, "comfyui_image_fast", "http://192.168.71.127:8189")
    monkeypatch.setattr(settings, "character_view_qc_enabled", False)  # 控时：本测试不做三视图 QC
    monkeypatch.setattr(settings, "visual_model_url", "http://192.168.71.84:8000/v1")
    monkeypatch.setattr(settings, "visual_model_name", "qwen3.6-uncensored")


async def _download(url: str, dest: Path) -> Path:
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return dest


@pytest.mark.slow
@pytest.mark.asyncio
async def test_long_video_56s_drift_observation(real_settings, tmp_path):
    """4×14s 帧链续写 + 三项漂移指标采集 + 详细测试报告。"""
    started = time.time()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Step 1: 角色定妆照（SDXL 自定义提示词，跳过 LLM/搜索，控时）
    # ------------------------------------------------------------------
    char_agent = CharacterAgent()
    char_resp = await char_agent.execute(CharacterRequest(
        character=Character(
            character_id="m21_drift_lin",
            name="林晚",
            role="主角",
            age=24,
            description="年轻女性，黑色长直发，琥珀色眼瞳，红色风衣。",
            personality="坚韧、警觉",
        ),
        style="写实电影感",
        custom_positive_prompt=CHARACTER_PROMPT,
        custom_negative_prompt=CHARACTER_NEGATIVE,
    ))
    assert char_resp.success, f"角色定妆照生成失败: {char_resp.error}"
    ref_urls: dict[str, str] = char_resp.data.get("reference_images", {})
    assert ref_urls.get("front"), f"缺少 front 视图: {char_resp.data}"

    ref_local: list[Path] = []
    for view in ("front", "side", "closeup"):
        url = ref_urls.get(view)
        if url:
            ref_local.append(await _download(url, tmp_path / f"ref_{view}.png"))
    first_frame_url = ref_urls["front"]

    # ------------------------------------------------------------------
    # Step 2: 4 块 × 14s 帧链续写（逐块透传定妆照 + 画风锚定）
    # ------------------------------------------------------------------
    service = LongVideoService(
        video_agent=VideoAgent(),
        worker_url=settings.h3_comfyui_url,
    )
    progress: list[tuple[int, str]] = []
    result = await service.generate(
        first_frame_url=first_frame_url,
        chunk_prompts=CHUNK_PROMPTS,
        negative_prompt=CHARACTER_NEGATIVE,
        reference_images=[u for u in ref_urls.values() if u],
        style="写实电影感",
        chunk_seconds=CHUNK_SECONDS,
        max_chunks=N_CHUNKS,
        work_dir=tmp_path / "longvideo",
        progress_callback=lambda p, m: progress.append((p, m)),
    )

    assert result.chunks_completed == N_CHUNKS
    # 时长硬性 sanity 区间（防断链/错拼），56-58 目标达成度写入报告
    assert 50.0 <= result.duration_seconds <= 62.0, (
        f"总时长 {result.duration_seconds:.2f}s 超出 sanity 区间"
    )
    in_target = TARGET_MIN_S <= result.duration_seconds <= TARGET_MAX_S

    # 逐块时长落账（验证每块 ≈14s）
    chunk_durations = [await probe_video_duration(p) for p in result.chunk_paths]

    # ------------------------------------------------------------------
    # Step 3: 漂移累积观测（三项量化指标 + 趋势 + 异常 + 建议）
    # ------------------------------------------------------------------
    drift = await DriftMetricsService().evaluate(
        result.chunk_paths,
        reference_image_paths=ref_local,
        chunk_intents=CHUNK_INTENTS,
        frames_per_chunk=3,
        work_dir=tmp_path / "drift",
    )

    # ------------------------------------------------------------------
    # Step 4: 报告落盘（JSON 原始数据 + Markdown 详细报告）
    # ------------------------------------------------------------------
    meta = {
        "测试时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        "测试块配置": f"{N_CHUNKS} 块 × {CHUNK_SECONDS}s",
        "总时长": f"{result.duration_seconds:.2f}s（目标 {TARGET_MIN_S:.0f}-{TARGET_MAX_S:.0f}s：{'达成' if in_target else '未达成'}）",
        "逐块时长": ", ".join(f"{d:.2f}s" for d in chunk_durations),
        "生成耗时": f"{result.elapsed_seconds:.0f}s（含帧链抽取上传）",
        "漂移评估耗时": f"{time.time() - started - result.elapsed_seconds:.0f}s",
        "视频后端": f"H3 原生路径（{settings.h3_comfyui_url}）",
        "VLM": f"{settings.visual_model_name} @ {settings.visual_model_url}",
        "角色": "林晚（黑长直/琥珀瞳/红色风衣，SDXL majicMIX 三视图锚定）",
        "画风": "写实电影感",
        "拼接产物": str(result.video_path),
    }
    ts = time.strftime("%Y%m%d_%H%M%S")
    # 产物持久化：pytest tmp 目录会被后续运行清理，视频/参考图必须复制到报告目录
    # （M22.2 路线 A MOS 评估复用 long_video.mp4 作路线A产物）
    artifact_dir = REPORT_DIR / f"artifacts_{ts}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copy2(result.video_path, artifact_dir / "long_video.mp4")
    for i, cp in enumerate(result.chunk_paths):
        shutil.copy2(cp, artifact_dir / f"chunk_{i:02d}.mp4")
    for rp in ref_local:
        shutil.copy2(rp, artifact_dir / rp.name)
    meta["拼接产物"] = str(artifact_dir / "long_video.mp4")

    json_path = REPORT_DIR / f"drift_56s_{ts}.json"
    json_path.write_text(json.dumps({
        "meta": meta,
        "chunk_durations": chunk_durations,
        "in_target_range": in_target,
        **drift.to_dict(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = REPORT_DIR / f"drift_56s_{ts}.md"
    md_path.write_text(render_markdown_report(drift, meta), encoding="utf-8")

    print(f"\n[M21.2] 总时长 {result.duration_seconds:.2f}s "
          f"({'56-58s 达成' if in_target else '目标区间外'})")
    print(f"[M21.2] 逐块时长: {[f'{d:.2f}' for d in chunk_durations]}")
    print(f"[M21.2] 报告: {md_path}")

    # 指标完整性断言：4 块 3 缝全部入账（VLM fail-open 时分数可为 None，但结构必须完整）
    assert len(drift.chunks) == N_CHUNKS
    assert len(drift.seams) == N_CHUNKS - 1
    assert json_path.exists() and md_path.exists()
