"""RAG 提示词优化服务。

基于本地嵌入模型 + 向量缓存 + 项目 LLM 四层流水线，
实现：
1. 加载 knowledge_base/ 下的风格、镜头、负面、示例、方法论、类型片叙事模板六类 JSON；
2. 将条目编码为向量并持久化到本地缓存；
3. 按用户输入检索相关知识；
4. 调用 LLM 生成结构化优化提示词（正向/负向/风格说明/标签）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import json_repair
import numpy as np
from fastembed import TextEmbedding

from app.config import settings
from app.knowledge_base import KB_DIR
from app.agents.base import get_shared_llm_client, strip_think_tags

logger = logging.getLogger(__name__)

# 嵌入模型加载失败熔断 TTL：外网不可达时避免逐场景重复超时（每次 ~130s）
MODEL_LOAD_FAILURE_TTL_SECONDS = 600.0

DEFAULT_EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_TOP_K = 5
CACHE_VERSION = "v1"


@dataclass
class KnowledgeEntry:
    """知识库单条记录。"""

    id: str
    category: str
    domain: list[str] = field(default_factory=list)
    lang: str = "zh"
    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    negative_terms: list[str] = field(default_factory=list)
    style_intensity: float = 0.5
    model_target: list[str] = field(default_factory=list)
    source: str = "built-in"
    parent_id: str | None = None
    # example 专用字段
    optimized_positive: str = ""
    optimized_negative: str = ""
    style: str = ""
    recommended_loras: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_embed_text(self) -> str:
        """用于嵌入的文本：融合标题、内容与标签，提升检索召回。"""
        parts = [self.title, self.content, ",".join(self.tags)]
        if self.style:
            parts.append(self.style)
        return "\n".join(p for p in parts if p)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "id": self.id,
            "category": self.category,
            "domain": self.domain,
            "lang": self.lang,
            "title": self.title,
            "content": self.content,
            "tags": self.tags,
            "negative_terms": self.negative_terms,
            "style_intensity": self.style_intensity,
            "model_target": self.model_target,
            "source": self.source,
            "parent_id": self.parent_id,
        }
        if self.optimized_positive:
            data["optimized_positive"] = self.optimized_positive
        if self.optimized_negative:
            data["optimized_negative"] = self.optimized_negative
        if self.style:
            data["style"] = self.style
        if self.recommended_loras:
            data["recommended_loras"] = self.recommended_loras
        data.update(self.extra)
        return data


class RAGService:
    """内置 RAG 服务：管理知识库、嵌入、检索与提示词优化。"""

    def __init__(
        self,
        model_name: str | None = None,
        cache_dir: Path | None = None,
        kb_dir: Path | None = None,
        top_k: int = DEFAULT_TOP_K,
    ) -> None:
        self.model_name = model_name or DEFAULT_EMBED_MODEL
        self.kb_dir = kb_dir or KB_DIR
        self.cache_dir = cache_dir or (Path(__file__).resolve().parent.parent.parent / "data")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.top_k = top_k

        self._entries: list[KnowledgeEntry] = []
        self._embeddings: np.ndarray | None = None
        self._embedding_model: TextEmbedding | None = None
        self._initialized = False
        self._model_load_failed_at: float | None = None

    # ------------------------------------------------------------------
    # 初始化与缓存
    # ------------------------------------------------------------------

    def _init_model(self) -> None:
        """懒加载嵌入模型，避免导入即耗时。加载失败后熔断 TTL 内直接抛错。"""
        if self._embedding_model is not None:
            return
        if self._model_load_failed_at is not None:
            if time.time() - self._model_load_failed_at < MODEL_LOAD_FAILURE_TTL_SECONDS:
                raise RuntimeError(
                    f"嵌入模型 {self.model_name} 此前加载失败，熔断中（TTL 内不再重试）"
                )
            self._model_load_failed_at = None  # TTL 已过，允许重试
        try:
            logger.info("Loading embedding model: %s", self.model_name)
            self._embedding_model = TextEmbedding(model_name=self.model_name)
        except Exception as e:
            self._model_load_failed_at = time.time()
            logger.error("Failed to load embedding model %s: %s", self.model_name, e)
            raise RuntimeError(f"无法加载嵌入模型 {self.model_name}: {e}") from e

    def _cache_path(self) -> Path:
        """根据知识库内容哈希生成缓存文件路径，知识库变更后自动失效。"""
        kb_hash = self._compute_kb_hash()
        return self.cache_dir / f"rag_cache_{self.model_name.replace('/', '_')}_{CACHE_VERSION}_{kb_hash}.json"

    def _compute_kb_hash(self) -> str:
        """计算知识库原始文件内容的 md5，用于缓存失效判断。"""
        hasher = hashlib.md5()
        for path in sorted(self.kb_dir.glob("*.json")):
            hasher.update(path.read_bytes())
        return hasher.hexdigest()[:16]

    def _load_kb_files(self) -> list[KnowledgeEntry]:
        """读取 knowledge_base 目录下所有 JSON 文件。"""
        entries: list[KnowledgeEntry] = []
        for path in sorted(self.kb_dir.glob("*.json")):
            if path.name == "__init__.py":
                continue  # pragma: no cover — glob("*.json") 永不匹配 .py，防御性死分支
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for raw in data.get("entries", []):
                    entries.append(self._raw_to_entry(raw))
                logger.info("Loaded knowledge base: %s (%d entries)", path.name, len(data.get("entries", [])))
            except Exception as e:
                logger.warning("Failed to load knowledge base %s: %s", path.name, e)
        return entries

    @staticmethod
    def _raw_to_entry(raw: dict[str, Any]) -> KnowledgeEntry:
        """将 JSON 原始条目转换为 KnowledgeEntry。"""
        known_fields = {
            "id", "category", "domain", "lang", "title", "content", "tags",
            "negative_terms", "style_intensity", "model_target", "source",
            "parent_id", "optimized_positive", "optimized_negative", "style",
            "recommended_loras",
        }
        extra = {k: v for k, v in raw.items() if k not in known_fields}
        return KnowledgeEntry(
            id=raw["id"],
            category=raw.get("category", "style"),
            domain=raw.get("domain", ["image", "video"]),
            lang=raw.get("lang", "zh"),
            title=raw.get("title", ""),
            content=raw.get("content", ""),
            tags=raw.get("tags", []),
            negative_terms=raw.get("negative_terms", []),
            style_intensity=raw.get("style_intensity", 0.5),
            model_target=raw.get("model_target", []),
            source=raw.get("source", "built-in"),
            parent_id=raw.get("parent_id"),
            optimized_positive=raw.get("optimized_positive", ""),
            optimized_negative=raw.get("optimized_negative", ""),
            style=raw.get("style", ""),
            recommended_loras=raw.get("recommended_loras", []),
            extra=extra,
        )

    def _load_cache(self) -> tuple[list[KnowledgeEntry], np.ndarray] | None:
        """尝试从缓存加载条目与嵌入向量。"""
        cache_path = self._cache_path()
        if not cache_path.exists():
            return None
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            entries = [self._raw_to_entry(e) for e in data["entries"]]
            embeddings = np.array(data["embeddings"], dtype=np.float32)
            if embeddings.shape[0] != len(entries):
                logger.warning("Cache size mismatch, rebuilding index")
                return None
            logger.info("Loaded RAG cache: %s (%d entries)", cache_path.name, len(entries))
            return entries, embeddings
        except Exception as e:
            logger.warning("Failed to load RAG cache: %s", e)
            return None

    def _save_cache(self, entries: list[KnowledgeEntry], embeddings: np.ndarray) -> None:
        """保存条目与嵌入向量到本地缓存。"""
        cache_path = self._cache_path()
        try:
            cache_path.write_text(
                json.dumps(
                    {
                        "model": self.model_name,
                        "version": CACHE_VERSION,
                        "entries": [e.to_dict() for e in entries],
                        "embeddings": embeddings.tolist(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            logger.info("Saved RAG cache: %s", cache_path.name)
        except Exception as e:
            logger.warning("Failed to save RAG cache: %s", e)

    def initialize(self) -> None:
        """初始化：加载知识库、嵌入并建立索引。可重复调用。"""
        if self._initialized:
            return

        cached = self._load_cache()
        if cached:
            self._entries, self._embeddings = cached
            self._initialized = True
            return

        self._entries = self._load_kb_files()
        if not self._entries:
            logger.warning("No knowledge entries loaded from %s", self.kb_dir)
            self._initialized = True
            return

        self._init_model()
        texts = [e.to_embed_text() for e in self._entries]
        logger.info("Encoding %d knowledge entries...", len(texts))
        embeddings = list(self._embedding_model.embed(texts))  # type: ignore[arg-type]
        self._embeddings = np.array(embeddings, dtype=np.float32)
        self._save_cache(self._entries, self._embeddings)
        self._initialized = True

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def _ensure_initialized(self) -> None:
        if not self._initialized:
            self.initialize()

    def _warm_up(self) -> None:
        """线程内预热：初始化索引并加载嵌入模型。

        缓存命中时 initialize() 不会加载模型（_embedding_model 为 None），
        首个 search() 会在事件循环内触发 _init_model() 同步下载/加载模型，
        外网不可达时阻塞事件循环 ~130s（2026-08-04 core 实测全接口冻结）。
        因此预热必须显式补加载模型。
        """
        self._ensure_initialized()
        if self._entries:
            self._init_model()

    def search(
        self,
        query: str,
        category: str | None = None,
        domain: str | None = None,
        style: str | None = None,
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        """检索与 query 最相关的知识条目。

        Args:
            query: 用户查询，支持中文或英文。
            category: 过滤类别（style/shot/negative/example/method/genre_trope）。
            domain: 过滤适用领域（image/video）。
            style: 过滤风格标签。
            top_k: 返回条数，默认使用初始化参数。

        Returns:
            按相似度排序的条目列表，每条包含 similarity 分数。
        """
        self._ensure_initialized()
        top_k = top_k or self.top_k

        if not self._entries or self._embeddings is None or self._embeddings.size == 0:
            return []

        self._init_model()
        query_vec = next(self._embedding_model.embed([query]))  # type: ignore[arg-type]
        query_vec = np.array(query_vec, dtype=np.float32)

        # 余弦相似度
        norms = np.linalg.norm(self._embeddings, axis=1) * np.linalg.norm(query_vec)
        with np.errstate(divide="ignore", invalid="ignore"):
            similarities = np.dot(self._embeddings, query_vec) / norms
            similarities = np.nan_to_num(similarities, nan=-1.0)

        # metadata 过滤
        indices: list[int] = []
        for i, entry in enumerate(self._entries):
            if category and entry.category != category:
                continue
            if domain and domain not in entry.domain:
                continue
            if style and style not in entry.tags and entry.style != style:
                continue
            indices.append(i)

        if not indices:
            return []

        filtered_sims = [(i, float(similarities[i])) for i in indices]
        filtered_sims.sort(key=lambda x: x[1], reverse=True)

        results: list[dict[str, Any]] = []
        for i, score in filtered_sims[:top_k]:
            entry = self._entries[i]
            item = entry.to_dict()
            item["similarity"] = round(score, 4)
            results.append(item)
        return results

    def _retrieve_multi(
        self,
        query: str,
        domain: str,
        style_hint: str | None,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        """六路同步检索（风格/镜头/示例/负面/方法/类型片模板）。

        search() 内部含 fastembed ONNX 同步推理，必须由调用方放入线程执行
        （optimize_prompt 经 asyncio.to_thread 调用本方法），避免阻塞事件循环。
        """
        return (
            self.search(query, category="style", domain=domain, style=style_hint, top_k=2),
            self.search(query, category="shot", domain=domain, top_k=2),
            self.search(query, category="example", domain=domain, style=style_hint, top_k=2),
            self.search(query, category="negative", domain=domain, top_k=2),
            self.search(query, category="method", domain=domain, top_k=2),
            self.search(query, category="genre_trope", domain=domain, style=style_hint, top_k=2),
        )

    def get_styles(self) -> list[dict[str, Any]]:
        """获取所有风格条目，用于前端下拉选择。"""
        self._ensure_initialized()
        return [e.to_dict() for e in self._entries if e.category == "style"]

    # ------------------------------------------------------------------
    # 提示词优化
    # ------------------------------------------------------------------
    def _build_system_prompt(self, retrieved: list[dict[str, Any]]) -> str:
        """根据检索结果构造系统提示词。"""
        method_blocks: list[str] = []
        style_blocks: list[str] = []
        shot_blocks: list[str] = []
        negative_blocks: list[str] = []
        example_blocks: list[str] = []
        trope_blocks: list[str] = []
        lora_blocks: list[str] = []

        for item in retrieved:
            cat = item["category"]
            title = item.get("title", item["id"])
            content = item.get("content", "")
            text = f"【{title}】{content}"
            if cat == "method":
                method_blocks.append(text)
            elif cat == "style":
                style_blocks.append(text)
            elif cat == "shot":
                shot_blocks.append(text)
            elif cat == "negative":
                negative_blocks.append(text)
            elif cat == "example":
                ex_text = text
                if item.get("optimized_positive"):
                    ex_text += f"\n正向：{item['optimized_positive']}"
                if item.get("optimized_negative"):
                    ex_text += f"\n负向：{item['optimized_negative']}"
                example_blocks.append(ex_text)
            elif cat == "genre_trope":
                trope_blocks.append(text)
                for lora in item.get("recommended_loras", []):
                    trigger = ", ".join(lora.get("trigger_words", []))
                    lora_blocks.append(
                        f"【{title}】{lora['filename']} (weight={lora.get('weight', 0.7)}, "
                        f"trigger_words={trigger})"
                    )

        sections: list[str] = [
            "你是一位专业的 AI 影视/动漫生成 Prompt 优化引擎，擅长将用户的中文场景描述改写成可直接用于图像/视频生成模型的高质量英文 Prompt。",
            "任务：",
            "1. 严格参考下方检索到的提示词优化方法、风格、镜头技法、负面提示词、示例与类型片叙事模板。",
            "2. 保留用户原始意图，不要引入未提及的新主题或角色。",
            "3. 输出结构化的 JSON 对象，字段如下：",
            "   - optimized_positive: 完整的英文正向提示词（逗号分隔，按“主体→动作→环境→镜头→光影→风格→质量”排序）。",
            "   - optimized_negative: 去重后的英文负面提示词（逗号分隔）。",
            "   - style_notes: 中文风格/技法说明。",
            "   - tags: 用于检索/分类的中文标签数组。",
            "   - lora_recommendations: 推荐的 LoRA 数组，每项包含 filename/trigger_words/weight。",   
            "约束：",
            "- 正向提示词应具体、可视觉化，避免抽象形容词。",
            "- 负面提示词不得与正向提示词语义冲突。",
            "- 若检索知识不足，直接基于用户输入优化，不要编造。",
            "- 只输出 JSON，不要 Markdown 代码块，不要解释。",
        ]

        if method_blocks:
            sections.append("\n[提示词优化方法]\n" + "\n".join(method_blocks))
        if style_blocks:
            sections.append("\n[风格参考]\n" + "\n".join(style_blocks))
        if shot_blocks:
            sections.append("\n[镜头/光影/构图参考]\n" + "\n".join(shot_blocks))
        if negative_blocks:
            sections.append("\n[负面提示词参考]\n" + "\n".join(negative_blocks))
        if example_blocks:
            sections.append("\n[高质量示例]\n" + "\n\n".join(example_blocks))
        if trope_blocks:
            sections.append("\n[类型片叙事镜头模板]\n" + "\n\n".join(trope_blocks))
        if lora_blocks:
            sections.append("\n[推荐 LoRA]\n" + "\n".join(dict.fromkeys(lora_blocks)))

        return "\n".join(sections)

    @staticmethod
    def _collect_lora_recommendations(retrieved: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """从检索结果中收集去重的 LoRA 推荐。"""
        recommendations: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in retrieved:
            for lora in item.get("recommended_loras", []):
                filename = lora.get("filename", "")
                if not filename or filename in seen:
                    continue
                seen.add(filename)
                recommendations.append(
                    {
                        "filename": filename,
                        "style_key": lora.get("style_key", ""),
                        "trigger_words": lora.get("trigger_words", []),
                        "weight": lora.get("weight", 0.7),
                    }
                )
        return recommendations

    async def optimize_prompt(
        self,
        user_prompt: str,
        domain: str = "video",
        style_hint: str | None = None,
        extra_instruction: str | None = None,
    ) -> dict[str, Any]:
        """使用 RAG + LLM 优化用户提示词。

        Args:
            user_prompt: 用户原始中文描述。
            domain: 目标领域（image/video）。
            style_hint: 风格提示，用于增强检索。
            extra_instruction: 额外优化指令。

        Returns:
            包含 optimized_positive、optimized_negative、style_notes、tags、
            lora_recommendations 的字典。若 LLM 调用失败，返回基于检索结果的兜底结果。
        """
        # 首次初始化需加载嵌入模型（外网不可达时可能阻塞 ~130s），放到线程避免冻结事件循环；
        # 注意必须同时预热模型——缓存命中时 initialize() 不加载模型，
        # 否则下方 search() 会在事件循环内同步加载模型（core 实测全接口冻结）
        await asyncio.to_thread(self._warm_up)

        # 1. 构建检索 query
        query = user_prompt
        if style_hint:
            query = f"{style_hint} {user_prompt}"

        # 2. 多路检索：风格 + 镜头 + 示例 + 负面 + 方法 + 类型片叙事模板。
        # search() 内部含 fastembed ONNX 同步推理（CPU 每路 ~20-100ms），
        # 六路串行在事件循环内执行会累积数百 ms 卡顿（pipeline 逐场景调用时放大），
        # 故整体放线程执行。
        (
            style_results,
            shot_results,
            example_results,
            negative_results,
            method_results,
            trope_results,
        ) = await asyncio.to_thread(
            self._retrieve_multi, query, domain, style_hint
        )

        # 去重合并
        seen_ids: set[str] = set()
        retrieved: list[dict[str, Any]] = []
        for r in style_results + shot_results + example_results + negative_results + method_results + trope_results:
            if r["id"] in seen_ids:
                continue
            seen_ids.add(r["id"])
            retrieved.append(r)

        lora_recommendations = self._collect_lora_recommendations(retrieved)

        # 3. 调用 LLM
        system_prompt = self._build_system_prompt(retrieved)
        user_msg = f"用户原始描述：\n{user_prompt}\n\n目标领域：{domain}\n"
        if style_hint:
            user_msg += f"期望风格：{style_hint}\n"
        if extra_instruction:
            user_msg += f"额外要求：{extra_instruction}\n"

        try:
            client = get_shared_llm_client()
            resp = await client.chat.completions.create(
                model=settings.exo_model_glm52,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.5,
                max_tokens=1500,
            )
            content = resp.choices[0].message.content or ""
            return self._parse_optimizer_output(content, retrieved, user_prompt, lora_recommendations)
        except Exception as e:
            logger.warning("LLM prompt optimization failed: %s", e)
            return self._fallback_output(retrieved, user_prompt, lora_recommendations)

    @staticmethod
    def _parse_optimizer_output(
        content: str,
        retrieved: list[dict[str, Any]],
        original_prompt: str,
        lora_recommendations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """解析 LLM 返回的 JSON，失败则回退。"""
        # 剥离思维链，再去除可能的 markdown 代码块
        cleaned = strip_think_tags(content)
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()
        parsed: dict[str, Any] | None = None
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            try:
                parsed = json_repair.loads(cleaned)
            except Exception as e:
                logger.warning("Failed to parse optimizer output as JSON: %s", e)

        if isinstance(parsed, dict):
            return {
                "optimized_positive": parsed.get("optimized_positive", ""),
                "optimized_negative": parsed.get("optimized_negative", ""),
                "style_notes": parsed.get("style_notes", ""),
                "tags": parsed.get("tags", []),
                "lora_recommendations": parsed.get("lora_recommendations") or (lora_recommendations or []),
                "original_prompt": original_prompt,
                "retrieved_count": len(retrieved),
            }

        logger.warning("Failed to parse optimizer output as JSON, using fallback")
        return RAGService._fallback_output(retrieved, original_prompt, lora_recommendations)

    @staticmethod
    def _fallback_output(
        retrieved: list[dict[str, Any]],
        original_prompt: str,
        lora_recommendations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """LLM 失败时的兜底输出：拼接检索到的风格与示例，并携带 LoRA 推荐。"""
        if lora_recommendations is None:
            lora_recommendations = RAGService._collect_lora_recommendations(retrieved)
        positives: list[str] = [original_prompt]
        negatives: list[str] = []
        tags: list[str] = []
        style_notes: list[str] = []

        for item in retrieved:
            if item["category"] == "style":
                positives.append(item["content"])
                style_notes.append(item["title"])
                tags.extend(item.get("tags", []))
            elif item["category"] == "shot":
                positives.append(item["content"])
                style_notes.append(item["title"])
            elif item["category"] == "negative":
                negatives.extend(item.get("negative_terms", []))
            elif item["category"] == "example":
                if item.get("optimized_positive"):
                    positives.append(item["optimized_positive"])
                if item.get("optimized_negative"):
                    negatives.extend(item["optimized_negative"].split(","))
                tags.extend(item.get("tags", []))
            elif item["category"] == "genre_trope":
                positives.append(item["content"])
                style_notes.append(item["title"])
                tags.extend(item.get("tags", []))
                if item.get("negative_terms"):
                    negatives.extend(item["negative_terms"])

        return {
            "optimized_positive": ", ".join(p for p in positives if p),
            "optimized_negative": ", ".join(n.strip() for n in negatives if n.strip()),
            "style_notes": "；".join(dict.fromkeys(style_notes)) if style_notes else "基于知识库检索结果拼接",
            "tags": list(dict.fromkeys(tags)),
            "lora_recommendations": lora_recommendations or [],
            "original_prompt": original_prompt,
            "retrieved_count": len(retrieved),
            "fallback": True,
        }


# 全局单例
rag_service = RAGService()
