"""RAG 知识库目录。

包含风格、镜头技法、负面提示词、示例 Prompt 四类原始数据，
供 RAGService 在启动时加载、嵌入并建立本地向量索引。
"""

from __future__ import annotations

from pathlib import Path

KB_DIR = Path(__file__).resolve().parent
