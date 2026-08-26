"""LTX-2.5 视频服务单元测试（离线全 mock，不经真机 :8198）。

conftest._patch_settings 默认 ltx_enabled=False、ltx_comfyui_url=http://localhost:9006；
需要开启的用例局部 monkeypatch。
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.services.ltx25_video_service import (
    LTX25VideoService,
    _snap_dim,
    _snap_ltx_frames,
)


@pytest.fixture
def service():
    return LTX25VideoService()


def _video_outputs(filename: str = "ltx25.mp4") -> dict:
    return {"70": {"videos": [{"filename": filename, "subfolder": "", "type": "output"}]}}


class TestSnapLtxFrames:
    """LTX-2.5 帧数必须满足 %8==1（25fps 音频 1:1 对齐）。"""

    def test_already_aligned_unchanged(self):
        assert _snap_ltx_frames(121) == 121
        assert _snap_ltx_frames(9) == 9

    def test_snaps_down_to_grid(self):
        assert _snap_ltx_frames(76) == 73  # 3s*25+1=76 → 73
        assert _snap_ltx_frames(200) == 193

    def test_minimum_is_nine(self):
        assert _snap_ltx_frames(1) == 9
        assert _snap_ltx_frames(8) == 9

    def test_grid_property_holds(self):
        for n in (9, 10, 25, 60, 100, 200, 361):
            snapped = _snap_ltx_frames(n)
            assert snapped % 8 == 1, f"n={n} -> {snapped} 不在 %8==1 网格上"
            assert 9 <= snapped <= max(9, n)


class TestSnapDim:
    """LTX-2.5 宽高必须是 32 的倍数。"""

    def test_multiple_of_32_unchanged(self):
        assert _snap_dim(768) == 768
        assert _snap_dim(1344) == 1344

    def test_snaps_down_to_multiple(self):
        assert _snap_dim(700) == 672

    def test_minimum_is_32(self):
        assert _snap_dim(10) == 32


class TestIsEnabled:
    def test_default_disabled_by_conftest(self, service):
        assert service.is_enabled() is False

    def test_enabled_when_setting_on(self, service, monkeypatch):
        monkeypatch.setattr(settings, "ltx_enabled", True)
        assert service.is_enabled() is True


class TestGenerateT2V:
    """T2V：distilled 单阶段全分辨率 8 步（2026-08-16 实机核验链）。"""

    async def test_submit_poll_extract_and_workflow_params(
        self, service, mock_call_comfyui, mock_get_comfyui_result
    ):
        mock_get_comfyui_result.return_value = _video_outputs()

        resp = await service.generate_t2v(
            prompt="a cat runs across a rooftop",
            negative_prompt="blurry",
            width=768,
            height=512,
            num_frames=76,
            seed=42,
            scene_id=5,
        )

        assert resp.success is True
        assert resp.data["scene_id"] == 5
        assert "ltx25.mp4" in resp.data["video_url"]
        assert resp.data["duration_seconds"] == 73 // 25

        # 必须直连 LTX 专用实例（conftest 占位 http://localhost:9006），而非 LB
        url, workflow = mock_call_comfyui.call_args[0]
        assert url == settings.ltx_comfyui_url

        # 真实权重文件名（2026-08-16 :8198 /object_info 核验）
        assert workflow["1"]["inputs"]["ckpt_name"] == (
            "ltx-2.5-22b-distilled-transformer-nvfp4.safetensors"
        )
        assert workflow["2"]["inputs"]["text_encoder"] == (
            "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"
        )
        assert workflow["2"]["inputs"]["ckpt_name"] == (
            "ltx-2.5-22b-distilled-transformer-nvfp4.safetensors"
        )
        assert workflow["3"]["inputs"]["vae_name"] == "ltx-2.5-video-vae-bf16.safetensors"
        assert workflow["4"]["inputs"]["vae_name"] == "ltx-2.5-audio-vae-bf16.safetensors"

        # 视频 latent：全分辨率 + %8==1 帧数（无 fps 输入）
        assert workflow["20"]["class_type"] == "EmptyLTXVLatentVideo"
        assert workflow["20"]["inputs"]["length"] == 73
        assert workflow["20"]["inputs"]["length"] % 8 == 1
        assert workflow["20"]["inputs"]["width"] == 768
        assert workflow["20"]["inputs"]["height"] == 512
        assert "fps" not in workflow["20"]["inputs"]
        # 音频 latent → 音画合并 → KSampler（单阶段 8 步 CFG=1 官方蒸馏采样器）
        assert workflow["21"]["class_type"] == "LTXVEmptyLatentAudio"
        assert workflow["21"]["inputs"]["frames_number"] == 73
        assert workflow["21"]["inputs"]["frame_rate"] == 25
        assert workflow["22"]["class_type"] == "LTXVConcatAVLatent"
        assert workflow["22"]["inputs"]["video_latent"] == ["20", 0]
        assert workflow["22"]["inputs"]["audio_latent"] == ["21", 0]
        assert workflow["30"]["class_type"] == "KSampler"
        assert workflow["30"]["inputs"]["steps"] == 8
        assert workflow["30"]["inputs"]["cfg"] == 1.0
        assert workflow["30"]["inputs"]["sampler_name"] == "euler_ancestral_cfg_pp"
        assert workflow["30"]["inputs"]["scheduler"] == "simple"
        assert workflow["30"]["inputs"]["denoise"] == 1.0
        assert workflow["30"]["inputs"]["latent_image"] == ["22", 0]
        assert workflow["30"]["inputs"]["seed"] == 42
        # 不存在的两阶段节点（:8198 无 LTXVBaseSampler/LTXVLatentUpscale）
        assert "40" not in workflow
        assert "41" not in workflow
        # 采样输出拆分音画 → 双 VAE 解码 → 25fps 合成
        assert workflow["45"]["class_type"] == "LTXVSeparateAVLatent"
        assert workflow["45"]["inputs"]["av_latent"] == ["30", 0]
        assert workflow["50"]["inputs"]["samples"] == ["45", 0]
        assert workflow["51"]["class_type"] == "LTXVAudioVAEDecode"
        assert workflow["51"]["inputs"]["samples"] == ["45", 1]
        assert workflow["51"]["inputs"]["audio_vae"] == ["4", 0]
        assert workflow["60"]["inputs"]["fps"] == 25
        # 提示词注入
        assert workflow["10"]["inputs"]["text"] == "a cat runs across a rooftop"
        assert workflow["11"]["inputs"]["text"] == "blurry"

    async def test_prompt_id_missing_returns_error(
        self, service, mock_call_comfyui
    ):
        mock_call_comfyui.return_value = {"no_prompt_id": True}
        resp = await service.generate_t2v(prompt="p")
        assert resp.success is False
        assert "prompt_id" in resp.error

    async def test_submit_failure_returns_error(self, service, mock_call_comfyui):
        mock_call_comfyui.side_effect = RuntimeError("ltx boom")
        resp = await service.generate_t2v(prompt="p")
        assert resp.success is False
        assert "ltx boom" in resp.error


class TestGenerateI2V:
    """I2V：首帧图经 LoadImage → LTXVImgToVideo 接管采样 pos/neg + 视频侧 latent。"""

    async def test_image_uploaded_and_wired(
        self, service, mock_upload_image, mock_call_comfyui, mock_get_comfyui_result
    ):
        mock_get_comfyui_result.return_value = _video_outputs("i2v.mp4")

        resp = await service.generate_i2v(
            image_url="http://x/first.png", prompt="p", num_frames=121, seed=7, scene_id=1
        )

        assert resp.success is True
        assert "i2v.mp4" in resp.data["video_url"]
        assert resp.data["duration_seconds"] == 121 // 25
        # 上传走 LTX 专用实例
        assert mock_upload_image.call_args[0][0] == settings.ltx_comfyui_url
        assert mock_upload_image.call_args[0][1] == "http://x/first.png"

        workflow = mock_call_comfyui.call_args[0][1]
        assert workflow["80"]["class_type"] == "LoadImage"
        assert workflow["80"]["inputs"]["image"] == "input.png"
        assert workflow["81"]["class_type"] == "LTXVImgToVideo"
        assert workflow["81"]["inputs"]["image"] == ["80", 0]
        assert workflow["81"]["inputs"]["positive"] == ["10", 0]
        assert workflow["81"]["inputs"]["negative"] == ["11", 0]
        assert workflow["81"]["inputs"]["vae"] == ["3", 0]
        # 采样器改吃 I2V 条件节点的 positive/negative；latent 仍走 concat 节点
        assert workflow["30"]["inputs"]["positive"] == ["81", 0]
        assert workflow["30"]["inputs"]["negative"] == ["81", 1]
        assert workflow["30"]["inputs"]["latent_image"] == ["22", 0]
        # 视频侧 latent（含图像条件）注入 concat，音频侧不变
        assert workflow["22"]["inputs"]["video_latent"] == ["81", 2]
        assert workflow["22"]["inputs"]["audio_latent"] == ["21", 0]


class TestGenerateFLF2V:
    """FLF2V：首帧 LTXVImgToVideo + 末帧 LTXVAddGuide（frame_idx=length-1）双锚定。"""

    async def test_first_and_last_frames_wired(
        self, service, mock_upload_image, mock_call_comfyui, mock_get_comfyui_result
    ):
        mock_upload_image.side_effect = ["first.png", "last.png"]
        mock_get_comfyui_result.return_value = _video_outputs("flf2v.mp4")

        resp = await service.generate_flf2v(
            first_frame_url="http://x/first.png",
            last_frame_url="http://x/last.png",
            prompt="p",
            num_frames=121,
            scene_id=2,
        )

        assert resp.success is True
        # 上传 2 次（首帧 + 末帧），全部走 LTX 实例且保持顺序
        assert mock_upload_image.await_count == 2
        uploaded = [c.args for c in mock_upload_image.call_args_list]
        assert uploaded == [
            (settings.ltx_comfyui_url, "http://x/first.png"),
            (settings.ltx_comfyui_url, "http://x/last.png"),
        ]

        workflow = mock_call_comfyui.call_args[0][1]
        assert workflow["82"]["class_type"] == "LoadImage"
        assert workflow["82"]["inputs"]["image"] == "last.png"
        # 末帧走 LTXVAddGuide（实机 LTXVImgToVideo 无 last_image 输入）
        assert "last_image" not in workflow["81"]["inputs"]
        assert workflow["83"]["class_type"] == "LTXVAddGuide"
        assert workflow["83"]["inputs"]["image"] == ["82", 0]
        assert workflow["83"]["inputs"]["frame_idx"] == 120
        assert workflow["83"]["inputs"]["positive"] == ["81", 0]
        assert workflow["83"]["inputs"]["negative"] == ["81", 1]
        assert workflow["83"]["inputs"]["latent"] == ["81", 2]
        # 采样器与 concat 视频侧 latent 改吃末帧锚定链输出
        assert workflow["30"]["inputs"]["positive"] == ["83", 0]
        assert workflow["30"]["inputs"]["negative"] == ["83", 1]
        assert workflow["22"]["inputs"]["video_latent"] == ["83", 2]

    async def test_upload_failure_returns_error(self, service, mock_upload_image):
        mock_upload_image.side_effect = RuntimeError("upload down")
        resp = await service.generate_flf2v(
            first_frame_url="http://x/a.png",
            last_frame_url="http://x/b.png",
            prompt="p",
        )
        assert resp.success is False
        assert "upload down" in resp.error
