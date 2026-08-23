# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""R18 制作工厂流水线后端测试（2026-08-19）。

覆盖：
- 分集剧本规划（episode_count 循环 + 场景影视字段）
- compose v2 后期（片头尾卡 / 调色 / xfade 转场 / BGM 混音）
- r18-factory/qc 质检端点（时长/音轨/字幕相似度纯函数 + 集成）
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from novelvideo import config
from novelvideo import model_library as ml
from novelvideo.agents import r18_script_planner as planner
from novelvideo.api.routes import model_library as ml_routes

from test_model_library import _client  # noqa: F401  (复用客户端夹具)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """隔离 settings.db + 模型根目录（照 test_model_library._isolate）。"""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    roots = tmp_path / "models"
    (roots / "checkpoints").mkdir(parents=True)
    (roots / "loras").mkdir(parents=True)
    monkeypatch.setenv("DASHBOX_MODEL_ROOTS", str(roots))
    ml.nas_library_service._cache = None
    ml.nas_library_service._cache_at = 0.0
    yield tmp_path


# ---------------------------------------------------------------------------
# 分集剧本（episode_count + 影视分镜字段）
# ---------------------------------------------------------------------------


def _fake_plan(title: str, scenes_n: int = 2) -> planner.R18ScriptPlan:
    scenes = [
        planner.R18Scene(
            scene_no=i + 1,
            kind="plot" if i % 2 == 0 else "action",
            shot_description=f"desc{i}",
            image_prompt=f"prompt{i}",
            shot_size="中景",
            camera_move="推镜",
            action_desc="走向床边",
            expression="微笑",
            scene_desc="酒店卧室·夜·暖光",
        )
        for i in range(scenes_n)
    ]
    return planner.R18ScriptPlan(title=title, scenes=scenes)


class _FakeRun:
    def __init__(self, output):
        self.output = output

    async def run(self, prompt: str):
        # 按调用次序返回不同集
        idx = getattr(self, "_calls", 0)
        self._calls = idx + 1
        return _FakeRunResult(_fake_plan(f"第{idx + 1}集"))


class _FakeRunResult:
    def __init__(self, output):
        self.output = output


class TestEpisodePlanning:
    def test_single_episode_keeps_compat(self):
        req = planner.R18ScriptPlanRequest(synopsis="梗概")
        assert req.episode_count == 1

    @pytest.mark.asyncio
    async def test_multi_episode_loops_llm(self, monkeypatch):
        agent = _FakeRun(None)

        def _fake_agent():
            return agent

        monkeypatch.setattr(planner, "_get_agent", _fake_agent)
        req = planner.R18ScriptPlanRequest(synopsis="梗概", episode_count=3)
        plan = await planner.plan_r18_script(req)
        # 顶层 = 第 1 集（scenes 可直接消费）+ episodes 全集
        assert plan.title == "第1集"
        assert [ep.title for ep in plan.episodes] == ["第1集", "第2集", "第3集"]
        assert [ep.episode_no for ep in plan.episodes] == [1, 2, 3]

    def test_build_prompt_episode_context(self):
        req = planner.R18ScriptPlanRequest(synopsis="梗概", episode_count=2)
        p1 = planner.build_user_prompt(req, episode_no=1)
        assert "共 2 集" in p1 and "第 1 集" in p1
        p2 = planner.build_user_prompt(req, episode_no=2, prev_episode_summary="上一集发生了…")
        assert "上一集剧情回顾" in p2

    def test_scene_film_fields(self):
        s = planner.R18Scene(
            scene_no=1, kind="plot", shot_description="d", image_prompt="p"
        )
        # 新字段带缺省（老消费方安全）
        assert s.shot_size == "" and s.camera_move == ""
        assert s.action_desc == "" and s.expression == "" and s.scene_desc == ""


# ---------------------------------------------------------------------------
# compose v2 filter（片头尾卡 / 调色 / xfade / BGM）
# ---------------------------------------------------------------------------


class TestComposeFilterV2:
    def test_defaults_unchanged(self):
        fc, vout, aout = ml_routes._build_compose_filter(
            num_videos=1,
            video_has_audio=[True],
            tts_offsets_ms=[0],
            has_srt=False,
            target_w=832,
            target_h=1216,
        )
        assert "concat=n=1" in fc and "xfade" not in fc and "color=c=" not in fc

    def test_opening_closing_cards_join_concat(self):
        fc, vout, aout = ml_routes._build_compose_filter(
            num_videos=1,
            video_has_audio=[False],
            tts_offsets_ms=[None],
            has_srt=False,
            target_w=832,
            target_h=1216,
            opening=ml_routes.R18TitleCard(text="深夜来电", duration_sec=2.0),
            closing=ml_routes.R18TitleCard(text="完", duration_sec=1.5, bg_color="0x101018"),
        )
        assert "color=c=black:s=832x1216:d=2.00" in fc
        assert "concat=n=3" in fc
        assert vout == "[vcat]"

    def test_color_profile_applied(self):
        fc, _, _ = ml_routes._build_compose_filter(
            num_videos=1,
            video_has_audio=[False],
            tts_offsets_ms=[None],
            has_srt=False,
            target_w=832,
            target_h=1216,
            color_profile="film",
        )
        assert "eq=contrast=1.09" in fc

    def test_xfade_chain_with_cards(self):
        fc, vout, aout = ml_routes._build_compose_filter(
            num_videos=2,
            video_has_audio=[False, False],
            tts_offsets_ms=[None, None],
            has_srt=False,
            target_w=832,
            target_h=1216,
            transition="fade",
            transition_sec=0.5,
            opening=ml_routes.R18TitleCard(text="T", duration_sec=2.0),
            video_durations=[4.0, 5.0],
        )
        # 3 段（卡+2镜头）2 次 xfade；offset1 = 2-0.5；offset2 = 2-0.5+4-0.5
        assert fc.count("xfade=transition=fade") == 2
        assert "offset=1.500" in fc and "offset=5.000" in fc

    def test_bgm_mix_label(self):
        fc, _, aout = ml_routes._build_compose_filter(
            num_videos=1,
            video_has_audio=[False],
            tts_offsets_ms=[None],
            has_srt=False,
            target_w=832,
            target_h=1216,
            bgm_input_index=1,
            bgm_volume=0.4,
            total_duration_sec=12.0,
        )
        assert "[1:a]aresample=24000" in fc and "volume=0.4" in fc
        assert "atrim=0:12.000[bgm]" in fc and aout == "[aout]"

    def test_sfx_mix_label(self):
        """环境音效轨：BGM 之后的输入序号，独立音量循环混音。"""
        fc, _, aout = ml_routes._build_compose_filter(
            num_videos=1,
            video_has_audio=[False],
            tts_offsets_ms=[None],
            has_srt=False,
            target_w=832,
            target_h=1216,
            bgm_input_index=1,
            bgm_volume=0.4,
            sfx_input_index=2,
            sfx_volume=0.2,
            total_duration_sec=10.0,
        )
        assert "atrim=0:10.000[bgm]" in fc
        assert "[2:a]aresample=24000" in fc and "volume=0.2" in fc
        assert "atrim=0:10.000[sfx]" in fc
        # 两轨都进 amix
        assert "amix=inputs=2" in fc and aout == "[aout]"


class TestComposeEndpointV2:
    def _mk_ctx(self, monkeypatch, tmp_path):
        out = tmp_path / "output"
        (out / "freezone/_outputs/nsfw_studio").mkdir(parents=True)
        ctx = type("Ctx", (), {"output_dir": str(out), "project_id": "PRJ123"})()

        async def _fake_resolve(**kwargs):
            return ctx

        def _fake_require(ctx_, **kwargs):
            return None

        monkeypatch.setattr(ml_routes, "resolve_project_context", _fake_resolve)
        monkeypatch.setattr(ml_routes, "require_project_home_node", _fake_require)
        return out

    def test_cards_bgm_transition_happy_path(self, monkeypatch, tmp_path):
        pytest.importorskip("respx")

        ml.set_nsfw(True)
        out = self._mk_ctx(monkeypatch, tmp_path)
        v1 = out / "freezone/_outputs/nsfw_studio/v1.mp4"
        v2 = out / "freezone/_outputs/nsfw_studio/v2.mp4"
        bgm = out / "freezone/_outputs/nsfw_studio/bgm.mp3"
        for f in (v1, v2, bgm):
            f.write_bytes(b"x" * 16)

        async def _dur(path):
            return 4.0 if path.name != "bgm.mp3" else 30.0

        async def _aud(path):
            return path.suffix == ".mp4"

        async def _size(path):
            return (832, 1216)

        executed: dict = {}

        class _FakeProc:
            returncode = 0

            async def communicate(self):
                return b"", b""

        async def _fake_exec(*cmd, **kwargs):
            executed["cmd"] = cmd
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
            return _FakeProc()

        monkeypatch.setattr(ml_routes, "_probe_media_duration", _dur)
        monkeypatch.setattr(ml_routes, "_probe_has_audio", _aud)
        monkeypatch.setattr(ml_routes, "_probe_video_size", _size)
        monkeypatch.setattr(ml_routes.asyncio, "create_subprocess_exec", _fake_exec)

        c = _client()
        r = c.post(
            "/model-library/r18-compose",
            json={
                "project_id": "PRJ123",
                "shots": [
                    {"video_url": f"/static/projects/PRJ123/freezone/_outputs/nsfw_studio/{v1.name}"},
                    {"video_url": f"/static/projects/PRJ123/freezone/_outputs/nsfw_studio/{v2.name}"},
                ],
                "opening": {"text": "深夜来电", "duration_sec": 2.0},
                "closing": {"text": "完", "duration_sec": 1.0},
                "transition": "fade",
                "transition_sec": 0.5,
                "color_profile": "warm",
                "bgm_url": f"/static/projects/PRJ123/freezone/_outputs/nsfw_studio/{bgm.name}",
                "bgm_volume": 0.3,
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        # 总时长 = 4 段（卡+2镜头+尾卡）3 边界 ×0.5 重叠：4+4+2+1-1.5 = 9.5
        assert data["duration_sec"] == 9.5
        cmd = executed["cmd"]
        # BGM 输入带 -stream_loop -1
        assert "-stream_loop" in cmd and "-1" in cmd
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert fc.count("xfade=transition=fade") == 3
        assert "volume=0.3" in fc and "colorbalance" in fc

    def test_subtitles_rebuilt_on_real_timeline(self, monkeypatch, tmp_path):
        """subtitles（逐镜头文本）→ 后端按真实时长+片头卡+xfade 重叠重建 SRT。

        前端不再自行拼 SRT（计划时长会与成片渐漂）；真实时间轴与 TTS adelay 同源。
        3 镜头各 4s、片头卡 2s、fade 0.5s：
        S1 [2.0, 5.5]，S2 无字幕，S3 start = 2+4-0.5 + 4-0.5 = 9.0 → [9.0, 12.5]
        """
        ml.set_nsfw(True)
        out = self._mk_ctx(monkeypatch, tmp_path)
        vids = []
        for i in range(3):
            v = out / f"freezone/_outputs/nsfw_studio/v{i}.mp4"
            v.write_bytes(b"x" * 16)
            vids.append(v)

        async def _dur(path):
            return 4.0

        async def _aud(path):
            return False

        async def _size(path):
            return (832, 1216)

        class _FakeProc:
            returncode = 0

            async def communicate(self):
                return b"", b""

        written: dict = {}

        async def _fake_exec(*cmd, **kwargs):
            # 捕获 sub.srt 内容（cwd 即临时目录）
            sub = Path(kwargs.get("cwd", ".")) / "sub.srt"
            if sub.is_file():
                written["srt"] = sub.read_text(encoding="utf-8")
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
            return _FakeProc()

        monkeypatch.setattr(ml_routes, "_probe_media_duration", _dur)
        monkeypatch.setattr(ml_routes, "_probe_has_audio", _aud)
        monkeypatch.setattr(ml_routes, "_probe_video_size", _size)
        monkeypatch.setattr(ml_routes.asyncio, "create_subprocess_exec", _fake_exec)

        c = _client()
        r = c.post(
            "/model-library/r18-compose",
            json={
                "project_id": "PRJ123",
                "shots": [
                    {"video_url": f"/static/projects/PRJ123/freezone/_outputs/nsfw_studio/{v.name}"}
                    for v in vids
                ],
                "subtitles": ["林薇：你来了。", "", "陈默：晚安。"],
                "opening": {"text": "深夜来电", "duration_sec": 2.0},
                "transition": "fade",
                "transition_sec": 0.5,
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        srt = data["srt"]
        assert "00:00:02,000 --> 00:00:05,500" in srt
        assert "林薇：你来了。" in srt
        assert "00:00:09,000 --> 00:00:12,500" in srt
        assert "陈默：晚安。" in srt
        # 空字幕镜头不出块（只有 2 块）
        assert srt.count("-->") == 2
        # 烧录进 ffmpeg 的 sub.srt 与响应一致
        assert written.get("srt") == srt

    def test_subtitles_aligned_with_tts_on_real_timeline(self, monkeypatch, tmp_path):
        """P0 回归：非均匀真实时长 + 长对话 + TTS → SRT 与 adelay 必须同一时间轴。

        模拟 H3 真实出片（时长远非计划整数）：4 镜 [5.37, 4.83, 6.20, 3.50]s、
        片头卡 2s、fade 0.5s（4 个 xfade 边界）。
        镜头真实起始（= TTS adelay 基准）：
          S1=2.000  S2=6.870  S3=11.200  S4=16.900
        SRT 块 [start, start+dur-0.5]：
          S1 [2.000, 6.870]  S2 [6.870, 11.200]  S3 [11.200, 16.900]  S4 [16.900, 19.900]
        TTS（S2/S4 各 +250ms 淡入）：adelay 7120 / 17150 —— 与字幕块同起点。
        """
        ml.set_nsfw(True)
        out = self._mk_ctx(monkeypatch, tmp_path)
        vids, tts_files = [], []
        real_durations = [5.37, 4.83, 6.20, 3.50]
        for i in range(4):
            v = out / f"freezone/_outputs/nsfw_studio/v{i}.mp4"
            v.write_bytes(b"x" * 16)
            vids.append(v)
        for i in (1, 3):  # S2/S4 带 TTS
            t = out / f"freezone/_outputs/nsfw_studio/tts{i}.mp3"
            t.write_bytes(b"x" * 16)
            tts_files.append(t)

        async def _dur(path):
            for i, v in enumerate(vids):
                if path.name == v.name:
                    return real_durations[i]
            return 4.0

        async def _aud(path):
            return False

        async def _size(path):
            return (832, 1216)

        class _FakeProc:
            returncode = 0

            async def communicate(self):
                return b"", b""

        executed: dict = {}

        async def _fake_exec(*cmd, **kwargs):
            executed["cmd"] = cmd
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
            return _FakeProc()

        monkeypatch.setattr(ml_routes, "_probe_media_duration", _dur)
        monkeypatch.setattr(ml_routes, "_probe_has_audio", _aud)
        monkeypatch.setattr(ml_routes, "_probe_video_size", _size)
        monkeypatch.setattr(ml_routes.asyncio, "create_subprocess_exec", _fake_exec)

        long_dialogue = "林薇：我以为你再也不会回来了，这些年我一直在等一个说法，哪怕是一句谎话也好。"
        c = _client()
        r = c.post(
            "/model-library/r18-compose",
            json={
                "project_id": "PRJ123",
                "shots": [
                    {
                        "video_url": f"/static/projects/PRJ123/freezone/_outputs/nsfw_studio/{vids[i].name}",
                        **({"tts_url": f"/static/projects/PRJ123/freezone/_outputs/nsfw_studio/{tts_files[0].name}"} if i == 1 else {}),
                        **({"tts_url": f"/static/projects/PRJ123/freezone/_outputs/nsfw_studio/{tts_files[1].name}"} if i == 3 else {}),
                    }
                    for i in range(4)
                ],
                "subtitles": ["深夜，她推开公寓门。", long_dialogue, "走廊尽头的灯忽明忽暗。", "陈默：晚安，别再等我了。"],
                "opening": {"text": "深夜来电", "duration_sec": 2.0},
                "transition": "fade",
                "transition_sec": 0.5,
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        srt = data["srt"]

        # ① SRT 时间轴 = 真实起始（片头卡 2s + 逐镜真实时长 - xfade 重叠）
        assert "00:00:02,000 --> 00:00:06,870" in srt          # S1
        assert "00:00:06,870 --> 00:00:11,200" in srt          # S2（长对话）
        assert "00:00:11,200 --> 00:00:16,900" in srt          # S3
        assert "00:00:16,900 --> 00:00:19,900" in srt          # S4
        assert long_dialogue in srt
        assert srt.count("-->") == 4
        # 总时长 = 2 + 19.9 - 0.5×4 = 19.9
        assert data["duration_sec"] == 19.9

        # ② TTS adelay 与 SRT 块同起点（+250ms 淡入）：S2 6870+250 / S4 16900+250
        fc = executed["cmd"][executed["cmd"].index("-filter_complex") + 1]
        assert "adelay=7120:all=1" in fc
        assert "adelay=17150:all=1" in fc

    def test_legacy_srt_param_still_burned(self, monkeypatch, tmp_path):
        """旧消费方直接传 srt 文本：原样烧录并在响应回传（向后兼容）。"""
        ml.set_nsfw(True)
        out = self._mk_ctx(monkeypatch, tmp_path)
        v1 = out / "freezone/_outputs/nsfw_studio/v1.mp4"
        v1.write_bytes(b"x" * 16)

        async def _dur(path):
            return 4.0

        async def _aud(path):
            return False

        async def _size(path):
            return (832, 1216)

        class _FakeProc:
            returncode = 0

            async def communicate(self):
                return b"", b""

        async def _fake_exec(*cmd, **kwargs):
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
            return _FakeProc()

        monkeypatch.setattr(ml_routes, "_probe_media_duration", _dur)
        monkeypatch.setattr(ml_routes, "_probe_has_audio", _aud)
        monkeypatch.setattr(ml_routes, "_probe_video_size", _size)
        monkeypatch.setattr(ml_routes.asyncio, "create_subprocess_exec", _fake_exec)

        legacy = "1\n00:00:00,000 --> 00:00:02,000\n旧字幕\n"
        c = _client()
        r = c.post(
            "/model-library/r18-compose",
            json={
                "project_id": "PRJ123",
                "shots": [
                    {"video_url": f"/static/projects/PRJ123/freezone/_outputs/nsfw_studio/{v1.name}"}
                ],
                "srt": legacy,
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["srt"] == legacy


# ---------------------------------------------------------------------------
# QC 质检（纯函数 + 端点集成）
# ---------------------------------------------------------------------------


class TestQcPure:
    def test_extract_srt_text(self):
        srt = "1\n00:00:00,000 --> 00:00:02,000\n你好\n2\n00:00:02,000 --> 00:00:04,000\n世界！\n"
        assert ml_routes._qc_extract_srt_text(srt) == "你好世界！"

    def test_similarity_ignores_punct(self):
        assert ml_routes._qc_similarity("你好，世界！", "你好世界") == 1.0
        assert ml_routes._qc_similarity("完全不同", "毫无关系") < 0.3

    def test_similarity_empty(self):
        assert ml_routes._qc_similarity("", "x") == 0.0


class TestQcEndpoint:
    def _mk_ctx(self, monkeypatch, tmp_path):
        out = tmp_path / "output"
        (out / "freezone/_outputs/nsfw_studio").mkdir(parents=True)
        ctx = type("Ctx", (), {"output_dir": str(out)})()

        async def _fake_resolve(**kwargs):
            return ctx

        def _fake_require(ctx_, **kwargs):
            return None

        monkeypatch.setattr(ml_routes, "resolve_project_context", _fake_resolve)
        monkeypatch.setattr(ml_routes, "require_project_home_node", _fake_require)
        return out

    def test_blocked_without_r18(self):
        ml_routes.nsfw_status  # 确认模块属性存在（r18 gate 在端点首行）
        c = _client()
        r = c.post("/model-library/r18-factory/qc", json={"compose_url": "/x", "project_id": "P"})
        assert r.status_code == 403

    def test_qc_full_checks(self, monkeypatch, tmp_path):
        pytest.importorskip("respx")
        import respx

        ml.set_nsfw(True)
        out = self._mk_ctx(monkeypatch, tmp_path)
        comp = out / "freezone/_outputs/nsfw_studio/final.mp4"
        comp.write_bytes(b"x" * 16)

        async def _dur(path):
            return 10.0

        async def _aud(path):
            return True

        monkeypatch.setattr(ml_routes, "_probe_media_duration", _dur)
        monkeypatch.setattr(ml_routes, "_probe_has_audio", _aud)

        class _FakeProc:
            returncode = 0

            async def communicate(self):
                return b"", b""

        async def _fake_exec(*cmd, **kwargs):
            # ffmpeg 抽音轨：把输出 wav 写出来（模拟）
            out_wav = Path(cmd[-1])
            out_wav.parent.mkdir(parents=True, exist_ok=True)
            out_wav.write_bytes(b"RIFF" + b"\x00" * 32)
            return _FakeProc()

        monkeypatch.setattr(ml_routes.asyncio, "create_subprocess_exec", _fake_exec)

        srt = "1\n00:00:00,000 --> 00:00:05,000\n你别走，我等这一天\n"

        with respx.mock:
            respx.post("http://192.168.71.127:8300/v1/asr/transcribe").mock(
                return_value=Response(200, json={"text": "你别走，我等这一天"})
            )
            # LLM 审查：直接失败路径 → fail-open 跳过
            async def _boom(lines):
                raise RuntimeError("llm down")

            monkeypatch.setattr(ml_routes, "review_r18_quality", _boom)

            c = _client()
            r = c.post(
                "/model-library/r18-factory/qc",
                json={
                    "project_id": "PRJ123",
                    "compose_url": f"/static/projects/PRJ123/freezone/_outputs/nsfw_studio/{comp.name}",
                    "srt": srt,
                    "scenes": [
                        {"scene_no": 1, "shot_description": "d", "dialogue": "你别走", "duration_sec": 5},
                        {"scene_no": 2, "shot_description": "d2", "narration": "我等这一天", "duration_sec": 5},
                    ],
                    "llm_review": True,
                },
            )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["passed"] is True
        assert data["av_sync_ok"] is True  # 10s vs 10s
        assert data["has_audio"] is True
        assert data["asr_similarity"] == 1.0
        assert data["llm"] is None  # fail-open

    def test_qc_fails_on_no_audio(self, monkeypatch, tmp_path):
        ml.set_nsfw(True)
        out = self._mk_ctx(monkeypatch, tmp_path)
        comp = out / "freezone/_outputs/nsfw_studio/final.mp4"
        comp.write_bytes(b"x" * 16)

        async def _dur(path):
            return 9.0

        async def _aud(path):
            return False

        monkeypatch.setattr(ml_routes, "_probe_media_duration", _dur)
        monkeypatch.setattr(ml_routes, "_probe_has_audio", _aud)

        c = _client()
        r = c.post(
            "/model-library/r18-factory/qc",
            json={
                "project_id": "PRJ123",
                "compose_url": f"/static/projects/PRJ123/freezone/_outputs/nsfw_studio/{comp.name}",
                "scenes": [],
                "llm_review": False,
            },
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["passed"] is False and data["has_audio"] is False
