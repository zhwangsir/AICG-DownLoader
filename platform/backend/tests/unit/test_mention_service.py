"""M24.1: @角色提及解析服务单元测试 + POST /assets/resolve-mentions API 测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import CharacterAsset
from app.services import mention_service
from app.services.character_library import CharacterLibrary
from app.services.mention_service import (
    MAX_MENTIONS,
    _match_asset,
    auto_link_characters,
    extract_mentions,
    resolve_mentions,
)


@pytest.fixture
def lib(tmp_path):
    """隔离的角色资产库（临时目录，不触碰真实 output/character/library）。"""
    library = CharacterLibrary(library_dir=tmp_path)
    library.save(CharacterAsset(
        character_id="c001",
        name="云曦",
        reference_images={"front": "http://x/yunxi.png", "side": "http://x/yunxi_s.png"},
        appearance_lock="yunxi, silver hair, blue eyes",
        locked=True,
    ))
    library.save(CharacterAsset(
        character_id="c002",
        name="林远",
        reference_images={"front": "http://x/linyuan.png"},
        appearance_lock="linyuan, yellow uniform, dark skin",
        locked=True,
    ))
    library.save(CharacterAsset(
        character_id="c003",
        name="小云曦",
        reference_images={"front": "http://x/xiaoyunxi.png"},
        appearance_lock="should-not-appear",  # 未锁定，get_appearance_lock 返回空
        locked=False,
    ))
    library.save(CharacterAsset(
        character_id="c004",
        name="Mugi",
        reference_images={"front": "http://x/mugi.png"},
        appearance_lock="mugi, twintails",
        locked=True,
    ))
    return library


class TestExtractMentions:
    def test_basic(self):
        assert extract_mentions("@云曦 和 @林远") == ["云曦", "林远"]

    def test_dedup_keeps_first_occurrence_order(self):
        assert extract_mentions("@林远 @云曦 @林远") == ["林远", "云曦"]

    def test_no_mention(self):
        assert extract_mentions("云曦 和 林远") == []

    def test_empty_and_none_like(self):
        assert extract_mentions("") == []

    def test_hyphen_underscore_digits(self):
        assert extract_mentions("@Char-01 @char_02") == ["Char-01", "char_02"]

    def test_stops_at_punctuation(self):
        assert extract_mentions("@云曦，你好！@林远。") == ["云曦", "林远"]


class TestMatchAsset:
    def test_exact(self, lib):
        asset, mt = _match_asset("云曦", lib.list())
        assert mt == "exact" and asset.character_id == "c001"

    def test_case_insensitive(self, lib):
        asset, mt = _match_asset("mugi", lib.list())
        assert mt == "ci" and asset.character_id == "c004"

    def test_fuzzy_prefers_closest_length(self, lib):
        # 「云」与「云曦」长度差 1、与「小云曦」差 2 → 选「云曦」
        asset, mt = _match_asset("云", lib.list())
        assert mt == "fuzzy" and asset.character_id == "c001"

    def test_no_match(self, lib):
        asset, mt = _match_asset("外星人", lib.list())
        assert asset is None and mt == ""


class TestResolveMentions:
    def test_full_resolve(self, lib):
        result = resolve_mentions("@云曦 和 @林远 对话", library=lib)
        assert result["text"] == "@云曦 和 @林远 对话"
        assert len(result["mentions"]) == 2
        m0, m1 = result["mentions"]
        assert m0["matched"] is True and m0["match_type"] == "exact"
        assert m0["character_id"] == "c001" and m0["name"] == "云曦"
        assert m0["reference_front"] == "http://x/yunxi.png"
        assert m0["appearance_lock"] == "yunxi, silver hair, blue eyes"
        assert m1["character_id"] == "c002"
        assert result["unmatched"] == []
        assert result["reference_images"] == ["http://x/yunxi.png", "http://x/linyuan.png"]
        # expanded_text：锁定角色的外观锁定卡拼入前缀段，原文保留在后
        assert "外观锁定（@云曦）: yunxi, silver hair, blue eyes" in result["expanded_text"]
        assert "外观锁定（@林远）: linyuan, yellow uniform, dark skin" in result["expanded_text"]
        assert result["expanded_text"].endswith("@云曦 和 @林远 对话")

    def test_unlocked_asset_has_no_lock(self, lib):
        result = resolve_mentions("@小云曦", library=lib)
        m = result["mentions"][0]
        assert m["matched"] is True and m["locked"] is False
        assert m["appearance_lock"] == ""  # 未锁定不暴露锁定卡
        assert m["reference_front"] == "http://x/xiaoyunxi.png"
        # 无锁定卡 → expanded_text 保持原文
        assert result["expanded_text"] == "@小云曦"

    def test_unmatched_listed(self, lib):
        result = resolve_mentions("@云曦 和 @外星人", library=lib)
        assert result["mentions"][1]["matched"] is False
        assert result["unmatched"] == ["外星人"]
        # 未匹配角色不生成锁定卡前缀（原文中的 @外星人 字样保留属正常）
        assert "外观锁定（@外星人）" not in result["expanded_text"]

    def test_reference_images_deduped(self, lib):
        result = resolve_mentions("@云曦 @云曦", library=lib)
        assert result["reference_images"] == ["http://x/yunxi.png"]

    def test_no_mentions_passthrough(self, lib):
        result = resolve_mentions("普通提示词", library=lib)
        assert result["mentions"] == [] and result["unmatched"] == []
        assert result["expanded_text"] == "普通提示词"

    def test_exceeds_max_mentions_raises(self, lib):
        text = " ".join(f"@角色{i}" for i in range(MAX_MENTIONS + 1))
        with pytest.raises(ValueError, match="提及数量超限"):
            resolve_mentions(text, library=lib)


class TestAutoLinkCharacters:
    """M25.2: AutoLink 自动资产匹配 — 自然语言文本扫描角色名。"""

    def test_exact_names_in_text(self, lib):
        hits = auto_link_characters("云曦转身看向林远，两人沉默对视", library=lib)
        assert [a.character_id for a in hits] == ["c001", "c002"]

    def test_order_by_first_occurrence(self, lib):
        hits = auto_link_characters("林远追上云曦", library=lib)
        assert [a.character_id for a in hits] == ["c002", "c001"]

    def test_case_insensitive_fallback(self, lib):
        hits = auto_link_characters("mugi 摘下耳机", library=lib)
        assert [a.character_id for a in hits] == ["c004"]

    def test_no_fuzzy_matching(self, lib):
        # 「云」单独出现不得命中「云曦」（自动挂接宁缺毋滥）
        assert auto_link_characters("天上云很多", library=lib) == []

    def test_no_match(self, lib):
        assert auto_link_characters("外星人走进便利店", library=lib) == []

    def test_empty_text(self, lib):
        assert auto_link_characters("", library=lib) == []
        assert auto_link_characters("   ", library=lib) == []

    def test_same_position_longest_name_wins(self, tmp_path):
        library = CharacterLibrary(library_dir=tmp_path)
        library.save(CharacterAsset(character_id="c010", name="林", locked=True))
        library.save(CharacterAsset(character_id="c011", name="林远", locked=True))
        hits = auto_link_characters("林远走出门", library=library)
        assert [a.character_id for a in hits] == ["c011"]


class TestResolveMentionsAPI:
    """POST /api/drama/assets/resolve-mentions 接口测试。"""

    @pytest.fixture
    def client(self, lib, monkeypatch):
        # 路由内部每次调用 mention_service.resolve_mentions → 默认取模块级全局单例，
        # monkeypatch 为隔离实例，避免读取真实 output/character/library
        monkeypatch.setattr(mention_service, "character_library", lib)
        return TestClient(app)

    def test_success(self, client):
        resp = client.post("/api/drama/assets/resolve-mentions", json={"text": "@云曦 看向 @林远"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert [m["character_id"] for m in data["mentions"]] == ["c001", "c002"]
        assert data["reference_images"] == ["http://x/yunxi.png", "http://x/linyuan.png"]
        assert "外观锁定（@云曦）" in data["expanded_text"]

    def test_unmatched_200(self, client):
        resp = client.post("/api/drama/assets/resolve-mentions", json={"text": "@不存在角色"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["mentions"][0]["matched"] is False
        assert data["unmatched"] == ["不存在角色"]

    def test_empty_text_422(self, client):
        resp = client.post("/api/drama/assets/resolve-mentions", json={"text": ""})
        assert resp.status_code == 422

    def test_missing_field_422(self, client):
        resp = client.post("/api/drama/assets/resolve-mentions", json={})
        assert resp.status_code == 422

    def test_over_limit_400(self, client):
        text = " ".join(f"@r{i}" for i in range(MAX_MENTIONS + 1))
        resp = client.post("/api/drama/assets/resolve-mentions", json={"text": text})
        assert resp.status_code == 400
        assert "提及数量超限" in resp.json()["detail"]
