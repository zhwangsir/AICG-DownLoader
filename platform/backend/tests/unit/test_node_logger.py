"""T3 核心链路节点日志埋点（node_logger）单元测试。"""

from __future__ import annotations

import logging

import pytest

from app.core.node_logger import node_log, node_span


@pytest.fixture
def caplog_node(caplog):
    """捕获 aicg.node logger 的输出。"""
    with caplog.at_level(logging.INFO, logger="aicg.node"):
        yield caplog


class TestNodeLog:
    def test_basic_fields(self, caplog_node):
        """节点标识 + 状态 + task_id + 参数全部出现在日志行。"""
        node_log("pipeline.script", "start", task_id="t1", premise="末日便利店", episodes=3)
        record = caplog_node.records[-1]
        assert "node=pipeline.script" in record.message
        assert "status=start" in record.message
        assert "task_id=t1" in record.message
        assert "premise=末日便利店" in record.message
        assert "episodes=3" in record.message

    def test_elapsed_and_level(self, caplog_node):
        """ok 状态走 INFO，error 状态走 ERROR。"""
        node_log("llm.chat", "ok", elapsed_ms=123.456, model="qwen")
        assert caplog_node.records[-1].levelno == logging.INFO
        assert "elapsed_ms=123.5" in caplog_node.records[-1].message

        node_log("llm.chat", "error", error="连接超时", elapsed_ms=4000.0)
        assert caplog_node.records[-1].levelno == logging.ERROR
        assert "error=连接超时" in caplog_node.records[-1].message

    def test_long_value_truncated(self, caplog_node):
        """超长参数值截断 200 字符，防止刷爆日志。"""
        node_log("x", "start", prompt="p" * 500)
        message = caplog_node.records[-1].message
        assert len(message) < 400
        assert message.endswith("…")

    def test_newline_flattened(self, caplog_node):
        """参数中的换行符压平，保证一条日志一行（grep 友好）。"""
        node_log("x", "start", text="line1\nline2")
        assert "line1 line2" in caplog_node.records[-1].message


class TestNodeSpan:
    async def test_start_ok_with_elapsed(self, caplog_node):
        """区间埋点：进入记 start，正常退出记 ok（含耗时）。"""
        async with node_span("pipeline.video", task_id="t2", scenes=3):
            pass
        messages = [r.message for r in caplog_node.records]
        assert any("node=pipeline.video status=start" in m and "scenes=3" in m for m in messages)
        assert any("node=pipeline.video status=ok" in m and "elapsed_ms=" in m for m in messages)

    async def test_error_reraises_with_log(self, caplog_node):
        """异常：记 error 日志后原样抛出。"""
        with pytest.raises(ValueError, match="boom"):
            async with node_span("pipeline.edit", task_id="t3"):
                raise ValueError("boom")
        error_records = [r for r in caplog_node.records if r.levelno == logging.ERROR]
        assert any(
            "node=pipeline.edit" in r.message and "error=boom" in r.message
            for r in error_records
        )
