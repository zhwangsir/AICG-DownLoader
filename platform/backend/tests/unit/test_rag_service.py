"""RAG 服务单元测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.services.rag_service import KnowledgeEntry, RAGService, rag_service


class TestModelLoadCircuitBreaker:
    """嵌入模型加载失败熔断：TTL 内不再重试，避免逐场景 ~130s 超时。"""

    def _make_service(self, tmp_path: Path) -> RAGService:
        return RAGService(cache_dir=tmp_path / "cache", kb_dir=tmp_path / "kb")

    def test_failure_circuit_breaks_subsequent_calls(self, tmp_path):
        service = self._make_service(tmp_path)
        with patch("app.services.rag_service.TextEmbedding", side_effect=OSError("Connection timed out")) as m_te:
            with pytest.raises(RuntimeError, match="无法加载嵌入模型"):
                service._init_model()
            assert m_te.call_count == 1
            # TTL 内第二次调用：熔断，不再尝试加载
            with pytest.raises(RuntimeError, match="熔断中"):
                service._init_model()
            assert m_te.call_count == 1

    def test_circuit_resets_after_ttl(self, tmp_path):
        service = self._make_service(tmp_path)
        with patch("app.services.rag_service.TextEmbedding", side_effect=OSError("timeout")):
            with pytest.raises(RuntimeError):
                service._init_model()
        # 模拟 TTL 已过
        service._model_load_failed_at -= 10000
        with patch("app.services.rag_service.TextEmbedding") as m_te:
            m_te.return_value = MagicMock()
            service._init_model()
            assert service._embedding_model is not None
            assert service._model_load_failed_at is None


class TestWarmUp:
    """M9.6 回归：缓存命中时 initialize() 提前返回不加载模型（_embedding_model 为 None），
    若预热仅调 _ensure_initialized，首个 search() 会在事件循环内同步下载模型，
    外网不可达时冻结全接口 ~130s（2026-08-04 core py-spy 实锤）。
    _warm_up 必须显式补加载模型，且 optimize_prompt 必须经 _warm_up 预热。"""

    def _make_service(self, tmp_path: Path) -> RAGService:
        return RAGService(cache_dir=tmp_path / "cache", kb_dir=tmp_path / "kb")

    def test_warm_up_loads_model_when_entries_exist(self, tmp_path):
        service = self._make_service(tmp_path)
        # 模拟缓存命中：initialize() 提前返回，模型未加载
        service._initialized = True
        service._entries = [
            KnowledgeEntry(id="s1", category="style", title="t", content="c")
        ]
        with patch("app.services.rag_service.TextEmbedding") as m_te:
            m_te.return_value = MagicMock()
            service._warm_up()
            assert m_te.call_count == 1
            assert service._embedding_model is not None

    def test_warm_up_skips_model_when_no_entries(self, tmp_path):
        service = self._make_service(tmp_path)
        service._initialized = True
        service._entries = []
        with patch("app.services.rag_service.TextEmbedding") as m_te:
            service._warm_up()
            assert m_te.call_count == 0

    async def test_optimize_prompt_warms_up_via_thread(self, monkeypatch, tmp_path):
        """回归守卫：optimize_prompt 必须调 _warm_up（而非仅 _ensure_initialized）。"""
        service = self._make_service(tmp_path)
        service._initialized = True
        service._entries = []
        called = {"warm_up": 0}

        def fake_warm_up() -> None:
            called["warm_up"] += 1

        monkeypatch.setattr(service, "_warm_up", fake_warm_up)
        monkeypatch.setattr(service, "search", MagicMock(return_value=[]))

        with patch("app.services.rag_service.get_shared_llm_client", side_effect=RuntimeError("no llm")):
            result = await service.optimize_prompt("测试提示词", domain="image")

        assert called["warm_up"] == 1
        assert result["fallback"] is True

    async def test_optimize_prompt_retrieves_via_thread(self, monkeypatch, tmp_path):
        """M9.8 回归守卫：六路 search() 含 fastembed ONNX 同步推理（CPU 每路 ~20-100ms），
        必须经 _retrieve_multi 由 asyncio.to_thread 放入线程执行；
        若回退为事件循环内直接调 search()，pipeline 逐场景调用会累积数百 ms 卡顿。"""
        service = self._make_service(tmp_path)
        service._initialized = True
        service._entries = []
        called = {"retrieve_multi": 0}
        search_calls: list[str] = []

        def fake_retrieve_multi(query, domain, style_hint):
            called["retrieve_multi"] += 1
            return ([], [], [], [], [], [])

        def fake_search(query, category=None, domain=None, style=None, top_k=None):
            search_calls.append(category or "")
            return []

        monkeypatch.setattr(service, "_retrieve_multi", fake_retrieve_multi)
        monkeypatch.setattr(service, "search", fake_search)

        with patch("app.services.rag_service.get_shared_llm_client", side_effect=RuntimeError("no llm")):
            await service.optimize_prompt("测试提示词", domain="image")

        # 必须走 _retrieve_multi 线程入口，而非事件循环内直接 search()
        assert called["retrieve_multi"] == 1
        assert search_calls == []

    def test_retrieve_multi_calls_six_categories(self, monkeypatch, tmp_path):
        """_retrieve_multi 必须覆盖六类知识库检索。"""
        service = self._make_service(tmp_path)
        categories: list[str] = []

        def fake_search(query, category=None, domain=None, style=None, top_k=None):
            categories.append(category or "")
            return []

        monkeypatch.setattr(service, "search", fake_search)
        service._retrieve_multi("q", "image", None)
        assert categories == ["style", "shot", "example", "negative", "method", "genre_trope"]


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

        with patch("app.services.rag_service.get_shared_llm_client", return_value=fake_client):
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

        with patch("app.services.rag_service.get_shared_llm_client", side_effect=RuntimeError("LLM 失败")):
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

        with patch("app.services.rag_service.get_shared_llm_client", return_value=fake_client):
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
