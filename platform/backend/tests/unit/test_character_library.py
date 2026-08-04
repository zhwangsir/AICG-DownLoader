"""角色资产库单元测试。

覆盖：
- CRUD 往返与局部更新白名单
- 定妆照生成后自动登记（register_from_card）
- 外观锁定卡注入分镜 prompt（storyboard_agent）
- character_agent.execute 自动登记闭环
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.character_agent import CharacterAgent
from app.agents.storyboard_agent import StoryboardAgent
from app.models.schemas import Character, CharacterAsset, CharacterRequest, Scene
from app.services.character_library import APPEARANCE_LOCK_MAX_CHARS, CharacterLibrary


@pytest.fixture
def library(tmp_path):
    """独立的临时资产库实例。"""
    return CharacterLibrary(library_dir=tmp_path / "library")


@pytest.fixture
def sample_char() -> Character:
    return Character(
        character_id="char_001",
        name="林远",
        role="主角",
        age=26,
        description="冷峻的都市侦探，黑色风衣",
        personality="沉稳",
    )


class TestCharacterLibraryCRUD:
    def test_save_get_roundtrip(self, library, sample_char):
        asset = CharacterAsset(character_id=sample_char.character_id, name=sample_char.name)
        library.save(asset)
        loaded = library.get(sample_char.character_id)
        assert loaded is not None
        assert loaded.name == "林远"
        assert loaded.created_at > 0
        assert loaded.updated_at >= loaded.created_at

    def test_list_sorted_by_updated_desc(self, library):
        library.save(CharacterAsset(character_id="a", name="A"))
        library.save(CharacterAsset(character_id="b", name="B"))
        library.save(CharacterAsset(character_id="a", name="A2"))  # 再更新 a
        names = [a.name for a in library.list()]
        assert names[0] == "A2"

    def test_delete(self, library):
        library.save(CharacterAsset(character_id="x", name="X"))
        assert library.delete("x") is True
        assert library.get("x") is None
        assert library.delete("x") is False

    def test_update_whitelist_only(self, library):
        library.save(CharacterAsset(character_id="c1", name="旧名", appearance_lock="lock prompt"))
        updated = library.update("c1", name="新名", locked=False, reference_images={"front": "hack"}, used_prompts={"x": "y"})
        assert updated.name == "新名"
        assert updated.locked is False
        assert updated.character_id == "c1"
        # 白名单外字段不生效
        assert updated.reference_images == {}
        assert updated.used_prompts == {}

    def test_update_missing_returns_none(self, library):
        assert library.update("nope", name="x") is None

    def test_invalid_character_id_rejected(self, library):
        with pytest.raises(ValueError):
            library.save(CharacterAsset(character_id="../evil", name="X"))

    def test_corrupted_file_tolerated(self, library):
        (library._dir / "bad.json").write_text("not json", encoding="utf-8")
        library.save(CharacterAsset(character_id="good", name="G"))
        assert library.get("good") is not None  # 损坏文件不影响其他资产


class TestRegisterFromCard:
    def test_default_locked_with_appearance_lock(self, library, sample_char):
        long_prompt = "1boy, solo, black trench coat, sharp eyes" + " x" * 500
        asset = library.register_from_card(
            character=sample_char,
            reference_images={"front": "http://x/front.png"},
            used_prompts={"positive_prompt": long_prompt, "negative_prompt": "neg"},
        )
        assert asset.locked is True
        assert len(asset.appearance_lock) <= APPEARANCE_LOCK_MAX_CHARS
        assert asset.appearance_lock.startswith("1boy, solo")
        assert asset.reference_images["front"] == "http://x/front.png"

    def test_reregister_preserves_manual_appearance_lock(self, library, sample_char):
        asset = library.register_from_card(sample_char, {}, {"positive_prompt": "原始 prompt"})
        library.update(sample_char.character_id, appearance_lock="用户精修外观卡")
        asset2 = library.register_from_card(sample_char, {"front": "new.png"}, {"positive_prompt": "新 prompt"})
        # 重新生成定妆照不覆盖用户精修的外观锁定卡，但参考图更新
        assert asset2.appearance_lock == "用户精修外观卡"
        assert asset2.reference_images["front"] == "new.png"

    def test_reregister_preserves_unlock_state(self, library, sample_char):
        library.register_from_card(sample_char, {}, {"positive_prompt": "p"})
        library.update(sample_char.character_id, locked=False)
        asset = library.register_from_card(sample_char, {}, {"positive_prompt": "p2"})
        assert asset.locked is False

    def test_created_at_preserved_on_reregister(self, library, sample_char):
        a1 = library.register_from_card(sample_char, {}, {"positive_prompt": "p"})
        a2 = library.register_from_card(sample_char, {}, {"positive_prompt": "p2"})
        assert a2.created_at == a1.created_at


class TestAppearanceLockResolution:
    def test_locked_character_resolved(self, library, sample_char):
        library.register_from_card(sample_char, {"front": "f.png"}, {"positive_prompt": "外观关键词A"})
        resolved = library.resolve_characters([sample_char])
        assert resolved[0]["appearance_lock"] == "外观关键词A"
        assert resolved[0]["reference_front"] == "f.png"

    def test_unlocked_character_no_lock(self, library, sample_char):
        library.register_from_card(sample_char, {}, {"positive_prompt": "外观关键词A"})
        library.update(sample_char.character_id, locked=False)
        resolved = library.resolve_characters([sample_char])
        assert resolved[0]["appearance_lock"] == ""

    def test_unregistered_character_fallback(self, library, sample_char):
        resolved = library.resolve_characters([sample_char])
        assert resolved[0]["description"] == sample_char.description
        assert resolved[0]["appearance_lock"] == ""

    def test_locked_character_prefers_library_description(self, library, sample_char):
        library.register_from_card(sample_char, {}, {"positive_prompt": "p"})
        library.update(sample_char.character_id, description="资产库精修描述")
        resolved = library.resolve_characters([sample_char])
        assert resolved[0]["description"] == "资产库精修描述"


class TestStoryboardAppearanceInjection:
    """分镜 prompt 必须注入锁定角色的外观锁定卡。"""

    async def test_locked_character_injected(self, library, sample_char, mock_call_llm):
        library.register_from_card(sample_char, {}, {"positive_prompt": "black trench coat, sharp eyes"})
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})
        agent = StoryboardAgent()
        scene = Scene(scene_id=1, description="主角盯着手机屏幕")

        with patch("app.agents.storyboard_agent.character_library", library):
            await agent._generate_prompts(scene, [sample_char], "写实电影感")

        user_msg = next(m["content"] for m in mock_call_llm.call_args.kwargs["messages"] if m["role"] == "user")
        assert "外观锁定" in user_msg
        assert "black trench coat, sharp eyes" in user_msg

    async def test_unlocked_character_not_injected(self, library, sample_char, mock_call_llm):
        library.register_from_card(sample_char, {}, {"positive_prompt": "black trench coat"})
        library.update(sample_char.character_id, locked=False)
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})
        agent = StoryboardAgent()
        scene = Scene(scene_id=1, description="主角盯着手机屏幕")

        with patch("app.agents.storyboard_agent.character_library", library):
            await agent._generate_prompts(scene, [sample_char], "写实电影感")

        user_msg = next(m["content"] for m in mock_call_llm.call_args.kwargs["messages"] if m["role"] == "user")
        assert "外观锁定" not in user_msg
        # 角色基础信息仍在
        assert "林远" in user_msg

    async def test_library_failure_falls_back(self, sample_char, mock_call_llm):
        """资产库异常时回退请求内角色描述，不阻断分镜生成。"""
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})
        agent = StoryboardAgent()
        scene = Scene(scene_id=1, description="主角盯着手机屏幕")
        broken = CharacterLibrary.__new__(CharacterLibrary)
        broken.resolve_characters = lambda chars: (_ for _ in ()).throw(RuntimeError("disk error"))

        with patch("app.agents.storyboard_agent.character_library", broken):
            await agent._generate_prompts(scene, [sample_char], "写实电影感")

        user_msg = next(m["content"] for m in mock_call_llm.call_args.kwargs["messages"] if m["role"] == "user")
        assert "林远" in user_msg


class TestCharacterAgentAutoRegister:
    """角色生成成功后自动登记资产库。"""

    async def test_execute_registers_asset(
        self,
        library,
        sample_char,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        mock_call_llm.return_value = json.dumps(
            {"front_view_prompt": "fp", "side_view_prompt": "sp", "closeup_prompt": "cp", "negative_prompt": "neg"}
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "c.png", "subfolder": "", "type": "output"}]}
        }
        agent = CharacterAgent()

        with patch("app.agents.character_agent.character_library", library):
            response = await agent.execute(CharacterRequest(character=sample_char))

        assert response.success is True
        asset = library.get(sample_char.character_id)
        assert asset is not None
        assert asset.locked is True
        assert asset.appearance_lock == "fp"
        assert "front" in asset.reference_images

    async def test_register_failure_does_not_break_generation(
        self,
        sample_char,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        mock_call_llm.return_value = json.dumps(
            {"front_view_prompt": "fp", "side_view_prompt": "sp", "closeup_prompt": "cp", "negative_prompt": "neg"}
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "c.png", "subfolder": "", "type": "output"}]}
        }
        agent = CharacterAgent()
        broken = CharacterLibrary.__new__(CharacterLibrary)
        broken.register_from_card = lambda **kw: (_ for _ in ()).throw(RuntimeError("disk full"))

        with patch("app.agents.character_agent.character_library", broken):
            response = await agent.execute(CharacterRequest(character=sample_char))

        assert response.success is True  # 登记失败不影响生成
