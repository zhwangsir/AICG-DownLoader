"""M24.2: VideoRequest seed/lock_params 字段 + shot_params.json 落盘集成测试。

集成范围：schemas 字段校验 → orchestrator 快照落盘 → JSON 读回 →
VideoRequest 反序列化重建，验证全链路字段完整性。
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.models.schemas import VideoRequest
from app.services.pipeline_orchestrator import PipelineOrchestrator

U64_MAX = 18446744073709551615  # 2^64 - 1


def _req(**kw) -> VideoRequest:
    base = {"scene_id": 1, "image_url": "http://x/keyframe.png", "prompt": "p"}
    base.update(kw)
    return VideoRequest(**base)


class TestVideoRequestNewFields:
    """seed（u64 语义）/ lock_params（JSON 对象语义）字段校验。"""

    def test_defaults(self):
        req = _req()
        assert req.seed is None
        assert req.lock_params is None

    def test_seed_u64_boundaries(self):
        assert _req(seed=0).seed == 0
        assert _req(seed=U64_MAX).seed == U64_MAX
        assert _req(seed=123456789).seed == 123456789

    def test_seed_negative_rejected(self):
        with pytest.raises(ValidationError):
            _req(seed=-1)

    def test_seed_over_u64_rejected(self):
        with pytest.raises(ValidationError):
            _req(seed=U64_MAX + 1)

    def test_lock_params_dict(self):
        params = {"engine": "h3", "steps": 8, "sampler": "res_multistep", "turbo": True}
        req = _req(lock_params=params)
        assert req.lock_params == params

    def test_lock_params_non_dict_rejected(self):
        with pytest.raises(ValidationError):
            _req(lock_params="h3")
        with pytest.raises(ValidationError):
            _req(lock_params=[1, 2])

    def test_json_serialization_roundtrip(self):
        """model_dump(mode=json) → json.loads → 字段完整（含 u64 大数精确性）。"""
        req = _req(
            seed=U64_MAX,
            lock_params={"engine": "ltx", "cfg": 1.0},
            engine="h3",
            reference_images=["http://x/front.png"],
        )
        dumped = req.model_dump(mode="json")
        back = json.loads(json.dumps(dumped))
        assert back["seed"] == U64_MAX  # u64 上限值 JSON 往返不丢精度
        assert back["lock_params"] == {"engine": "ltx", "cfg": 1.0}
        assert back["engine"] == "h3"
        # JSON 反序列化 → VideoRequest 重建，字段逐一相等
        rebuilt = VideoRequest(**back)
        assert rebuilt.seed == req.seed
        assert rebuilt.lock_params == req.lock_params
        assert rebuilt.reference_images == req.reference_images


class TestShotParamsPersistence:
    """shot_params.json 落盘/读回集成测试（快照目录重定向 tmp）。"""

    @pytest.fixture
    def orchestrator(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "app.services.pipeline_orchestrator.PIPELINE_OUTPUT_ROOT", tmp_path
        )
        return PipelineOrchestrator()

    def _items(self) -> list[VideoRequest]:
        return [
            _req(scene_id=1, seed=42, lock_params={"engine": "h3"}, prompt="镜头1"),
            _req(scene_id=2, seed=None, prompt="镜头2"),
            _req(scene_id=3, seed=7, lock_params={"engine": "ltx", "steps": 8}, prompt="镜头3"),
        ]

    def test_pending_snapshot_fields_complete(self, orchestrator, tmp_path):
        path = orchestrator._save_shot_params("pipeline-test", self._items())
        assert path == tmp_path / "pipeline-test" / "shot_params.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["project_id"] == "pipeline-test"
        assert payload["saved_at"] > 0
        shots = payload["shots"]
        assert len(shots) == 3
        # 新字段落盘完整
        assert shots[0]["seed"] == 42
        assert shots[0]["lock_params"] == {"engine": "h3"}
        assert shots[1]["seed"] is None
        assert shots[1]["lock_params"] is None
        assert shots[2]["seed"] == 7
        assert shots[2]["lock_params"] == {"engine": "ltx", "steps": 8}
        # 生成前全部 pending、无产物
        assert all(s["status"] == "pending" and s["video_url"] == "" for s in shots)

    def test_results_merged_snapshot(self, orchestrator, tmp_path):
        videos = [
            {"scene_id": 1, "video_url": "http://x/v1.mp4"},
            {"scene_id": 3, "video_url": "http://x/v3.mp4"},
        ]
        failed = [2]
        path = orchestrator._save_shot_params(
            "pipeline-test", self._items(), videos=videos, failed=failed
        )
        shots = json.loads(path.read_text(encoding="utf-8"))["shots"]
        assert shots[0]["status"] == "success" and shots[0]["video_url"] == "http://x/v1.mp4"
        assert shots[1]["status"] == "failed" and shots[1]["video_url"] == ""
        assert shots[2]["status"] == "success" and shots[2]["video_url"] == "http://x/v3.mp4"
        # 合并结果不破坏锁定的输入参数
        assert shots[0]["seed"] == 42 and shots[0]["lock_params"] == {"engine": "h3"}

    def test_snapshot_roundtrip_rebuilds_video_request(self, orchestrator):
        """落盘 → 读回 → VideoRequest 重建：重拍时按 scene_id 恢复参数的完整链路。"""
        items = self._items()
        path = orchestrator._save_shot_params("pipeline-rt", items)
        payload = json.loads(path.read_text(encoding="utf-8"))
        for shot, original in zip(payload["shots"], items):
            # 快照条目含 status/video_url 额外键，Pydantic 默认忽略 extra
            rebuilt = VideoRequest(**shot)
            assert rebuilt.scene_id == original.scene_id
            assert rebuilt.seed == original.seed
            assert rebuilt.lock_params == original.lock_params
            assert rebuilt.prompt == original.prompt
            assert rebuilt.engine == original.engine

    def test_project_id_sanitized(self, orchestrator, tmp_path):
        path = orchestrator._save_shot_params("../evil/../../etc", self._items())
        # 路径穿越字符被剔除，落在 tmp 根下
        assert tmp_path in path.parents
        assert ".." not in path.parts
