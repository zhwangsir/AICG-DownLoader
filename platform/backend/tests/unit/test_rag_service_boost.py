"""RAG 服务覆盖率补充测试（只使用 mock，离线可跑）。

覆盖目标：rag_service.py 中既有测试未触达的分支——
嵌入文本 style 融合、缓存路径/哈希、KB 文件加载、缓存读写、initialize 全流程、
_ensure_initialized、search 空结果与 metadata 过滤、get_styles、
系统提示词各分区、style_hint/extra_instruction 传参、markdown 剥离、
json_repair 回退与解析失败兜底、_fallback_output 各分类分支。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.services.rag_service import KnowledgeEntry, RAGService


def _make_service(tmp_path: Path) -> RAGService:
    return RAGService(cache_dir=tmp_path / "cache", kb_dir=tmp_path / "kb")


def _write_kb(kb_dir: Path, name: str, entries: list[dict]) -> Path:
    kb_dir.mkdir(parents=True, exist_ok=True)
    path = kb_dir / name
    path.write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")
    return path


class TestToEmbedTextWithStyle:
    """line 67: style 非空时应并入嵌入文本。"""

    def test_style_appended(self):
        entry = KnowledgeEntry(id="s1", category="style", title="t", content="c", style="cyberpunk")
        text = entry.to_embed_text()
        assert "cyberpunk" in text
        # style 位于最后一行
        assert text.splitlines()[-1] == "cyberpunk"

    def test_no_style_not_appended(self):
        entry = KnowledgeEntry(id="s1", category="style", title="t", content="c")
        assert "cyberpunk" not in entry.to_embed_text()


class TestCachePathAndHash:
    """lines 143-144, 148-151: _cache_path / _compute_kb_hash。"""

    def test_cache_path_contains_model_and_hash(self, tmp_path):
        service = _make_service(tmp_path)
        _write_kb(service.kb_dir, "styles.json", [{"id": "s1", "category": "style"}])
        path = service._cache_path()
        assert path.parent == service.cache_dir
        assert "BAAI_bge-small-zh-v1.5" in path.name
        assert path.name.startswith("rag_cache_")
        assert path.name.endswith(".json")

    def test_hash_changes_with_kb_content(self, tmp_path):
        service = _make_service(tmp_path)
        _write_kb(service.kb_dir, "a.json", [{"id": "s1"}])
        hash1 = service._compute_kb_hash()
        path1 = service._cache_path()
        _write_kb(service.kb_dir, "a.json", [{"id": "s2"}])
        hash2 = service._compute_kb_hash()
        assert hash1 != hash2
        assert service._cache_path() != path1
        assert len(hash1) == 16

    def test_hash_empty_kb_dir(self, tmp_path):
        service = _make_service(tmp_path)
        # kb 目录不存在/为空时哈希仍稳定可算
        assert len(service._compute_kb_hash()) == 16


class TestLoadKbFiles:
    """lines 155-166: _load_kb_files 正常加载与坏文件告警跳过。"""

    def test_loads_entries_from_json_files(self, tmp_path):
        service = _make_service(tmp_path)
        _write_kb(service.kb_dir, "styles.json", [
            {"id": "s1", "category": "style", "title": "写实"},
            {"id": "s2", "category": "style", "title": "动漫"},
        ])
        _write_kb(service.kb_dir, "shots.json", [{"id": "shot1", "category": "shot"}])
        entries = service._load_kb_files()
        assert {e.id for e in entries} == {"s1", "s2", "shot1"}
        assert all(isinstance(e, KnowledgeEntry) for e in entries)

    def test_bad_json_file_skipped_with_warning(self, tmp_path):
        service = _make_service(tmp_path)
        _write_kb(service.kb_dir, "good.json", [{"id": "s1"}])
        service.kb_dir.joinpath("bad.json").write_text("{not valid json", encoding="utf-8")
        entries = service._load_kb_files()
        assert [e.id for e in entries] == ["s1"]

    def test_missing_entries_key_yields_nothing(self, tmp_path):
        service = _make_service(tmp_path)
        service.kb_dir.mkdir(parents=True)
        service.kb_dir.joinpath("empty.json").write_text("{}", encoding="utf-8")
        assert service._load_kb_files() == []


class TestLoadCache:
    """lines 200-214: _load_cache 四种路径。"""

    def _seed_cache(self, service: RAGService, entries: list[dict], embeddings: list) -> Path:
        cache_path = service._cache_path()
        cache_path.write_text(
            json.dumps({"model": service.model_name, "version": "v1",
                        "entries": entries, "embeddings": embeddings},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        return cache_path

    def test_returns_none_when_file_missing(self, tmp_path):
        service = _make_service(tmp_path)
        _write_kb(service.kb_dir, "a.json", [{"id": "s1"}])
        assert service._load_cache() is None

    def test_loads_valid_cache(self, tmp_path):
        service = _make_service(tmp_path)
        _write_kb(service.kb_dir, "a.json", [{"id": "s1"}])
        self._seed_cache(service, [{"id": "s1", "category": "style", "title": "写实"}],
                         [[1.0, 0.0]])
        result = service._load_cache()
        assert result is not None
        entries, embeddings = result
        assert entries[0].id == "s1"
        assert embeddings.shape == (1, 2)
        assert embeddings.dtype == np.float32

    def test_size_mismatch_rebuilds(self, tmp_path):
        service = _make_service(tmp_path)
        _write_kb(service.kb_dir, "a.json", [{"id": "s1"}])
        # 2 条目但仅 1 条向量 → 缓存作废
        self._seed_cache(service, [{"id": "s1"}, {"id": "s2"}], [[1.0, 0.0]])
        assert service._load_cache() is None

    def test_corrupt_cache_returns_none(self, tmp_path):
        service = _make_service(tmp_path)
        _write_kb(service.kb_dir, "a.json", [{"id": "s1"}])
        service._cache_path().write_text("{broken", encoding="utf-8")
        assert service._load_cache() is None


class TestSaveCache:
    """lines 218-235: _save_cache 成功与写盘失败。"""

    def test_saves_and_roundtrips(self, tmp_path):
        service = _make_service(tmp_path)
        _write_kb(service.kb_dir, "a.json", [{"id": "s1"}])
        entries = [KnowledgeEntry(id="s1", category="style", title="写实", style="film")]
        embeddings = np.array([[1.0, 0.0]], dtype=np.float32)
        service._save_cache(entries, embeddings)
        cache_path = service._cache_path()
        assert cache_path.exists()
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        assert payload["model"] == service.model_name
        assert payload["version"] == "v1"
        assert payload["entries"][0]["id"] == "s1"
        assert payload["entries"][0]["style"] == "film"
        # 能被 _load_cache 读回
        loaded = service._load_cache()
        assert loaded is not None and loaded[0][0].id == "s1"

    def test_write_failure_swallowed(self, tmp_path, monkeypatch):
        service = _make_service(tmp_path)
        _write_kb(service.kb_dir, "a.json", [{"id": "s1"}])

        def _boom(self, *args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom)
        # 不抛异常，仅告警
        service._save_cache([KnowledgeEntry(id="s1", category="style")],
                            np.array([[1.0]], dtype=np.float32))


class TestInitialize:
    """lines 239-260: initialize 四条路径。"""

    def test_already_initialized_returns_early(self, tmp_path):
        service = _make_service(tmp_path)
        service._initialized = True
        with patch.object(service, "_load_cache", side_effect=AssertionError("不应调用")):
            service.initialize()

    def test_cache_hit_skips_model_and_kb(self, tmp_path):
        service = _make_service(tmp_path)
        _write_kb(service.kb_dir, "a.json", [{"id": "s1"}])
        cache_path = service._cache_path()
        cache_path.write_text(json.dumps({
            "entries": [{"id": "cached1", "category": "style"}],
            "embeddings": [[1.0, 0.0]],
        }), encoding="utf-8")
        with patch("app.services.rag_service.TextEmbedding", side_effect=AssertionError("不应加载模型")):
            service.initialize()
        assert service._initialized
        assert [e.id for e in service._entries] == ["cached1"]
        assert service._embeddings.shape == (1, 2)

    def test_no_entries_marks_initialized(self, tmp_path):
        service = _make_service(tmp_path)
        service.kb_dir.mkdir(parents=True)  # 空 kb 目录
        with patch("app.services.rag_service.TextEmbedding", side_effect=AssertionError("不应加载模型")):
            service.initialize()
        assert service._initialized
        assert service._entries == []

    def test_full_path_encodes_and_saves_cache(self, tmp_path):
        service = _make_service(tmp_path)
        _write_kb(service.kb_dir, "styles.json", [
            {"id": "s1", "category": "style", "title": "写实"},
            {"id": "s2", "category": "style", "title": "动漫", "style": "anime"},
        ])
        fake_model = MagicMock()
        fake_model.embed = MagicMock(
            side_effect=lambda texts: iter([[1.0, 0.0] for _ in texts])
        )
        with patch("app.services.rag_service.TextEmbedding", return_value=fake_model) as m_te:
            service.initialize()
        assert m_te.call_count == 1
        assert service._initialized
        assert len(service._entries) == 2
        assert service._embeddings.shape == (2, 2)
        # 缓存已落盘，二次 initialize（新实例）直接命中
        service2 = _make_service(tmp_path)
        with patch("app.services.rag_service.TextEmbedding", side_effect=AssertionError("不应加载模型")):
            service2.initialize()
        assert [e.id for e in service2._entries] == ["s1", "s2"]


class TestEnsureInitialized:
    """line 267: 未初始化时 _ensure_initialized 触发 initialize。"""

    def test_triggers_initialize(self, tmp_path):
        service = _make_service(tmp_path)
        assert not service._initialized
        with patch.object(service, "initialize") as m_init:
            service._ensure_initialized()
            m_init.assert_called_once()

    def test_skips_when_initialized(self, tmp_path):
        service = _make_service(tmp_path)
        service._initialized = True
        with patch.object(service, "initialize", side_effect=AssertionError("不应调用")):
            service._ensure_initialized()


class TestSearchEmptyAndFilters:
    """lines 305, 323, 325, 329: 空索引与 metadata 过滤。"""

    def _service_with_vectors(self, tmp_path) -> RAGService:
        service = _make_service(tmp_path)
        service._entries = [
            KnowledgeEntry(id="s1", category="style", title="写实", content="realistic",
                           domain=["image", "video"], tags=["film"], style="realistic_film"),
            KnowledgeEntry(id="s2", category="style", title="动漫", content="anime",
                           domain=["video"], tags=["anime"]),
        ]
        service._embeddings = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        service._initialized = True
        fake_model = MagicMock()
        fake_model.embed = MagicMock(side_effect=lambda _texts: iter([[1.0, 0.0]]))
        service._embedding_model = fake_model
        return service

    def test_empty_entries_returns_empty(self, tmp_path):
        service = _make_service(tmp_path)
        service._initialized = True
        service._entries = []
        assert service.search("任何查询") == []

    def test_none_embeddings_returns_empty(self, tmp_path):
        service = _make_service(tmp_path)
        service._initialized = True
        service._entries = [KnowledgeEntry(id="s1", category="style")]
        service._embeddings = None
        assert service.search("任何查询") == []

    def test_zero_size_embeddings_returns_empty(self, tmp_path):
        service = _make_service(tmp_path)
        service._initialized = True
        service._entries = [KnowledgeEntry(id="s1", category="style")]
        service._embeddings = np.array([], dtype=np.float32)
        assert service.search("任何查询") == []

    def test_domain_filter_excludes(self, tmp_path):
        service = self._service_with_vectors(tmp_path)
        # s2 仅适用 video；domain=image 时应被过滤
        results = service.search("q", domain="image", top_k=5)
        assert [r["id"] for r in results] == ["s1"]

    def test_style_filter_by_tags_and_style_field(self, tmp_path):
        service = self._service_with_vectors(tmp_path)
        # style 命中 entry.style 字段
        results = service.search("q", style="realistic_film", top_k=5)
        assert [r["id"] for r in results] == ["s1"]
        # style 命中 tags
        results = service.search("q", style="anime", top_k=5)
        assert [r["id"] for r in results] == ["s2"]

    def test_no_matching_indices_returns_empty(self, tmp_path):
        service = self._service_with_vectors(tmp_path)
        assert service.search("q", style="不存在的风格", top_k=5) == []


class TestGetStyles:
    """lines 371-372: get_styles 只返回 style 类别条目。"""

    def test_returns_only_style_entries(self, tmp_path):
        service = _make_service(tmp_path)
        service._initialized = True
        service._entries = [
            KnowledgeEntry(id="s1", category="style", title="写实"),
            KnowledgeEntry(id="shot1", category="shot", title="特写"),
            KnowledgeEntry(id="s2", category="style", title="动漫"),
        ]
        styles = service.get_styles()
        assert [s["id"] for s in styles] == ["s1", "s2"]
        assert all(s["category"] == "style" for s in styles)


class TestBuildSystemPromptSections:
    """lines 393, 397, 399, 401-406, 435, 439, 441, 443: 方法/镜头/负面/示例分区。"""

    def test_all_sections_present(self, tmp_path):
        service = _make_service(tmp_path)
        retrieved = [
            {"id": "m1", "category": "method", "title": "方法A", "content": "先主体后环境"},
            {"id": "shot1", "category": "shot", "title": "特写", "content": "close-up"},
            {"id": "n1", "category": "negative", "title": "通用负面", "content": "blurry"},
            {"id": "e1", "category": "example", "title": "示例1", "content": "ex content",
             "optimized_positive": "a masterpiece", "optimized_negative": "lowres"},
        ]
        prompt = service._build_system_prompt(retrieved)
        assert "[提示词优化方法]" in prompt and "先主体后环境" in prompt
        assert "[镜头/光影/构图参考]" in prompt and "close-up" in prompt
        assert "[负面提示词参考]" in prompt and "blurry" in prompt
        assert "[高质量示例]" in prompt
        assert "正向：a masterpiece" in prompt
        assert "负向：lowres" in prompt

    def test_example_without_optimized_fields(self, tmp_path):
        service = _make_service(tmp_path)
        retrieved = [{"id": "e1", "category": "example", "title": "示例1", "content": "ex"}]
        prompt = service._build_system_prompt(retrieved)
        assert "[高质量示例]" in prompt
        assert "正向：" not in prompt
        assert "负向：" not in prompt


class TestOptimizePromptQueryAndMessage:
    """lines 499, 531, 533: style_hint 拼入 query，style_hint/extra_instruction 拼入 user_msg。"""

    async def test_style_hint_prefixes_query_and_message(self, monkeypatch, tmp_path):
        service = _make_service(tmp_path)
        service._initialized = True
        service._entries = []
        queries: list[str] = []

        def fake_search(query, category=None, domain=None, style=None, top_k=None):
            queries.append(query)
            return []

        monkeypatch.setattr(service, "search", fake_search)

        captured: dict = {}
        fake_client = MagicMock()

        async def fake_create(**kwargs):
            captured.update(kwargs)
            resp = MagicMock()
            resp.choices = [MagicMock(message=MagicMock(content='{"optimized_positive": "x"}'))]
            return resp

        fake_client.chat.completions.create = fake_create

        with patch("app.services.rag_service.get_shared_llm_client", return_value=fake_client):
            await service.optimize_prompt(
                "主角看手机", domain="video", style_hint="赛博朋克",
                extra_instruction="突出霓虹灯",
            )

        # style_hint 作为前缀拼入检索 query
        assert queries and all(q.startswith("赛博朋克 主角看手机") for q in queries)
        user_msg = captured["messages"][1]["content"]
        assert "期望风格：赛博朋克" in user_msg
        assert "额外要求：突出霓虹灯" in user_msg

    async def test_no_hints_omits_extra_lines(self, monkeypatch, tmp_path):
        service = _make_service(tmp_path)
        service._initialized = True
        service._entries = []
        monkeypatch.setattr(service, "search", MagicMock(return_value=[]))

        captured: dict = {}
        fake_client = MagicMock()

        async def fake_create(**kwargs):
            captured.update(kwargs)
            resp = MagicMock()
            resp.choices = [MagicMock(message=MagicMock(content='{"optimized_positive": "x"}'))]
            return resp

        fake_client.chat.completions.create = fake_create

        with patch("app.services.rag_service.get_shared_llm_client", return_value=fake_client):
            await service.optimize_prompt("主角看手机", domain="image")

        user_msg = captured["messages"][1]["content"]
        assert "期望风格" not in user_msg
        assert "额外要求" not in user_msg


class TestParseOptimizerOutput:
    """lines 563-565, 569-573, 586-587: markdown 剥离、json_repair、解析失败兜底。"""

    def test_strips_markdown_code_fence(self):
        content = '```json\n{"optimized_positive": "abc", "tags": ["t"]}\n```'
        result = RAGService._parse_optimizer_output(content, [], "orig")
        assert result["optimized_positive"] == "abc"
        assert result["tags"] == ["t"]
        assert "fallback" not in result

    def test_json_repair_fixes_broken_json(self):
        # 尾逗号：json.loads 失败，json_repair 修复
        content = '{"optimized_positive": "abc",}'
        result = RAGService._parse_optimizer_output(content, [], "orig")
        assert result["optimized_positive"] == "abc"
        assert "fallback" not in result

    def test_json_repair_exception_falls_back(self):
        with patch("app.services.rag_service.json_repair.loads", side_effect=ValueError("boom")):
            result = RAGService._parse_optimizer_output("{broken", [], "orig")
        assert result["fallback"] is True
        assert result["original_prompt"] == "orig"

    def test_non_dict_parsed_falls_back(self):
        # json.loads 失败、json_repair 返回非 dict（纯文本）→ 走兜底
        result = RAGService._parse_optimizer_output("这根本不是 JSON", [], "orig")
        assert result["fallback"] is True
        assert result["retrieved_count"] == 0

    def test_fallback_uses_provided_lora_recommendations(self):
        loras = [{"filename": "a.safetensors", "style_key": "", "trigger_words": [], "weight": 0.7}]
        result = RAGService._parse_optimizer_output("not json", [], "orig", loras)
        assert result["lora_recommendations"] == loras


class TestFallbackOutputBranches:
    """lines 609-610, 612, 614-618: shot/negative/example 分支。"""

    def test_shot_branch_appends_positive_and_notes(self):
        retrieved = [{"id": "shot1", "category": "shot", "title": "低角度仰拍",
                      "content": "low angle shot", "tags": ["仰拍"]}]
        result = RAGService._fallback_output(retrieved, "主角登场")
        assert "low angle shot" in result["optimized_positive"]
        assert "低角度仰拍" in result["style_notes"]
        # shot 分支不并入 tags
        assert result["tags"] == []

    def test_negative_branch_extends_negatives(self):
        retrieved = [{"id": "n1", "category": "negative", "title": "通用负面",
                      "content": "", "negative_terms": ["blurry", "low quality"]}]
        result = RAGService._fallback_output(retrieved, "测试")
        assert "blurry" in result["optimized_negative"]
        assert "low quality" in result["optimized_negative"]

    def test_example_branch_merges_prompts_and_tags(self):
        retrieved = [{
            "id": "e1", "category": "example", "title": "示例",
            "content": "ex", "tags": ["电影感"],
            "optimized_positive": "cinematic masterpiece",
            "optimized_negative": "lowres, watermark",
        }]
        result = RAGService._fallback_output(retrieved, "测试")
        assert "cinematic masterpiece" in result["optimized_positive"]
        # optimized_negative 按逗号拆分并入
        assert "lowres" in result["optimized_negative"]
        assert "watermark" in result["optimized_negative"]
        assert "电影感" in result["tags"]

    def test_example_branch_without_optimized_fields(self):
        retrieved = [{"id": "e1", "category": "example", "title": "示例",
                      "content": "ex", "tags": ["t"]}]
        result = RAGService._fallback_output(retrieved, "测试")
        assert result["optimized_positive"] == "测试"
        assert result["optimized_negative"] == ""
        assert "t" in result["tags"]
