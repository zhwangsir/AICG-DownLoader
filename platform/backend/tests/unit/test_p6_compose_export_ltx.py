"""P6: drama compose/export 768P + generate/one-click never auto-LTX."""

from __future__ import annotations

from app.agents.video_agent import route_video_engine
from app.config import settings
from app.models.schemas import EditRequest, EditSegment, PipelineRunRequest, VideoRequest


def _seg() -> EditSegment:
    return EditSegment(
        scene_id=1,
        video_url="http://x/v.mp4",
        audio_url="http://x/a.wav",
    )


def _video(**kw) -> VideoRequest:
    defaults = dict(
        scene_id=1,
        image_url="http://x/i.png",
        prompt="cinematic empty street, camera pans",
        duration_seconds=5,
    )
    defaults.update(kw)
    return VideoRequest(**defaults)


class TestComposeExport768P:
    def test_edit_request_defaults_768p_portrait(self):
        req = EditRequest(segments=[_seg()])
        assert req.output_resolution == "768x1344"
        assert req.output_fps == 24

    def test_pipeline_run_request_defaults_768p_portrait(self):
        req = PipelineRunRequest(premise="深夜便利店偶遇")
        assert req.output_resolution == "768x1344"
        assert req.output_fps == 24

    def test_landscape_768p_is_valid_export_size(self):
        req = EditRequest(segments=[_seg()], output_resolution="1344x768")
        assert req.output_resolution == "1344x768"


class TestDramaGenerateNeverAutoLtx:
    def test_empty_motion_stays_h3_when_ltx_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "ltx_enabled", True)
        assert (
            route_video_engine(
                _video(prompt="aerial drone shot over the city, camera pans"),
                settings,
            )
            == "h3"
        )

    def test_long_duration_stays_h3_when_ltx_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "ltx_enabled", True)
        assert route_video_engine(_video(duration_seconds=20), settings) == "h3"

    def test_auto_engine_stays_h3(self, monkeypatch):
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "ltx_enabled", True)
        assert route_video_engine(_video(engine="auto", duration_seconds=20), settings) == "h3"

    def test_explicit_ltx_still_allowed_when_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "ltx_enabled", True)
        assert route_video_engine(_video(engine="ltx"), settings) == "ltx"

    def test_explicit_ltx_disabled_falls_back_h3(self, monkeypatch):
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "ltx_enabled", False)
        assert route_video_engine(_video(engine="ltx"), settings) == "h3"


class TestNoRegressProductPins:
    def test_p2_last_frame_chain_default_on(self):
        assert settings.h3_last_frame_chain_enabled is True

    def test_p5_vlm_default_flash_next(self):
        assert settings.visual_model_name == "qwen3.8-flash-next"

    def test_p5_h3_generate_still_768p(self):
        assert settings.h3_width == 768
        assert settings.h3_height == 1344
