"""M25.3 画布工作流模板库 —— GET /api/drama/pipeline/templates 单元测试。

覆盖：
- 端点返回结构（templates/total/categories 字段齐全，条目含 id/title/category/tags/summary/content）
- category 过滤（genre_trope 命中 / 不存在类别返回空）
- KB 缺失/加载失败兜底（get_templates 抛异常 → 200 + 空列表，不 5xx）
- RAGService.get_templates 服务层（默认类别 / 显式类别 / 无命中）
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.rag_service import KnowledgeEntry, RAGService


@pytest.fixture
def client():
    """返回已配置好的 TestClient。"""
    return TestClient(app)


# 模拟 genre_tropes.json 中的两条模板条目（to_dict() 输出形态）
TROPE_A = {
    "id": "trope_boss_romance_confrontation",
    "category": "genre_trope",
    "domain": ["image", "video"],
    "lang": "zh",
    "title": "霸总对峙/壁咚",
    "content": "CEO romance confrontation: tall male lead in tailored suit, " * 15,  # 超长验证截断
    "tags": ["霸总", "对峙", "壁咚"],
    "negative_terms": ["casual"],
    "style_intensity": 0.75,
    "model_target": ["SDXL"],
    "source": "built-in",
    "parent_id": None,
}
TROPE_B = {
    "id": "trope_sweet_cafe_date",
    "category": "genre_trope",
    "domain": ["image", "video"],
    "lang": "zh",
    "title": "甜宠咖啡馆约会",
    "content": "sweet romance cafe date",
    "tags": ["甜宠", "约会"],
    "negative_terms": [],
    "style_intensity": 0.55,
    "model_target": ["SDXL"],
    "source": "built-in",
    "parent_id": None,
}


class TestPipelineTemplatesRoute:
    """GET /api/drama/pipeline/templates 路由层。"""

    def test_returns_structure(self, client):
        """返回结构：templates/total/categories 齐全，条目字段完整。"""
        with patch(
            "app.routers.drama.rag_service.get_templates",
            return_value=[TROPE_A, TROPE_B],
        ) as mock_get:
            response = client.get("/api/drama/pipeline/templates")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["categories"] == ["genre_trope"]
        assert len(data["templates"]) == 2
        first = data["templates"][0]
        assert first["id"] == "trope_boss_romance_confrontation"
        assert first["title"] == "霸总对峙/壁咚"
        assert first["category"] == "genre_trope"
        assert first["tags"] == ["霸总", "对峙", "壁咚"]
        assert first["content"].startswith("CEO romance confrontation")
        # 摘要截断 200 字符并追加省略号
        assert len(first["summary"]) == 201
        assert first["summary"].endswith("…")
        # 默认不带 category 参数 → 服务层收到 None
        mock_get.assert_called_once_with(category=None)

    def test_short_content_summary_not_truncated(self, client):
        """内容不足 200 字符时摘要原样返回，不追加省略号。"""
        with patch(
            "app.routers.drama.rag_service.get_templates",
            return_value=[TROPE_B],
        ):
            response = client.get("/api/drama/pipeline/templates")
        assert response.status_code == 200
        item = response.json()["templates"][0]
        assert item["summary"] == "sweet romance cafe date"

    def test_category_filter(self, client):
        """category 查询参数透传到服务层。"""
        with patch(
            "app.routers.drama.rag_service.get_templates",
            return_value=[TROPE_A],
        ) as mock_get:
            response = client.get("/api/drama/pipeline/templates?category=genre_trope")
        assert response.status_code == 200
        mock_get.assert_called_once_with(category="genre_trope")
        assert response.json()["total"] == 1

    def test_unknown_category_returns_empty(self, client):
        """不存在类别 → 200 + 空列表 + 空 categories。"""
        with patch(
            "app.routers.drama.rag_service.get_templates",
            return_value=[],
        ):
            response = client.get("/api/drama/pipeline/templates?category=nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["templates"] == []
        assert data["total"] == 0
        assert data["categories"] == []

    def test_kb_missing_fallback_empty_list(self, client):
        """KB 缺失/加载失败：服务层抛异常 → 200 + 空列表兜底（不 5xx）。"""
        with patch(
            "app.routers.drama.rag_service.get_templates",
            side_effect=RuntimeError("knowledge_base 目录不可读"),
        ):
            response = client.get("/api/drama/pipeline/templates")
        assert response.status_code == 200
        data = response.json()
        assert data["templates"] == []
        assert data["total"] == 0
        assert data["categories"] == []


class TestRagServiceGetTemplates:
    """RAGService.get_templates 服务层（绕过嵌入模型，直接注入条目）。"""

    def _service_with_entries(self, entries: list[KnowledgeEntry]) -> RAGService:
        service = RAGService.__new__(RAGService)
        service._entries = entries
        service._embeddings = None
        service._embedding_model = None
        service._initialized = True  # 跳过 initialize（避免加载嵌入模型）
        service._model_load_failed_at = None
        return service

    def _trope(self, entry_id: str, category: str = "genre_trope") -> KnowledgeEntry:
        return KnowledgeEntry(
            id=entry_id,
            category=category,
            title=f"标题-{entry_id}",
            content=f"内容-{entry_id}",
            tags=["标签"],
        )

    def test_default_category_genre_trope(self):
        """不传 category 时默认过滤 genre_trope。"""
        service = self._service_with_entries(
            [self._trope("t1"), self._trope("s1", category="style")]
        )
        result = service.get_templates()
        assert [r["id"] for r in result] == ["t1"]

    def test_explicit_category(self):
        """显式 category 过滤命中。"""
        service = self._service_with_entries(
            [self._trope("t1"), self._trope("t2"), self._trope("s1", category="style")]
        )
        result = service.get_templates(category="genre_trope")
        assert [r["id"] for r in result] == ["t1", "t2"]

    def test_no_match_returns_empty(self):
        """无命中类别 → 空列表。"""
        service = self._service_with_entries([self._trope("s1", category="style")])
        assert service.get_templates(category="genre_trope") == []

    def test_empty_kb_returns_empty(self):
        """KB 无任何条目 → 空列表。"""
        service = self._service_with_entries([])
        assert service.get_templates() == []

    def test_result_contains_expected_fields(self):
        """返回字段与知识库条目 schema 一致（含 tags/content/category）。"""
        service = self._service_with_entries([self._trope("t1")])
        item = service.get_templates()[0]
        assert item["id"] == "t1"
        assert item["category"] == "genre_trope"
        assert item["title"] == "标题-t1"
        assert item["content"] == "内容-t1"
        assert item["tags"] == ["标签"]
