from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from common.performance import (
    JobManager,
    JobQueueFull,
    JobStatus,
    SharedSectionHub,
    _LatestFrameQueue,
    format_sse,
    safe_error,
)


async def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not reached in time")
        await asyncio.sleep(0.01)


class JobManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_persisted_numbers_do_not_hide_later_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "jobs.json"
            state_path.write_text(
                json.dumps(
                    {
                        "jobs": [
                            {
                                "id": "bad-numbers",
                                "key": "broken",
                                "status": "completed",
                                "progress": "not-a-number",
                                "total": float("nan"),
                            },
                            {
                                "id": "good-after-bad",
                                "key": "valid",
                                "status": "completed",
                                "progress": "7",
                                "total": "9",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            manager = JobManager(state_path=state_path)
            malformed = await manager.get("bad-numbers")
            self.assertEqual(malformed["progress"], 0)
            self.assertIsNone(malformed["total"])
            good = await manager.get("good-after-bad")
            self.assertEqual(good["progress"], 7)
            self.assertEqual(good["total"], 9)
            await manager.close()

    async def test_lifecycle_deduplicates_active_key_and_reports_progress(self) -> None:
        manager = JobManager(max_workers=1, max_pending=2)
        started = asyncio.Event()
        release = asyncio.Event()

        async def factory(context):
            started.set()
            context.report(message="Lecture", progress=1, total=2)
            await release.wait()
            return {"count": 2}

        first = await manager.submit_async(
            "library-scan",
            factory,
            metadata={"view": "library"},
        )
        duplicate = await manager.submit_async("library-scan", factory)
        self.assertEqual(first["id"], duplicate["id"])
        await asyncio.wait_for(started.wait(), 1)
        release.set()
        await wait_until(
            lambda: manager._records[first["id"]].status == JobStatus.COMPLETED
        )
        completed = await manager.get(first["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["progress"], 2)
        self.assertEqual(completed["result"], {"count": 2})
        await manager.close()

    async def test_thread_factory_runs_off_event_loop(self) -> None:
        manager = JobManager(max_workers=1)
        loop_thread = threading.get_ident()
        worker_threads: list[int] = []

        def factory(context):
            worker_threads.append(threading.get_ident())
            context.report(message="Copie", progress=1, total=1)
            return {"ok": True}

        queued = await manager.submit_thread("copy", factory)
        await wait_until(
            lambda: manager._records[queued["id"]].status == JobStatus.COMPLETED
        )
        self.assertNotEqual(worker_threads, [loop_thread])
        self.assertEqual((await manager.get(queued["id"]))["result"], {"ok": True})
        await manager.close()

    async def test_failure_is_sanitized(self) -> None:
        manager = JobManager(
            max_workers=1,
            error_factory=lambda _exc: safe_error(
                code="job_safe",
                message="Tâche indisponible.",
                recovery="Réessayer",
            ),
        )

        async def factory(_context):
            raise RuntimeError("token=top-secret")

        queued = await manager.submit_async("failure", factory)
        await wait_until(
            lambda: manager._records[queued["id"]].status == JobStatus.FAILED
        )
        record = await manager.get(queued["id"])
        self.assertEqual(record["error"]["code"], "job_safe")
        self.assertNotIn("top-secret", json.dumps(record))
        await manager.close()

    async def test_pending_queue_is_bounded_and_running_job_can_cancel(self) -> None:
        manager = JobManager(max_workers=1, max_pending=1)
        started = asyncio.Event()
        blocker = asyncio.Event()

        async def factory(_context):
            started.set()
            await blocker.wait()
            return None

        async def queued_factory(_context):
            return None

        first = await manager.submit_async("first", factory)
        await asyncio.wait_for(started.wait(), 1)
        await manager.submit_async("second", queued_factory)
        with self.assertRaises(JobQueueFull):
            await manager.submit_async("third", queued_factory)
        await manager.cancel(first["id"])
        await wait_until(
            lambda: manager._records[first["id"]].status == JobStatus.INTERRUPTED
        )
        blocker.set()
        await wait_until(
            lambda: manager._records[first["id"]].status == JobStatus.INTERRUPTED
        )
        await manager.close()

    async def test_restart_exposes_interrupted_job_without_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "jobs.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "jobs": [
                            {
                                "id": "persisted",
                                "key": "organize",
                                "status": "running",
                                "createdAt": "2026-01-01T00:00:00.000Z",
                                "metadata": {"kind": "organize"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            executed = False

            async def should_not_run(_context):
                nonlocal executed
                executed = True

            manager = JobManager(state_path=state_path)
            self.assertFalse(executed)
            rows = await manager.snapshot()
            self.assertEqual(rows[0]["status"], "interrupted")
            self.assertEqual(rows[0]["error"]["code"], "job_interrupted")
            self.assertEqual(manager._queue.qsize(), 0)
            await manager.close()

    async def test_progress_reports_are_coalesced_and_final_value_is_kept(self) -> None:
        manager = JobManager(max_workers=1)
        started = asyncio.Event()
        release = asyncio.Event()

        async def factory(context):
            started.set()
            for value in range(1, 501):
                context.report(progress=value, total=500)
            await release.wait()
            return {"ok": True}

        queued = await manager.submit_async("scan", factory)
        await asyncio.wait_for(started.wait(), 1)
        self.assertLessEqual(len(manager._pending_progress), 1)
        release.set()
        await wait_until(
            lambda: manager._records[queued["id"]].status == JobStatus.COMPLETED
        )
        record = await manager.get(queued["id"])
        self.assertEqual(record["progress"], 500)
        await manager.close()

    async def test_thread_cancellation_keeps_worker_and_key_until_thread_returns(self) -> None:
        manager = JobManager(max_workers=1)
        started = threading.Event()
        release = threading.Event()

        def factory(_context):
            started.set()
            release.wait(2)
            return {"mutated": True}

        first = await manager.submit_thread("copy", factory)
        await asyncio.wait_for(asyncio.to_thread(started.wait, 1), 1)
        await manager.cancel(first["id"])
        duplicate = await manager.submit_thread("copy", factory)
        self.assertEqual(duplicate["id"], first["id"])
        self.assertNotEqual(
            manager._records[first["id"]].status,
            JobStatus.INTERRUPTED,
        )
        release.set()
        await wait_until(
            lambda: manager._records[first["id"]].status == JobStatus.INTERRUPTED
        )
        await manager.close()

    async def test_thread_jobs_use_owner_pool_and_newer_persistence_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "jobs.json"
            manager = JobManager(max_workers=1, state_path=state_path)
            thread_name = []

            def factory(_context):
                thread_name.append(threading.current_thread().name)
                return {"ok": True}

            queued = await manager.submit_thread("copy", factory)
            await wait_until(
                lambda: manager._records[queued["id"]].status == JobStatus.COMPLETED
            )
            self.assertTrue(thread_name[0].startswith("dashboard-job"))
            manager._write_state({"version": 1, "jobs": [{"id": "new"}]}, 2)
            manager._write_state({"version": 1, "jobs": [{"id": "old"}]}, 1)
            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8"))["jobs"][0]["id"],
                "new",
            )
            await manager.close()


class SharedSectionHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_freshness_does_not_replace_pending_full_section_frame(self) -> None:
        queue = _LatestFrameQueue(maxsize=1)
        initial = {
            "event": "snapshot",
            "data": {"type": "snapshot", "topic": "files", "data": {"items": []}},
        }
        freshness = {
            "event": "freshness",
            "data": {
                "type": "freshness",
                "topic": "files",
                "collectedAt": "later",
                "unchanged": True,
                "stale": False,
            },
        }
        changed = {
            "event": "section",
            "data": {
                "type": "section",
                "topic": "files",
                "data": {"items": ["new-file"]},
            },
        }
        queue.put_nowait(initial)
        self.assertEqual(queue.get_nowait(), initial)
        queue.put_nowait(freshness)
        queue.put_nowait(changed)
        with self.assertRaises(asyncio.QueueFull):
            queue.put_nowait(freshness)
        self.assertEqual(queue.get_nowait(), changed)

    async def test_full_state_and_job_frames_evict_freshness_under_backpressure(self) -> None:
        queue = _LatestFrameQueue(maxsize=2)
        freshness = {
            "event": "freshness",
            "data": {"type": "freshness", "topic": "files"},
        }
        job = {
            "event": "job",
            "data": {"type": "job", "job": {"id": "copy-1", "status": "running"}},
        }
        changed = {
            "event": "section",
            "data": {"type": "section", "topic": "files", "data": {"ok": True}},
        }
        queue.put_nowait(freshness)
        queue.put_nowait(job)
        queue.put_nowait(changed)
        self.assertEqual(queue.get_nowait(), job)
        self.assertEqual(queue.get_nowait(), changed)

        queue = _LatestFrameQueue(maxsize=2)
        terminal = {
            "event": "job",
            "data": {"type": "job", "job": {"id": "copy-2", "status": "completed"}},
        }
        queue.put_nowait(freshness)
        queue.put_nowait(changed)
        queue.put_nowait(terminal)
        self.assertEqual(queue.get_nowait(), changed)
        self.assertEqual(queue.get_nowait(), terminal)

    async def test_subscribers_share_one_non_overlapping_collection(self) -> None:
        hub = SharedSectionHub(default_interval=0.01, heartbeat_interval=1)
        gate = asyncio.Event()
        calls = 0
        running = 0
        maximum_running = 0

        async def factory():
            nonlocal calls, running, maximum_running
            calls += 1
            running += 1
            maximum_running = max(maximum_running, running)
            await gate.wait()
            running -= 1
            return {"items": [1, 2, 3]}

        hub.register("torrents", factory, interval=30)
        first = hub.stream(["torrents"])
        second = hub.stream(["torrents"])
        first_task = asyncio.create_task(anext(first))
        second_task = asyncio.create_task(anext(second))
        await wait_until(lambda: calls == 1)
        gate.set()
        first_frame, second_frame = await asyncio.gather(
            first_task,
            second_task,
        )
        self.assertEqual(first_frame["event"], "snapshot")
        self.assertEqual(second_frame["data"]["data"], {"items": [1, 2, 3]})
        self.assertEqual(calls, 1)
        self.assertEqual(maximum_running, 1)
        await first.aclose()
        await second.aclose()
        await hub.close()

    async def test_invalidation_updates_all_subscribers_and_disconnect_pauses(self) -> None:
        hub = SharedSectionHub(default_interval=30, heartbeat_interval=1)
        calls = 0
        value = {"version": 1}

        async def factory():
            nonlocal calls
            calls += 1
            return value

        hub.register("storage", factory)
        first = hub.stream(["storage"])
        second = hub.stream(["storage"])
        await asyncio.wait_for(anext(first), 1)
        await asyncio.wait_for(anext(second), 1)
        value = {"version": 2}
        hub.invalidate("storage")
        next_first, next_second = await asyncio.gather(
            asyncio.wait_for(anext(first), 1),
            asyncio.wait_for(anext(second), 1),
        )
        self.assertEqual(next_first["data"]["data"], value)
        self.assertEqual(next_second["data"]["data"], value)
        self.assertEqual(next_first["event"], "section")
        await first.aclose()
        await second.aclose()
        calls_after_disconnect = calls
        await asyncio.sleep(0.05)
        self.assertEqual(calls, calls_after_disconnect)
        await hub.close()

    async def test_late_subscriber_gets_full_snapshot_and_error_is_sanitized(self) -> None:
        hub = SharedSectionHub(default_interval=30)
        calls = 0
        release_failure = asyncio.Event()

        async def factory():
            nonlocal calls
            calls += 1
            if calls > 1:
                await release_failure.wait()
                raise RuntimeError("password=raw")
            return {"items": ["safe"], "total": 1}

        hub.register("files", factory)
        first = hub.stream(["files"])
        initial = await asyncio.wait_for(anext(first), 1)
        await first.aclose()

        reconnect = hub.stream(["files"])
        cached = await asyncio.wait_for(anext(reconnect), 1)
        self.assertEqual(cached["event"], "snapshot")
        self.assertEqual(cached["data"]["data"]["total"], 1)
        release_failure.set()
        hub.invalidate("files")
        failure = await asyncio.wait_for(anext(reconnect), 1)
        serialized = json.dumps(failure["data"])
        self.assertNotIn("password", serialized)
        self.assertEqual(
            failure["data"]["data"]["error"]["code"],
            "collection_failed",
        )
        await reconnect.aclose()
        await hub.close()

    async def test_topics_stay_isolated_and_late_subscriber_never_overlaps_cancellation(self) -> None:
        hub = SharedSectionHub(default_interval=30, heartbeat_interval=1)
        first_started = asyncio.Event()
        calls = 0
        running = 0
        maximum_running = 0

        async def factory(topic: str):
            nonlocal calls, running, maximum_running
            calls += 1
            current = calls
            running += 1
            maximum_running = max(maximum_running, running)
            try:
                if topic == "first" and current == 1:
                    first_started.set()
                    await asyncio.sleep(10)
                return {"topic": topic}
            finally:
                running -= 1

        hub.register("first", lambda: factory("first"))
        hub.register("second", lambda: factory("second"))
        first = hub.stream(["first"])
        second = hub.stream(["second"])
        first_wait = asyncio.create_task(anext(first))
        await asyncio.wait_for(first_started.wait(), 1)
        first_wait.cancel()
        await asyncio.gather(first_wait, return_exceptions=True)
        replacement = hub.stream(["first"])
        replacement_frame = await asyncio.wait_for(anext(replacement), 1)
        second_frame = await asyncio.wait_for(anext(second), 1)
        self.assertEqual(replacement_frame["data"]["topic"], "first")
        self.assertEqual(second_frame["data"]["topic"], "second")
        self.assertEqual(maximum_running, 1)
        await replacement.aclose()
        await second.aclose()
        await hub.close()

    async def test_topic_frames_are_not_delivered_to_other_subscribers(self) -> None:
        hub = SharedSectionHub(default_interval=30, heartbeat_interval=1)

        async def first():
            return {"value": 1}

        async def second():
            return {"value": 2}

        hub.register("first", first)
        hub.register("second", second)
        first_stream = hub.stream(["first"])
        second_stream = hub.stream(["second"])
        first_frame = await asyncio.wait_for(anext(first_stream), 1)
        second_frame = await asyncio.wait_for(anext(second_stream), 1)
        self.assertEqual(first_frame["data"]["topic"], "first")
        self.assertEqual(second_frame["data"]["topic"], "second")
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(anext(first_stream), 0.1)
        await first_stream.aclose()
        await second_stream.aclose()
        await hub.close()

    async def test_all_initial_sections_fit_when_there_are_more_than_queue_slots(self) -> None:
        hub = SharedSectionHub(default_interval=30, queue_size=2, heartbeat_interval=1)
        topics = [f"topic-{index}" for index in range(6)]
        for topic in topics:
            async def snapshot(value=topic):
                return {"topic": value}
            hub.register(topic, snapshot)

        stream = hub.stream(topics)
        received = [await asyncio.wait_for(anext(stream), 1) for _ in topics]
        self.assertEqual(
            {frame["data"]["topic"] for frame in received},
            set(topics),
        )
        await stream.aclose()
        await hub.close()

    async def test_all_initial_job_records_fit_in_small_hub_queue(self) -> None:
        jobs = JobManager(max_workers=1, retention=8)
        for index in range(5):
            async def completed(_context, value=index):
                return {"value": value}
            queued = await jobs.submit_async(f"job-{index}", completed)
            await wait_until(
                lambda job_id=queued["id"]: jobs._records[job_id].status
                == JobStatus.COMPLETED
            )

        hub = SharedSectionHub(
            job_manager=jobs,
            default_interval=30,
            queue_size=1,
            heartbeat_interval=1,
        )
        hub.register("files", lambda: asyncio.sleep(0, result={"ok": True}))
        stream = hub.stream(["files"])
        frames = [await asyncio.wait_for(anext(stream), 1) for _ in range(6)]
        received_ids = {
            frame["data"]["job"]["id"]
            for frame in frames
            if frame["event"] == "job"
        }
        self.assertEqual(received_ids, {row["id"] for row in await jobs.snapshot()})
        await stream.aclose()
        await hub.close()
        await jobs.close()

    async def test_unchanged_collections_refresh_freshness(self) -> None:
        hub = SharedSectionHub(default_interval=0.01, heartbeat_interval=1)
        value = 0

        async def factory():
            nonlocal value
            value += 1
            return {
                "stable": True,
                "refreshedAt": f"2026-01-01T00:00:0{value}Z",
            }

        hub.register(
            "files",
            factory,
            fingerprint_factory=lambda: {"stable": True},
        )
        stream = hub.stream(["files"])
        first = await asyncio.wait_for(anext(stream), 1)
        second = await asyncio.wait_for(anext(stream), 1)
        self.assertEqual(first["event"], "snapshot")
        self.assertEqual(second["event"], "freshness")
        self.assertTrue(second["data"]["unchanged"])
        self.assertFalse(second["data"]["stale"])
        self.assertEqual(
            second["data"]["sourceUpdatedAt"],
            "2026-01-01T00:00:02Z",
        )
        self.assertGreaterEqual(
            second["data"]["collectedAt"],
            first["data"]["collectedAt"],
        )
        await stream.aclose()
        late = hub.stream(["files"])
        cached = await asyncio.wait_for(anext(late), 1)
        self.assertEqual(cached["event"], "snapshot")
        self.assertEqual(
            cached["data"]["collectedAt"],
            second["data"]["collectedAt"],
        )
        self.assertEqual(
            cached["data"]["data"]["refreshedAt"],
            "2026-01-01T00:00:02Z",
        )
        await late.aclose()
        await hub.close()

    async def test_recovery_from_error_emits_fresh_frame_with_same_data(self) -> None:
        hub = SharedSectionHub(default_interval=30, heartbeat_interval=1)
        calls = 0

        async def recover():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("temporary failure")
            return {"stable": True}

        hub.register("files", recover)
        stream = hub.stream(["files"])
        first = await asyncio.wait_for(anext(stream), 1)
        hub.invalidate("files")
        failed = await asyncio.wait_for(anext(stream), 1)
        self.assertTrue(failed["data"]["stale"])
        hub.invalidate("files")
        recovered = await asyncio.wait_for(anext(stream), 1)
        self.assertFalse(recovered["data"]["stale"])
        self.assertEqual(recovered["data"]["changedAt"], first["data"]["changedAt"])
        await stream.aclose()
        await hub.close()

    async def test_invalidation_during_collection_forces_immediate_recollection(self) -> None:
        hub = SharedSectionHub(default_interval=30, heartbeat_interval=1)
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
                return {"version": 1}
            return {"version": 2}

        hub.register("files", factory)
        stream = hub.stream(["files"])
        first_wait = asyncio.create_task(anext(stream))
        await asyncio.wait_for(entered.wait(), 1)
        hub.invalidate("files")
        release.set()
        latest = await asyncio.wait_for(first_wait, 1)
        # The bounded queue keeps only the latest frame for this topic. The
        # stale first result may therefore be replaced before the consumer
        # resumes, but the mutation must force a second collection immediately.
        self.assertEqual(latest["data"]["data"], {"version": 2})
        self.assertEqual(calls, 2)
        await stream.aclose()
        await hub.close()

    async def test_concurrent_direct_refresh_is_coalesced(self) -> None:
        hub = SharedSectionHub(default_interval=30)
        release = asyncio.Event()
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            await release.wait()
            return {"ok": True}

        hub.register("files", factory)
        first = asyncio.create_task(hub.refresh("files"))
        second = asyncio.create_task(hub.refresh("files"))
        await wait_until(lambda: calls == 1)
        release.set()
        await asyncio.gather(first, second)
        self.assertEqual(calls, 1)
        await hub.close()

    async def test_job_progress_is_forwarded_as_sse(self) -> None:
        jobs = JobManager(max_workers=1)
        hub = SharedSectionHub(job_manager=jobs, default_interval=30)
        release = asyncio.Event()

        async def section():
            return {"ok": True}

        async def operation(context):
            context.report(message="Traitement", progress=1, total=2)
            await release.wait()
            return {"done": True}

        hub.register("files", section)
        stream = hub.stream(["files"])
        await asyncio.wait_for(anext(stream), 1)
        await jobs.submit_async("copy", operation)
        job_frame = await asyncio.wait_for(anext(stream), 1)
        while job_frame["data"]["job"]["progress"] != 1:
            job_frame = await asyncio.wait_for(anext(stream), 1)
        self.assertEqual(job_frame["event"], "job")
        self.assertEqual(job_frame["data"]["job"]["progress"], 1)
        release.set()
        await stream.aclose()
        await hub.close()
        await jobs.close()

    def test_sse_formatter_uses_structured_frame(self) -> None:
        encoded = format_sse(
            {
                "event": "heartbeat",
                "id": "4",
                "data": {"type": "heartbeat", "sequence": 4},
            }
        )
        self.assertEqual(
            encoded,
            "id: 4\nevent: heartbeat\n"
            'data: {"type":"heartbeat","sequence":4}\n\n',
        )


if __name__ == "__main__":
    unittest.main()
