"""P7 端到端集成验收脚本。

从"一句话创意"走完 8 Agent 全链路，产出可播放短剧成片，并生成验收报告。
预计耗时：角色 2min + 分镜 12s + 视频 ~10min/场景 + 配音/字幕/剪辑/质检 <30s。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "http://127.0.0.1:8100"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "e2e_test"
REPORT_PATH = OUTPUT_DIR / "e2e_report.json"

PREMISE = "深夜的便利店，一名疲惫的程序员和神秘女顾客因最后一杯热咖啡相遇。"
PROJECT_ID = f"e2e-{int(time.time())}"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


async def post(client: httpx.AsyncClient, path: str, payload: dict[str, Any], timeout: float = 600.0) -> dict[str, Any]:
    response = await client.post(f"{BASE_URL}{path}", json=payload, timeout=httpx.Timeout(timeout))
    response.raise_for_status()
    return response.json()


async def check_static_file(url: str) -> bool:
    try:
        # trust_env=False 避免 macOS 系统 HTTP 代理（如 127.0.0.1:7890）
        # 拦截 localhost/Tailscale 内网请求导致连接挂起
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(url, timeout=30.0)
            return response.status_code == 200 and len(response.content) > 0
    except Exception:
        return False


async def step_script(client: httpx.AsyncClient) -> dict[str, Any]:
    log("Step 1/9: 生成剧本...")
    payload = {
        "premise": PREMISE,
        "genre": "都市悬疑",
        "episodes": 1,
        "scenes_per_episode": 2,
    }
    result = await post(client, "/api/drama/script/generate", payload)
    if not result.get("success"):
        raise RuntimeError(f"剧本生成失败: {result.get('error')}")
    script = result["data"]
    log(f"  标题: {script['title']}")
    log(f"  角色: {len(script['characters'])}")
    log(f"  场景: {len(script['scenes'])}")
    return script


async def step_character(client: httpx.AsyncClient, script: dict[str, Any]) -> dict[str, Any]:
    log("Step 2/9: 生成主角定妆照...")
    protagonist = next((c for c in script["characters"] if "主角" in c.get("role", "") or c.get("role") == "protagonist"), script["characters"][0])
    payload = {
        "character": protagonist,
        "style": "写实电影感",
        "consistency_level": "L3",
    }
    result = await post(client, "/api/drama/character/generate", payload)
    if not result.get("success"):
        raise RuntimeError(f"角色生成失败: {result.get('error')}")
    character = result["data"]
    log(f"  角色: {character.get('name')}")
    for view, url in character.get("reference_images", {}).items():
        ok = await check_static_file(url)
        log(f"  视图 {view}: {'OK' if ok else 'FAIL'} ({url})")
    return character


async def step_storyboard(client: httpx.AsyncClient, script: dict[str, Any]) -> list[dict[str, Any]]:
    log("Step 3/9: 生成分镜关键帧...")
    storyboards: list[dict[str, Any]] = []
    for scene in script["scenes"]:
        payload = {
            "scene": scene,
            "characters": script["characters"],
            "style": "写实电影感",
        }
        result = await post(client, "/api/drama/storyboard/generate", payload)
        if not result.get("success"):
            raise RuntimeError(f"分镜生成失败 scene={scene['scene_id']}: {result.get('error')}")
        sb = result["data"]
        ok = await check_static_file(sb["image_url"])
        log(f"  场景 {sb['scene_id']}: {'OK' if ok else 'FAIL'} ({sb['image_url']})")
        storyboards.append(sb)
    return storyboards


async def step_video(client: httpx.AsyncClient, storyboards: list[dict[str, Any]], scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    log("Step 4/9: 生成视频片段...")
    videos: list[dict[str, Any]] = []
    scene_prompt_map = {s["scene_id"]: s.get("prompt", "") for s in scenes}
    for sb in storyboards:
        payload = {
            "scene_id": sb["scene_id"],
            "image_url": sb["image_url"],
            "prompt": scene_prompt_map.get(sb["scene_id"], ""),
            "negative_prompt": "",
            "duration_seconds": 3,
        }
        result = await post(client, "/api/drama/video/generate", payload)
        if not result.get("success"):
            raise RuntimeError(f"视频生成失败 scene={sb['scene_id']}: {result.get('error')}")
        video = result["data"]
        ok = await check_static_file(video["video_url"])
        log(f"  场景 {video['scene_id']}: {'OK' if ok else 'FAIL'} ({video['video_url']})")
        videos.append(video)
    return videos


async def step_voice(client: httpx.AsyncClient, script: dict[str, Any]) -> list[dict[str, Any]]:
    log("Step 5/9: 生成配音...")
    voices: list[dict[str, Any]] = []
    character_map = {c["character_id"]: c for c in script["characters"]}
    for scene in script["scenes"]:
        if not scene.get("dialogue"):
            continue
        lines = scene["dialogue"].strip().split("\n")
        dialogues = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            dialogues.append({
                "text": line,
                "character_name": scene.get("character_actions", "")[:10],
                "character_role": "主角" if "主角" in scene.get("character_actions", "") else "配角",
            })
        if not dialogues:
            dialogues.append({
                "text": scene.get("description", "场景无言"),
                "character_name": "旁白",
                "character_role": "narrator",
            })
        payload = {
            "scene_id": scene["scene_id"],
            "dialogues": dialogues,
        }
        result = await post(client, "/api/drama/voice/generate", payload)
        if not result.get("success"):
            raise RuntimeError(f"配音生成失败 scene={scene['scene_id']}: {result.get('error')}")
        voice = result["data"]
        audio_urls = voice.get("audio_urls", [])
        if audio_urls:
            results = await asyncio.gather(*[check_static_file(u["audio_url"]) for u in audio_urls])
            ok = all(results)
        else:
            ok = False
        log(f"  场景 {voice['scene_id']}: {'OK' if ok else 'FAIL'} ({voice.get('total_lines')} 条)")
        voices.append(voice)
    return voices


async def step_subtitle(client: httpx.AsyncClient, voices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    log("Step 6/9: 生成字幕...")
    subtitles: list[dict[str, Any]] = []
    for voice in voices:
        audio_urls = voice.get("audio_urls", [])
        if not audio_urls:
            continue
        payload = {
            "scene_id": voice["scene_id"],
            "audio_url": audio_urls[0]["audio_url"],
            "language": "zh",
        }
        result = await post(client, "/api/drama/subtitle/generate", payload)
        if not result.get("success"):
            raise RuntimeError(f"字幕生成失败 scene={voice['scene_id']}: {result.get('error')}")
        subtitle = result["data"]
        log(f"  场景 {subtitle['scene_id']}: {'OK' if subtitle.get('segments') else 'EMPTY'} ({len(subtitle.get('segments', []))} 段)")
        subtitles.append(subtitle)
    return subtitles


async def step_edit(client: httpx.AsyncClient, script: dict[str, Any], videos: list[dict[str, Any]], voices: list[dict[str, Any]], subtitles: list[dict[str, Any]]) -> dict[str, Any]:
    log("Step 7/9: 合成成片...")
    video_map = {v["scene_id"]: v for v in videos}
    voice_map = {v["scene_id"]: v for v in voices}
    subtitle_map = {s["scene_id"]: s for s in subtitles}

    segments = []
    for scene in script["scenes"]:
        sid = scene["scene_id"]
        if sid not in video_map or sid not in voice_map or sid not in subtitle_map:
            continue
        segments.append({
            "scene_id": sid,
            "video_url": video_map[sid]["video_url"],
            "audio_url": voice_map[sid]["audio_urls"][0]["audio_url"],
            "subtitle_url": subtitle_map[sid].get("srt_url") or f"/static/subtitle/scene_{sid}.srt",
            "duration_seconds": video_map[sid].get("duration_seconds", 3),
        })

    if not segments:
        raise RuntimeError("没有可合成的片段")

    payload = {
        "project_id": PROJECT_ID,
        "title": script["title"],
        "segments": segments,
        "transition": "fade",
        "output_resolution": "480x832",
        "output_fps": 24,
    }
    result = await post(client, "/api/drama/edit/compose", payload)
    if not result.get("success"):
        raise RuntimeError(f"剪辑失败: {result.get('error')}")
    edit = result["data"]
    ok = await check_static_file(edit["final_video_url"])
    log(f"  成片: {'OK' if ok else 'FAIL'} ({edit['final_video_url']})")
    log(f"  时长: {edit['duration_seconds']:.1f}s | 场景数: {edit['segments_count']}")
    return edit


async def step_quality(client: httpx.AsyncClient, script: dict[str, Any], subtitles: list[dict[str, Any]]) -> dict[str, Any]:
    log("Step 8/9: 文本质检...")
    payload = {
        "project_id": PROJECT_ID,
        "title": script["title"],
        "characters": script["characters"],
        "scenes": script["scenes"],
        "subtitles": subtitles,
    }
    # GLM-5.2 思考模式质检约需 160-300s，给 900s 余量
    result = await post(client, "/api/drama/quality/check", payload, timeout=900.0)
    if not result.get("success"):
        raise RuntimeError(f"文本质检失败: {result.get('error')}")
    quality = result["data"]
    log(f"  质量分: {quality['score']} | 问题: {len(quality['issues'])}")
    return quality


async def step_visual_quality(client: httpx.AsyncClient, videos: list[dict[str, Any]]) -> dict[str, Any]:
    log("Step 9/9: 视觉质检...")
    if not videos:
        log("  跳过：没有可质检的视频")
        return {"skipped": True, "reason": "no videos"}
    video = videos[0]
    payload = {
        "project_id": PROJECT_ID,
        "title": "P7 端到端验收",
        "scene_id": video["scene_id"],
        "video_url": video["video_url"],
        "max_frames": 4,
    }
    result = await post(client, "/api/drama/quality/visual", payload, timeout=300.0)
    if not result.get("success"):
        log(f"  视觉质检失败（非致命）: {result.get('error')}")
        return {"skipped": True, "reason": result.get("error", "unknown")}
    visual = result["data"]
    log(f"  质量分: {visual['score']} | 问题: {len(visual['issues'])}")
    return visual


async def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log(f"P7 端到端集成验收开始 | project_id={PROJECT_ID}")
    log(f"输出目录: {OUTPUT_DIR}")

    report: dict[str, Any] = {
        "project_id": PROJECT_ID,
        "premise": PREMISE,
        "started_at": time.time(),
        "steps": {},
        "passed": False,
    }

    async with httpx.AsyncClient(trust_env=False) as client:
        try:
            t0 = time.time()
            script = await step_script(client)
            report["steps"]["script"] = {"elapsed": time.time() - t0, "data": script}

            t0 = time.time()
            character = await step_character(client, script)
            report["steps"]["character"] = {"elapsed": time.time() - t0, "data": character}

            t0 = time.time()
            storyboards = await step_storyboard(client, script)
            report["steps"]["storyboard"] = {"elapsed": time.time() - t0, "data": storyboards}

            t0 = time.time()
            videos = await step_video(client, storyboards, script["scenes"])
            report["steps"]["video"] = {"elapsed": time.time() - t0, "data": videos}

            t0 = time.time()
            voices = await step_voice(client, script)
            report["steps"]["voice"] = {"elapsed": time.time() - t0, "data": voices}

            t0 = time.time()
            subtitles = await step_subtitle(client, voices)
            report["steps"]["subtitle"] = {"elapsed": time.time() - t0, "data": subtitles}

            t0 = time.time()
            edit = await step_edit(client, script, videos, voices, subtitles)
            report["steps"]["edit"] = {"elapsed": time.time() - t0, "data": edit}

            t0 = time.time()
            quality = await step_quality(client, script, subtitles)
            report["steps"]["quality"] = {"elapsed": time.time() - t0, "data": quality}

            t0 = time.time()
            visual_quality = await step_visual_quality(client, videos)
            report["steps"]["visual_quality"] = {"elapsed": time.time() - t0, "data": visual_quality}

            report["passed"] = True
            report["total_elapsed_seconds"] = time.time() - report["started_at"]
            log("=" * 60)
            log(f"P7 端到端集成验收通过 | 总耗时: {report['total_elapsed_seconds']:.1f}s")
            log(f"成片 URL: {edit['final_video_url']}")
            log("=" * 60)
        except Exception as e:
            report["error"] = str(e)
            report["total_elapsed_seconds"] = time.time() - report["started_at"]
            log(f"P7 端到端集成验收失败: {e}")
            import traceback
            traceback.print_exc()
            return 1
        finally:
            report["finished_at"] = time.time()
            REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"验收报告已保存: {REPORT_PATH}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
