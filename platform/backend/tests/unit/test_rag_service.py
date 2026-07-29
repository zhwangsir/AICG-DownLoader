"""RAG 服务单元测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.services.rag_service import KnowledgeEntry, RAGService, rag_service


class TestKnowledgeEntry:
    def test_to_embed_text(self):
        entry = KnowledgeEntry(
            id="s1",
            category="style",
            title="写实电影感",
            content="cinematic realistic film look",
            tags=["film", "realistic"],
        )
        text = entry.to_embed_text()
        assert "写实电影感" in text
        assert "cinematic realistic film look" in text
        assert "film,realistic" in text

    def test_to_dict_roundtrip(self):
        entry = KnowledgeEntry(
            id="e1",
            category="example",
            title="示例",
            optimized_positive="positive prompt",
            optimized_negative="negative prompt",
            style="cyberpunk",
            recommended_loras=[{"filename": "a.safetensors", "weight": 0.7}],
        )
        data = entry.to_dict()
        assert data["id"] == "e1"
        assert data["optimized_positive"] == "positive prompt"
        assert data["style"] == "cyberpunk"
        assert data["recommended_loras"][0]["filename"] == "a.safetensors"


class TestRAGServiceLoading:
    def test_kb_dir_exists(self):
        from app.knowledge_base import KB_DIR

        assert KB_DIR.exists()
        assert any(KB_DIR.glob("*.json"))

    def test_raw_to_entry_extra_fields(self):
        raw = {
            "id": "test",
            "category": "style",
            "custom_field": "extra",
        }
        entry = RAGService._raw_to_entry(raw)
        assert entry.extra["custom_field"] == "extra"


class TestRAGServiceSearch:
    def test_search_with_in_memory_vectors(self, tmp_path, monkeypatch):
        """构造固定条目与向量，验证检索与 metadata 过滤逻辑。"""
        service = RAGService(cache_dir=tmp_path, kb_dir=tmp_path)
        service._entries = [
            KnowledgeEntry(id="s1", category="style", title="写实", content="realistic"),
            KnowledgeEntry(id="s2", category="style", title="动漫", content="anime"),
            KnowledgeEntry(id="shot1", category="shot", title="特写", content="close-up"),
        ]
        # 构造正交向量，便于控制相似度
        service._embeddings = np.array(
            [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float32,
        )
        service._initialized = True

        # 模拟 embed 返回查询向量；每次调用返回新的迭代器，避免首次检索后迭代器耗尽
        fake_model = MagicMock()
        fake_model.embed = MagicMock(side_effect=lambda _texts: iter([[1.0, 0.0, 0.0]]))
        service._embedding_model = fake_model

        results = service.search("realistic", category="style", top_k=2)
        assert len(results) == 2
        assert results[0]["id"] == "s1"

        # metadata 过滤：shot 类别应排除
        shot_results = service.search("close-up", category="shot", top_k=1)
        assert len(shot_results) == 1
        assert shot_results[0]["id"] == "shot1"


class TestRAGServiceOptimize:
    async def test_optimize_prompt_uses_llm_result(self, monkeypatch, tmp_path):
        service = RAGService(cache_dir=tmp_path, kb_dir=tmp_path)
        service._initialized = True
        service._entries = []

        fake_search = MagicMock(return_value=[])
        monkeypatch.setattr(service, "search", fake_search)

        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps({
                "optimized_positive": "cinematic close-up",
                "optimized_negative": "blurry",
                "style_notes": "电影感",
                "tags": ["电影感"],
            })))
        ]
        fake_client.chat.completions.create = AsyncMock(return_value=fake_response)

        with patch("app.services.rag_service.AsyncOpenAI", return_value=fake_client):
            result = await service.optimize_prompt("主角看手机", domain="image")

        assert result["optimized_positive"] == "cinematic close-up"
        assert result["optimized_negative"] == "blurry"
        assert result["retrieved_count"] == 0
        assert "lora_recommendations" in result

    async def test_optimize_prompt_fallback_on_llm_error(self, monkeypatch, tmp_path):
        service = RAGService(cache_dir=tmp_path, kb_dir=tmp_path)
        service._initialized = True
        service._entries = [
            KnowledgeEntry(
                id="s1",
                category="style",
                title="写实",
                content="realistic film look",
                tags=["realistic"],
            ),
        ]

        fake_search = MagicMock(return_value=[service._entries[0].to_dict()])
        monkeypatch.setattr(service, "search", fake_search)

        with patch("app.services.rag_service.AsyncOpenAI", side_effect=RuntimeError("LLM 失败")):
            result = await service.optimize_prompt("test", domain="image")

        assert result["fallback"] is True
        assert "test" in result["optimized_positive"]
        assert result["retrieved_count"] == 1
        assert result["lora_recommendations"] == []

    def test_collect_lora_recommendations_dedup(self):
        retrieved = [
            {
                "id": "t1",
                "category": "genre_trope",
                "recommended_loras": [
                    {"filename": "a.safetensors", "style_key": "a", "trigger_words": ["a"], "weight": 0.7},
                    {"filename": "b.safetensors", "style_key": "b", "trigger_words": ["b"], "weight": 0.8},
                ],
            },
            {
                "id": "t2",
                "category": "genre_trope",
                "recommended_loras": [
                    {"filename": "a.safetensors", "style_key": "a", "trigger_words": ["a"], "weight": 0.7},
                ],
            },
        ]
        result = RAGService._collect_lora_recommendations(retrieved)
        assert len(result) == 2
        assert result[0]["filename"] == "a.safetensors"
        assert result[1]["filename"] == "b.safetensors"


class TestRAGServiceGenreTrope:
    def test_search_filter_by_genre_trope_category(self, tmp_path):
        service = RAGService(cache_dir=tmp_path, kb_dir=tmp_path)
        service._entries = [
            KnowledgeEntry(id="t1", category="genre_trope", title="霸总对峙", content="CEO romance confrontation"),
            KnowledgeEntry(id="s1", category="style", title="写实", content="realistic"),
        ]
        service._embeddings = np.array(
            [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
            ],
            dtype=np.float32,
        )
        service._initialized = True
        fake_model = MagicMock()
        fake_model.embed = MagicMock(side_effect=lambda _texts: iter([[1.0, 0.0, 0.0]]))
        service._embedding_model = fake_model

        results = service.search("霸总", category="genre_trope", top_k=2)
        assert len(results) == 1
        assert results[0]["id"] == "t1"

    def test_build_system_prompt_includes_trope_section(self, tmp_path):
        service = RAGService(cache_dir=tmp_path, kb_dir=tmp_path)
        retrieved = [
            {
                "id": "t1",
                "category": "genre_trope",
                "title": "霸总对峙",
                "content": "CEO romance confrontation",
                "tags": ["霸总"],
            }
        ]
        prompt = service._build_system_prompt(retrieved)
        assert "[类型片叙事镜头模板]" in prompt
        assert "霸总对峙" in prompt

    def test_build_system_prompt_includes_lora_section(self, tmp_path):
        service = RAGService(cache_dir=tmp_path, kb_dir=tmp_path)
        retrieved = [
            {
                "id": "t1",
                "category": "genre_trope",
                "title": "霸总对峙",
                "content": "CEO romance confrontation",
                "tags": ["霸总"],
                "recommended_loras": [
                    {
                        "style_key": "realistic_film",
                        "filename": "Cinematic_Photography_style_v1.safetensors",
                        "trigger_words": ["Cinematic Photography style"],
                        "weight": 0.75,
                    }
                ],
            }
        ]
        prompt = service._build_system_prompt(retrieved)
        assert "[推荐 LoRA]" in prompt
        assert "Cinematic_Photography_style_v1.safetensors" in prompt
        assert "weight=0.75" in prompt

    async def test_optimize_prompt_retrieves_genre_trope(self, monkeypatch, tmp_path):
        service = RAGService(cache_dir=tmp_path, kb_dir=tmp_path)
        service._initialized = True
        service._entries = []

        trope_entry = {
            "id": "t1",
            "category": "genre_trope",
            "title": "霸总对峙",
            "content": "CEO romance confrontation",
            "tags": ["霸总"],
            "recommended_loras": [
                {
                    "style_key": "realistic_film",
                    "filename": "Cinematic_Photography_style_v1.safetensors",
                    "trigger_words": ["Cinematic Photography style"],
                    "weight": 0.75,
                }
            ],
        }

        def fake_search(query, category=None, domain=None, style=None, top_k=None):
            if category == "genre_trope":
                return [trope_entry]
            return []

        monkeypatch.setattr(service, "search", fake_search)

        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps({
                "optimized_positive": "cinematic CEO confrontation",
                "optimized_negative": "cartoon",
                "style_notes": "霸总对峙",
                "tags": ["霸总"],
            })))
        ]
        fake_client.chat.completions.create = AsyncMock(return_value=fake_response)

        with patch("app.services.rag_service.AsyncOpenAI", return_value=fake_client):
            result = await service.optimize_prompt("总裁把女主逼到墙角", domain="video")

        assert result["retrieved_count"] == 1
        assert result["optimized_positive"] == "cinematic CEO confrontation"
        assert len(result["lora_recommendations"]) == 1
        assert result["lora_recommendations"][0]["filename"] == "Cinematic_Photography_style_v1.safetensors"

    def test_fallback_output_handles_genre_trope(self, tmp_path):
        service = RAGService(cache_dir=tmp_path, kb_dir=tmp_path)
        retrieved = [
            {
                "id": "t1",
                "category": "genre_trope",
                "title": "霸总对峙",
                "content": "CEO romance confrontation",
                "tags": ["霸总"],
                "negative_terms": ["cartoon", "casual"],
                "recommended_loras": [
                    {
                        "style_key": "realistic_film",
                        "filename": "Cinematic_Photography_style_v1.safetensors",
                        "trigger_words": ["Cinematic Photography style"],
                        "weight": 0.75,
                    }
                ],
            }
        ]
        result = RAGService._fallback_output(retrieved, "总裁逼墙角")
        assert "CEO romance confrontation" in result["optimized_positive"]
        assert "cartoon" in result["optimized_negative"]
        assert "霸总对峙" in result["style_notes"]
        assert "霸总" in result["tags"]
        assert len(result["lora_recommendations"]) == 1
        assert result["lora_recommendations"][0]["weight"] == 0.75


class TestGlobalRAGService:
    def test_singleton_not_initialized(self):
        assert isinstance(rag_service, RAGService)
        # 未调用前不应主动初始化
        assert not rag_service._initialized
