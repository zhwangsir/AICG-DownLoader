"""数据模型 schema 单元测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas import (
    AgentResponse,
    Character,
    CharacterCard,
    CharacterRequest,
    DialogueLine,
    Scene,
    Script,
    ScriptRequest,
    StoryboardRequest,
    StoryboardResult,
    SubtitleRequest,
    SubtitleResult,
    VideoRequest,
    VideoResult,
    VoiceRequest,
    VoiceResult,
)


class TestScriptRequest:
    def test_defaults(self):
        req = ScriptRequest(premise="测试创意")
        assert req.premise == "测试创意"
        assert req.genre == "都市悬疑"
        assert req.episodes == 1
        assert req.scenes_per_episode == 5

    def test_episodes_validation(self):
        with pytest.raises(ValidationError):
            ScriptRequest(premise="x", episodes=0)
        with pytest.raises(ValidationError):
            ScriptRequest(premise="x", episodes=101)

    def test_scenes_per_episode_validation(self):
        with pytest.raises(ValidationError):
            ScriptRequest(premise="x", scenes_per_episode=0)
        with pytest.raises(ValidationError):
            ScriptRequest(premise="x", scenes_per_episode=31)


class TestScript:
    def test_defaults(self):
        script = Script(title="测试剧")
        assert script.title == "测试剧"
        assert script.aspect_ratio == "9:16"
        assert script.total_episodes == 1
        assert script.characters == []
        assert script.scenes == []

    def test_with_character_and_scene(self, sample_character, sample_scene):
        script = Script(
            title="测试",
            characters=[sample_character],
            scenes=[sample_scene],
        )
        assert len(script.characters) == 1
        assert len(script.scenes) == 1
        assert script.scenes[0].scene_id == 1


class TestCharacterModels:
    def test_character_defaults(self):
        char = Character(character_id="char_001", name="测试")
        assert char.role == ""
        assert char.age is None
        assert char.description == ""

    def test_character_request_defaults(self, sample_character):
        req = CharacterRequest(character=sample_character)
        assert req.style == "写实电影感"
        assert req.consistency_level == "L3"

    def test_character_card(self):
        card = CharacterCard(
            character_id="char_001",
            name="测试",
            reference_images={"front": "http://x/a.png"},
        )
        assert card.anchor_points == 200
        assert card.consistency_level == "L2"


class TestScene:
    def test_defaults(self):
        scene = Scene(scene_id=1)
        assert scene.shot_type == "中景"
        assert scene.emotion == "neutral"
        assert scene.duration_seconds == 5
        assert scene.camera_movement == "static"


class TestStoryboardModels:
    def test_request_defaults(self, sample_scene):
        req = StoryboardRequest(scene=sample_scene)
        assert req.style == "写实电影感"
        assert req.characters == []

    def test_result(self):
        result = StoryboardResult(scene_id=1, image_url="http://x/sb.png")
        assert result.prompt_used == ""


class TestVideoModels:
    def test_request_defaults(self):
        req = VideoRequest(scene_id=1, image_url="http://x/i.png")
        assert req.prompt == ""
        assert req.negative_prompt == ""
        assert req.duration_seconds == 3

    def test_result(self):
        result = VideoResult(scene_id=1, video_url="http://x/v.mp4")
        assert result.duration_seconds == 3


class TestVoiceModels:
    def test_dialogue_line_defaults(self):
        line = DialogueLine(text="你好")
        assert line.character_name == ""
        assert line.rate == "+0%"

    def test_voice_request(self):
        req = VoiceRequest(
            scene_id=1,
            dialogues=[DialogueLine(text="你好")],
        )
        assert len(req.dialogues) == 1

    def test_voice_result_defaults(self):
        result = VoiceResult(scene_id=1)
        assert result.audio_urls == []
        assert result.total_lines == 0


class TestSubtitleModels:
    def test_request_defaults(self):
        req = SubtitleRequest(scene_id=1, audio_url="http://x/a.mp3")
        assert req.language == "zh"

    def test_result_defaults(self):
        result = SubtitleResult(scene_id=1)
        assert result.srt_content == ""
        assert result.language == "zh"


class TestAgentResponse:
    def test_defaults(self):
        resp = AgentResponse(success=True)
        assert resp.data is None
        assert resp.error is None
        assert resp.elapsed_seconds == 0.0

    def test_with_data(self):
        resp = AgentResponse(success=True, data={"x": 1}, elapsed_seconds=1.5)
        assert resp.data == {"x": 1}
        assert resp.elapsed_seconds == 1.5
