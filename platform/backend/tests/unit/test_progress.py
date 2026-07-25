"""ProgressTracker 单元测试。"""

from __future__ import annotations

import pytest

from app.core.progress import ProgressTracker, progress_event


@pytest.fixture
def tracker() -> ProgressTracker:
    return ProgressTracker(ttl_seconds=1.0)


class TestProgressTracker:
    def test_create_returns_task_id(self, tracker: ProgressTracker):
        task_id = tracker.create("video", "任务已创建")
        assert task_id.startswith("video-")
        record = tracker.get(task_id)
        assert record is not None
        assert record.status == "pending"
        assert record.percent == 0

    def test_update_percent_clamped(self, tracker: ProgressTracker):
        task_id = tracker.create("video")
        tracker.update(task_id, percent=-10)
        assert tracker.get(task_id).percent == 0
        tracker.update(task_id, percent=150)
        assert tracker.get(task_id).percent == 100

    def test_update_notifies_listeners(self, tracker: ProgressTracker):
        task_id = tracker.create("video")
        notified = []

        def listener(record):
            notified.append(record.percent)

        tracker.subscribe(task_id, listener)
        tracker.update(task_id, percent=50, message="half")
        assert notified == [50]

    def test_subscribe_unknown_task_returns_false(self, tracker: ProgressTracker):
        result = tracker.subscribe("not-exist", lambda r: None)
        assert result is False

    def test_cleanup_removes_expired(self, tracker: ProgressTracker):
        import time

        task_id = tracker.create("video")
        tracker.update(task_id, status="completed", percent=100)
        time.sleep(1.1)
        assert tracker.get(task_id) is None

    def test_progress_event_format(self, tracker: ProgressTracker):
        task_id = tracker.create("video")
        record = tracker.get(task_id)
        event = progress_event(record)
        assert event.startswith("data: {")
        assert '"task_id"' in event


class TestGlobalProgressTracker:
    def test_global_instance_exists(self):
        from app.core.progress import progress_tracker

        assert isinstance(progress_tracker, ProgressTracker)
