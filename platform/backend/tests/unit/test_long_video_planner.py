"""M21.3 LongVideoPlanner 单元测试 —— 纯规则模块，无外部依赖。

覆盖：
- 场景识别：地点指纹提取 / 相邻同组聚类
- 镜头切换检测：shot_type / camera / episode / setting 四类切换
- 语义连贯性：同集同地点情绪节拍全同 → 高分；跨集 → 低分；节拍链顺承
- 切块：贪心装填、跨集强制边界、超时长场景拆续写子块、max_chunks 截断、
  空 prompt 兜底、prompt 合并连接符、intent 格式、to_dict 序列化
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.models.schemas import Scene, Script
from app.services.long_video_planner import (
    ChunkPlan,
    LongVideoPlanner,
    LongVideoPlan,
    ShotSwitch,
)


def _scene(
    scene_id: int,
    *,
    episode: int = 1,
    shot_type: str = "中景",
    camera: str = "static",
    description: str = "深夜便利店，程序员盯着货架",
    prompt: str = "convenience store at night",
    emotion: str = "tension",
    duration: int = 5,
    beat: str = "hook",
) -> Scene:
    return Scene(
        scene_id=scene_id,
        episode=episode,
        shot_type=shot_type,
        description=description,
        prompt=prompt,
        emotion=emotion,
        duration_seconds=duration,
        camera_movement=camera,
        narrative_beat=beat,
    )


def _script(scenes: list[Scene]) -> Script:
    return Script(project_id="t", title="测试", scenes=scenes)


@pytest.fixture
def planner() -> LongVideoPlanner:
    return LongVideoPlanner()


@pytest.fixture(autouse=True)
def _long_video_defaults(monkeypatch):
    monkeypatch.setattr(settings, "long_video_chunk_seconds", 14)
    monkeypatch.setattr(settings, "long_video_max_chunks", 8)


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------
class TestInputValidation:
    def test_empty_script_raises(self, planner):
        with pytest.raises(ValueError, match="剧本无场景"):
            planner.plan(_script([]))

    def test_illegal_chunk_seconds_raises(self, planner):
        with pytest.raises(ValueError, match="chunk_seconds 非法"):
            planner.plan(_script([_scene(1)]), chunk_seconds=0)


# ---------------------------------------------------------------------------
# 场景识别
# ---------------------------------------------------------------------------
class TestSceneRecognition:
    def test_setting_fingerprint_lexicon_hit(self, planner):
        assert planner.setting_fingerprint(_scene(1, description="深夜便利店，灯光惨白")) == "便利店"
        assert planner.setting_fingerprint(_scene(2, description="天台上风吹很大")) == "天台"

    def test_setting_fingerprint_unknown_fallback(self, planner):
        assert planner.setting_fingerprint(_scene(3, description="一片虚无之中")) == "unknown"
        assert planner.setting_fingerprint(_scene(4, description="")) == "unknown"

    def test_scene_groups_adjacent_same_setting(self, planner):
        scenes = [
            _scene(1, description="便利店内，货架前"),
            _scene(2, description="便利店门口，雨下大了"),
            _scene(3, description="天台上，两人对峙"),
            _scene(4, episode=2, description="天台上，次日清晨"),
        ]
        groups = planner.recognize_scene_groups(scenes)
        assert groups == [[1, 2], [3], [4]]  # 跨集即换组（即便同地点）


# ---------------------------------------------------------------------------
# 镜头切换检测
# ---------------------------------------------------------------------------
class TestShotSwitchDetection:
    def test_shot_type_change_detected(self, planner):
        switches = planner.detect_shot_switches([
            _scene(1, shot_type="中景"),
            _scene(2, shot_type="特写"),
        ])
        assert len(switches) == 1
        assert switches[0].after_scene_id == 1 and switches[0].before_scene_id == 2
        assert "shot_type" in switches[0].kinds

    def test_camera_and_episode_and_setting_detected(self, planner):
        switches = planner.detect_shot_switches([
            _scene(1, camera="static", description="便利店内"),
            _scene(2, episode=2, camera="pan", description="天台上"),
        ])
        kinds = switches[0].kinds
        assert set(kinds) == {"episode", "setting", "camera"}

    def test_no_switch_when_identical(self, planner):
        assert planner.detect_shot_switches([_scene(1), _scene(2)]) == []


# ---------------------------------------------------------------------------
# 语义连贯性
# ---------------------------------------------------------------------------
class TestCoherence:
    def test_identical_metadata_high_coherence(self, planner):
        c = planner.coherence(_scene(1), _scene(2))
        # 同集 0.25 + 同地点 0.25 + 同情绪 0.25 + 同节拍 0.6*0.25
        assert c == pytest.approx(0.25 + 0.25 + 0.25 + 0.15)

    def test_beat_chain_follows_boosts_score(self, planner):
        c = planner.coherence(_scene(1, beat="hook"), _scene(2, beat="escalation"))
        assert c == pytest.approx(0.25 + 0.25 + 0.25 + 0.25)  # 满分

    def test_cross_episode_low_coherence(self, planner):
        c = planner.coherence(
            _scene(1, emotion="happy", beat="hook"),
            _scene(2, episode=2, description="天台上", emotion="sad", beat="cliffhanger"),
        )
        # 跨集 0 + 异地 0 + 跨情绪族 0.2*0.25 + 节拍跨级 0.3*0.25
        assert c == pytest.approx(0.05 + 0.075)
        assert c < 0.3

    def test_empty_beats_neutral(self, planner):
        c = planner.coherence(_scene(1, beat=""), _scene(2, beat=""))
        # 双空节拍 0.5*0.25，其余满分
        assert c == pytest.approx(0.25 + 0.25 + 0.25 + 0.125)


# ---------------------------------------------------------------------------
# 切块：贪心装填
# ---------------------------------------------------------------------------
class TestChunkPacking:
    def test_greedy_fill_by_duration(self, planner):
        """4 场景 × 5s，块长 14 → 2 块（5+5 / 5+5），每块 10s。"""
        plan = planner.plan(_script([_scene(i) for i in range(1, 5)]))
        assert len(plan.chunks) == 2
        assert plan.chunks[0].scene_ids == [1, 2]
        assert plan.chunks[1].scene_ids == [3, 4]
        assert plan.chunks[0].estimated_seconds == pytest.approx(10.0)
        assert plan.total_estimated_seconds == pytest.approx(20.0)
        assert plan.scene_coverage == pytest.approx(1.0)

    def test_episode_forces_boundary(self, planner):
        """同容量可装下，但跨集强制拆块。"""
        scenes = [_scene(1, duration=5), _scene(2, duration=5, episode=2)]
        plan = planner.plan(_script(scenes))
        assert len(plan.chunks) == 2
        assert plan.chunks[1].boundary_before == "episode"
        assert plan.chunks[1].coherence_to_prev < 0.5

    def test_single_scene_exact_fit(self, planner):
        plan = planner.plan(_script([_scene(1, duration=14)]))
        assert len(plan.chunks) == 1
        assert plan.chunks[0].boundary_before == "start"
        assert plan.chunks[0].coherence_to_prev == 1.0

    def test_oversized_scene_split_into_continuations(self, planner):
        """30s 单场景拆 3 子块（14+14+2），续写块带 continuation 前缀与边界。"""
        plan = planner.plan(_script([_scene(1, duration=30)]))
        assert len(plan.chunks) == 3
        assert [c.estimated_seconds for c in plan.chunks] == [14.0, 14.0, 2.0]
        assert plan.chunks[1].boundary_before == "continuation"
        assert plan.chunks[1].coherence_to_prev == 1.0
        assert plan.chunks[1].prompt.startswith("The shot continues smoothly")
        assert plan.chunks[2].boundary_before == "continuation"
        assert any("拆为 3 个续写子块" in w for w in plan.warnings)

    def test_max_chunks_truncation(self, planner, monkeypatch):
        monkeypatch.setattr(settings, "long_video_max_chunks", 1)
        plan = planner.plan(_script([_scene(i) for i in range(1, 5)]))
        assert len(plan.chunks) == 1
        assert plan.scene_coverage == pytest.approx(0.5)
        assert any("截断" in w for w in plan.warnings)

    def test_empty_prompt_falls_back_to_description(self, planner):
        plan = planner.plan(_script([_scene(1, prompt="", description="深夜便利店内")]))
        assert "便利店" in plan.chunks[0].prompt  # 描述兜底
        assert any("无 prompt" in w for w in plan.warnings)

    def test_illegal_duration_falls_back_to_chunk_seconds(self, planner):
        plan = planner.plan(_script([_scene(1, duration=0)]))
        assert plan.chunks[0].estimated_seconds == pytest.approx(14.0)
        assert any("时长非法" in w for w in plan.warnings)


# ---------------------------------------------------------------------------
# 块内容组装
# ---------------------------------------------------------------------------
class TestChunkAssembly:
    def test_multi_scene_prompt_joined(self, planner):
        scenes = [
            _scene(1, prompt="shot A", duration=5),
            _scene(2, prompt="shot B", duration=5),
        ]
        plan = planner.plan(_script(scenes))
        assert plan.chunks[0].prompt == "shot A Then, shot B"

    def test_intent_contains_emotion_and_description(self, planner):
        scenes = [_scene(1, emotion="tension", description="深夜便利店")]
        plan = planner.plan(_script(scenes))
        assert "tension" in plan.chunks[0].intent
        assert "深夜便利店" in plan.chunks[0].intent

    def test_boundary_classified_shot(self, planner):
        scenes = [
            _scene(1, shot_type="中景", duration=10),
            _scene(2, shot_type="特写", duration=10),  # 10+10>14 → 第二块
        ]
        plan = planner.plan(_script(scenes))
        assert plan.chunks[1].boundary_before == "shot"

    def test_boundary_classified_scene_on_setting_change(self, planner):
        scenes = [
            _scene(1, description="便利店内", duration=10),
            _scene(2, description="天台上", duration=10),
        ]
        plan = planner.plan(_script(scenes))
        assert plan.chunks[1].boundary_before == "scene"

    def test_to_dict_serializable(self, planner):
        import json

        plan = planner.plan(_script([_scene(i) for i in range(1, 4)]))
        data = plan.to_dict()
        json.dumps(data, ensure_ascii=False)  # 必须可 JSON 序列化
        assert data["chunks"][0]["scene_ids"] == [1, 2]
        assert "coherence_to_prev" in data["chunks"][1]
        assert isinstance(data["shot_switches"], list)
