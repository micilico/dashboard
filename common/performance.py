"""Bounded background jobs and shared SSE section collection.

Panel-facing data in this module is public data. Callers must ensure that job
metadata, progress payloads, results, errors and section snapshots contain only
values that are already safe to send to a browser.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
ErrorFactory = Callable[[BaseException], dict[str, str]]
JobFactory = Callable[["JobContext"], Awaitable[T] | T]
SnapshotFactory = Callable[[], Awaitable[Mapping[str, Any]]]
FingerprintFactory = Callable[[], Awaitable[Any] | Any]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def safe_error(
    _exc: BaseException | None = None,
    *,
    code: str = "operation_failed",
    message: str = "L’opération a échoué.",
    recovery: str = "Réessayer",
) -> dict[str, str]:
    """Return a stable error envelope without exception details."""
    return {"code": code, "message": message, "recovery": recovery}


def _public_error(
    factory: ErrorFactory | None,
    exc: BaseException,
    *,
    code: str,
) -> dict[str, str]:
    fallback = safe_error(code=code)
    if factory is None:
        return fallback
    try:
        value = factory(exc)
    except Exception:
        return fallback
    if not isinstance(value, Mapping):
        return fallback
    return {
        key: str(value.get(key, fallback[key]))[:500]
        for key in ("code", "message", "recovery")
    }


def _copy_public(value: Any) -> Any:
    """Copy JSON-shaped data so subscribers cannot mutate manager state."""
    try:
        return copy.deepcopy(value)
    except Exception:
        return None


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


TERMINAL_JOB_STATUSES = {
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.INTERRUPTED,
}


def _frame_key(payload: Mapping[str, Any]) -> str:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return "heartbeat"
    topic = data.get("topic")
    if topic:
        # A lightweight freshness signal may be coalesced with another
        # freshness signal, but must never evict a pending full payload.
        if payload.get("event") == "freshness" or data.get("type") == "freshness":
            return f"freshness:{topic}"
        return f"section:{topic}"
    job = data.get("job")
    if isinstance(job, Mapping) and job.get("id"):
        return f"job:{job['id']}"
    return "heartbeat"


class _LatestFrameQueue(asyncio.Queue[dict[str, Any]]):
    """Bounded queue that coalesces updates for the same topic or job."""

    def __init__(self, maxsize: int) -> None:
        super().__init__(maxsize=maxsize)
        self._keys: list[str] = []

    def put_nowait(self, item: dict[str, Any]) -> None:
        key = _frame_key(item)
        try:
            index = self._keys.index(key)
        except ValueError:
            if self.full():
                # Freshness and heartbeat frames are hints. Keep a changed
                # full state or job transition by evicting one of those hints
                # when backpressure fills the bounded queue.
                lightweight_index = next(
                    (
                        index
                        for index, queued in enumerate(self._queue)
                        if _frame_key(queued).startswith("freshness:")
                        or _frame_key(queued) == "heartbeat"
                    ),
                    None,
                )
                if lightweight_index is None:
                    raise asyncio.QueueFull
                self._remove_at(lightweight_index)
        else:
            self._remove_at(index)
        super().put_nowait(item)
        self._keys.append(key)

    def _remove_at(self, index: int) -> None:
        del self._keys[index]
        del self._queue[index]
        # Match Queue's task accounting when replacing or evicting an item.
        self._unfinished_tasks -= 1
        if self._unfinished_tasks == 0:
            self._finished.set()

    def get_nowait(self) -> dict[str, Any]:
        item = super().get_nowait()
        if self._keys:
            self._keys.pop(0)
        return item


class JobQueueFull(RuntimeError):
    """Raised when the bounded pending queue cannot accept another task."""


class JobNotFound(KeyError):
    """Raised when a job id is unknown or evicted."""


@dataclass(slots=True)
class JobRecord:
    id: str
    key: str
    status: JobStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    message: str = ""
    progress: int = 0
    total: int | None = None
    data: Any = None
    result: Any = None
    error: dict[str, str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "status": self.status.value,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "message": self.message,
            "progress": self.progress,
            "total": self.total,
            "data": _copy_public(self.data),
            "result": _copy_public(self.result),
            "error": _copy_public(self.error),
            "metadata": _copy_public(self.metadata) or {},
        }

    def persisted(self) -> dict[str, Any]:
        payload = self.public()
        if self.status not in TERMINAL_JOB_STATUSES:
            payload["status"] = JobStatus.INTERRUPTED.value
            payload["finishedAt"] = _utc_now()
            payload["error"] = safe_error(
                code="job_interrupted",
                message="La tâche a été interrompue par le redémarrage du service.",
                recovery="Relancer la tâche",
            )
        return payload


@dataclass(slots=True)
class _QueuedJob:
    id: str
    factory: JobFactory[Any]
    threaded: bool
    context: JobContext


class JobContext:
    """Thread-safe progress handle passed to a background factory."""

    def __init__(
        self,
        manager: JobManager,
        job_id: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._manager = manager
        self.job_id = job_id
        self._loop = loop
        self.cancelled = False

    def report(
        self,
        *,
        message: str | None = None,
        progress: int | None = None,
        total: int | None = None,
        data: Any = None,
    ) -> None:
        payload = {
            "message": None if message is None else str(message)[:500],
            "progress": None if progress is None else max(0, int(progress)),
            "total": None if total is None else max(0, int(total)),
            "data": _copy_public(data),
        }
        try:
            self._loop.call_soon_threadsafe(
                self._manager._schedule_progress,
                self.job_id,
                payload,
            )
        except RuntimeError:
            self.cancelled = True

    update = report


class JobManager:
    """Bounded scheduler with active-key deduplication and retention.

    A process restart never re-enqueues a persisted job. Persisted queued and
    running records become interrupted on construction. Persistence accepts
    only JSON-shaped public fields. Progress writes are debounced and one final
    snapshot is flushed by close().
    """

    def __init__(
        self,
        *,
        max_workers: int = 2,
        max_pending: int = 8,
        retention: int = 100,
        queue_size: int = 16,
        state_path: str | Path | None = None,
        persist_interval: float = 1.0,
        error_factory: ErrorFactory | None = None,
    ) -> None:
        if max_workers < 1 or max_pending < 1 or retention < 0 or queue_size < 1:
            raise ValueError("Job manager bounds must be positive")
        self.max_workers = max_workers
        self.max_pending = max_pending
        self.retention = retention
        self.queue_size = queue_size
        self.state_path = Path(state_path) if state_path is not None else None
        self.persist_interval = max(0.05, float(persist_interval))
        self.error_factory = error_factory
        self._queue: asyncio.Queue[_QueuedJob] = asyncio.Queue(maxsize=max_pending)
        self._records: dict[str, JobRecord] = {}
        self._active_by_key: dict[str, str] = {}
        self._job_tasks: dict[str, asyncio.Task[Any]] = {}
        self._contexts: dict[str, JobContext] = {}
        self._pending_progress: dict[str, dict[str, Any]] = {}
        self._progress_task: asyncio.Task[None] | None = None
        self._progress_interval = 0.1
        self._workers: list[asyncio.Task[None]] = []
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()
        self._closing = False
        self._closed = False
        self._persist_event = asyncio.Event()
        self._persist_task: asyncio.Task[None] | None = None
        self._persist_lock = threading.Lock()
        self._persist_revision = 0
        self._persisted_revision = 0
        self._thread_pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="dashboard-job",
        )
        self._load_persisted()

    def __len__(self) -> int:
        return len(self._records)

    def _load_persisted(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            rows = payload.get("jobs", []) if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                return
            for row in rows[-max(1, self.retention) :]:
                try:
                    self._restore_record(row)
                except (TypeError, ValueError, OverflowError):
                    # A damaged record should not hide valid records that
                    # follow it in the same persisted snapshot.
                    continue
        except (OSError, ValueError, TypeError):
            logger.warning("Unable to load persisted background jobs")

    def _restore_record(self, row: Any) -> None:
        if (
            not isinstance(row, Mapping)
            or not isinstance(row.get("id"), str)
            or not row["id"]
            or len(row["id"]) > 200
        ):
            return
        status_value = row.get("status")
        if status_value in (JobStatus.QUEUED.value, JobStatus.RUNNING.value):
            status = JobStatus.INTERRUPTED
            finished_at = row.get("finishedAt") or _utc_now()
            error = safe_error(
                code="job_interrupted",
                message="La tâche a été interrompue par le redémarrage du service.",
                recovery="Relancer la tâche",
            )
        else:
            try:
                status = JobStatus(status_value)
            except (TypeError, ValueError):
                return
            finished_at = row.get("finishedAt")
            raw_error = row.get("error")
            error = (
                {
                    key: str(raw_error.get(key, ""))[:500]
                    for key in ("code", "message", "recovery")
                }
                if isinstance(raw_error, Mapping)
                else None
            )
        job_id = row["id"]
        self._records[job_id] = JobRecord(
            id=job_id,
            key=str(row.get("key", job_id))[:200],
            status=status,
            created_at=str(row.get("createdAt") or _utc_now()),
            started_at=row.get("startedAt"),
            finished_at=finished_at,
            message=str(row.get("message") or "")[:500],
            progress=self._restored_integer(row.get("progress"), default=0),
            total=self._restored_integer(row.get("total"), default=None),
            data=_copy_public(row.get("data")),
            result=_copy_public(row.get("result")),
            error=error,
            metadata=(
                _copy_public(row.get("metadata"))
                if isinstance(row.get("metadata"), dict)
                else {}
            ),
        )
        self._trim_terminal_records()

    @staticmethod
    def _restored_integer(value: Any, *, default: int | None) -> int | None:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        try:
            return max(0, int(value))
        except (TypeError, ValueError, OverflowError):
            return default

    async def submit_async(
        self,
        key: str,
        factory: JobFactory[T],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._submit(key, factory, threaded=False, metadata=metadata)

    async def submit_thread(
        self,
        key: str,
        factory: JobFactory[T],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._submit(key, factory, threaded=True, metadata=metadata)

    async def _submit(
        self,
        key: str,
        factory: JobFactory[Any],
        *,
        threaded: bool,
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if self._closed or self._closing:
            raise RuntimeError("Job manager is closed")
        normalized_key = str(key).strip()[:200]
        if not normalized_key:
            raise ValueError("A job key is required")
        self._ensure_runtime()
        async with self._lock:
            active_id = self._active_by_key.get(normalized_key)
            if active_id and active_id in self._records:
                return self._records[active_id].public()
            if self._queue.full():
                raise JobQueueFull("The background job queue is full")
            job_id = uuid.uuid4().hex
            record = JobRecord(
                id=job_id,
                key=normalized_key,
                status=JobStatus.QUEUED,
                created_at=_utc_now(),
                metadata=_copy_public(dict(metadata or {})) or {},
            )
            context = JobContext(self, job_id, asyncio.get_running_loop())
            self._records[job_id] = record
            self._contexts[job_id] = context
            self._active_by_key[normalized_key] = job_id
            self._trim_terminal_records()
            self._queue.put_nowait(_QueuedJob(job_id, factory, threaded, context))
            public = record.public()
        await self._publish(public)
        self._signal_persist()
        return public

    async def _apply_progress(
        self,
        job_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        record = self._records.get(job_id)
        if record is None or record.status in TERMINAL_JOB_STATUSES:
            return
        if payload.get("message") is not None:
            record.message = str(payload["message"])[:500]
        if payload.get("progress") is not None:
            record.progress = max(0, int(payload["progress"]))
        if payload.get("total") is not None:
            record.total = max(0, int(payload["total"]))
        if "data" in payload:
            record.data = _copy_public(payload["data"])
        await self._publish(record.public())
        self._signal_persist()

    def _schedule_progress(
        self,
        job_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        if self._closing or self._closed:
            return
        pending = self._pending_progress.get(job_id, {})
        for field_name in ("message", "progress", "total"):
            if payload.get(field_name) is not None:
                pending[field_name] = payload[field_name]
        if "data" in payload:
            pending["data"] = _copy_public(payload["data"])
        self._pending_progress[job_id] = pending
        if self._progress_task is None or self._progress_task.done():
            self._progress_task = asyncio.create_task(
                self._flush_progress_loop(),
                name="dashboard-job-progress",
            )

    async def _flush_progress_loop(self) -> None:
        try:
            while self._pending_progress and not self._closing:
                await asyncio.sleep(self._progress_interval)
                batch = self._pending_progress
                self._pending_progress = {}
                for job_id, payload in batch.items():
                    await self._apply_progress(job_id, payload)
        finally:
            self._progress_task = None

    async def _flush_pending_progress(self, job_id: str) -> None:
        payload = self._pending_progress.pop(job_id, None)
        if payload is not None:
            await self._apply_progress(job_id, payload)

    async def _worker(self, worker_index: int) -> None:
        del worker_index
        while not self._closing:
            queued = await self._queue.get()
            try:
                await self._run_job(queued)
            finally:
                self._queue.task_done()

    async def _run_job(self, queued: _QueuedJob) -> None:
        record = self._records.get(queued.id)
        if record is None or record.status in TERMINAL_JOB_STATUSES:
            return
        record.status = JobStatus.RUNNING
        record.started_at = _utc_now()
        await self._publish(record.public())
        self._signal_persist()

        async def invoke() -> Any:
            if queued.threaded:
                loop = asyncio.get_running_loop()
                future = loop.run_in_executor(
                    self._thread_pool,
                    queued.factory,
                    queued.context,
                )
                try:
                    value = await asyncio.shield(future)
                except asyncio.CancelledError:
                    # Cancelling cannot stop an in-flight filesystem mutation.
                    # Keep the worker slot and active key until the thread
                    # actually returns.
                    await asyncio.shield(future)
                    raise
                return await value if inspect.isawaitable(value) else value
            value = queued.factory(queued.context)
            return await value if inspect.isawaitable(value) else value

        invoke_task = asyncio.create_task(invoke())
        self._job_tasks[queued.id] = invoke_task
        try:
            result = await invoke_task
            await self._flush_pending_progress(queued.id)
            record.status = JobStatus.COMPLETED
            record.progress = (
                record.total if record.total is not None else record.progress
            )
            record.result = _copy_public(result)
            record.message = record.message or "Tâche terminée."
        except asyncio.CancelledError:
            if record.status not in TERMINAL_JOB_STATUSES:
                record.status = JobStatus.INTERRUPTED
                record.error = safe_error(
                    code="job_interrupted",
                    message="La tâche a été interrompue.",
                    recovery="Relancer la tâche",
                )
                record.message = "Tâche interrompue."
            if self._closing:
                raise
        except BaseException as exc:
            record.status = JobStatus.FAILED
            record.error = _public_error(
                self.error_factory,
                exc,
                code="job_failed",
            )
            record.message = record.error["message"]
        finally:
            self._job_tasks.pop(queued.id, None)
            self._contexts.pop(queued.id, None)
            if record.status in TERMINAL_JOB_STATUSES:
                record.finished_at = _utc_now()
                self._active_by_key.pop(record.key, None)
                self._trim_terminal_records()
            await self._publish(
                self._records.get(record.id, record).public()
            )
            self._signal_persist()

    async def get(self, job_id: str) -> dict[str, Any]:
        record = self._records.get(job_id)
        if record is None:
            raise JobNotFound(job_id)
        return record.public()

    async def snapshot(self) -> list[dict[str, Any]]:
        return [record.public() for record in self._ordered_records()]

    async def cancel(self, job_id: str) -> dict[str, Any]:
        record = self._records.get(job_id)
        if record is None:
            raise JobNotFound(job_id)
        if record.status in TERMINAL_JOB_STATUSES:
            return record.public()
        task = self._job_tasks.get(job_id)
        context = self._contexts.get(job_id)
        if context is not None:
            context.cancelled = True
        if task is not None:
            task.cancel()
        else:
            record.status = JobStatus.INTERRUPTED
            record.finished_at = _utc_now()
            record.message = "Tâche interrompue."
            record.error = safe_error(
                code="job_interrupted",
                message="La tâche a été interrompue.",
                recovery="Relancer la tâche",
            )
            self._active_by_key.pop(record.key, None)
            await self._publish(record.public())
            self._signal_persist()
        return record.public()

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        queue = _LatestFrameQueue(
            maxsize=max(self.queue_size, len(self._records), 1)
        )
        self._subscribers.add(queue)
        try:
            for record in self._ordered_records():
                self._put_latest(queue, self._job_frame(record))
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    async def _publish(self, payload: Mapping[str, Any]) -> None:
        if not self._subscribers:
            return
        frame = {
            "type": "job",
            "job": _copy_public(dict(payload)),
        }
        for queue in tuple(self._subscribers):
            self._put_latest(queue, frame)

    @staticmethod
    def _put_latest(
        queue: asyncio.Queue[dict[str, Any]],
        payload: dict[str, Any],
    ) -> None:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            # Preserve another job's initial state. The custom queue replaces
            # updates for the same job in place when possible.
            pass

    def _ensure_runtime(self) -> None:
        if not self._workers:
            for index in range(self.max_workers):
                self._workers.append(
                    asyncio.create_task(self._worker(index))
                )
        if self.state_path is not None and self._persist_task is None:
            self._persist_task = asyncio.create_task(self._persist_loop())

    def _ordered_records(self) -> list[JobRecord]:
        return sorted(self._records.values(), key=lambda item: item.created_at)

    def _trim_terminal_records(self) -> None:
        terminals = [
            record
            for record in self._ordered_records()
            if record.status in TERMINAL_JOB_STATUSES
        ]
        excess = len(terminals) - self.retention
        for record in terminals[:max(0, excess)]:
            self._records.pop(record.id, None)

    def _signal_persist(self) -> None:
        if self.state_path is not None:
            self._persist_event.set()

    async def _persist_loop(self) -> None:
        while not self._closing:
            await self._persist_event.wait()
            self._persist_event.clear()
            await asyncio.sleep(self.persist_interval)
            await self._flush_persist()

    async def _flush_persist(self) -> None:
        if self.state_path is None:
            return
        self._persist_revision += 1
        revision = self._persist_revision
        payload = {
            "version": 1,
            "jobs": [row.persisted() for row in self._ordered_records()],
        }
        await asyncio.to_thread(self._write_state, payload, revision)

    def _write_state(self, payload: Mapping[str, Any], revision: int) -> None:
        assert self.state_path is not None
        with self._persist_lock:
            # A cancelled asyncio task cannot stop a thread that is already
            # writing. Revisions let the final close snapshot win even when an
            # older write acquires the filesystem lock afterwards.
            if revision < self._persisted_revision:
                return
            try:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.state_path.with_name(
                    f".{self.state_path.name}.{os.getpid()}.{revision}.tmp"
                )
                temporary.write_text(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                os.replace(temporary, self.state_path)
                self._persisted_revision = revision
            except (OSError, TypeError, ValueError):
                logger.warning("Unable to persist background job state")

    async def close(self) -> None:
        if self._closed:
            return
        self._closing = True
        # Preserve the latest reported counters before terminal state replaces
        # queued/running jobs with an interrupted result.
        for job_id, payload in tuple(self._pending_progress.items()):
            await self._apply_progress(job_id, payload)
        self._pending_progress.clear()
        for record in self._records.values():
            if record.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                context = self._contexts.get(record.id)
                if context is not None:
                    context.cancelled = True
                record.status = JobStatus.INTERRUPTED
                record.finished_at = _utc_now()
                record.message = "Tâche interrompue."
                record.error = safe_error(
                    code="job_interrupted",
                    message="La tâche a été interrompue par l’arrêt du service.",
                    recovery="Relancer la tâche",
                )
                self._active_by_key.pop(record.key, None)
        for task in self._job_tasks.values():
            task.cancel()
        if self._progress_task is not None:
            self._progress_task.cancel()
        for task in self._workers:
            task.cancel()
        pending = [
            *self._workers,
            *self._job_tasks.values(),
        ]
        if self._progress_task is not None:
            pending.append(self._progress_task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._workers.clear()
        self._job_tasks.clear()
        self._contexts.clear()
        self._progress_task = None
        if self._persist_task is not None:
            self._persist_task.cancel()
            await asyncio.gather(self._persist_task, return_exceptions=True)
            self._persist_task = None
        await asyncio.to_thread(self._thread_pool.shutdown, wait=True)
        await self._flush_persist()
        self._closed = True
        self._closing = False


@dataclass(slots=True)
class _Section:
    topic: str
    factory: SnapshotFactory
    interval: float
    error_factory: ErrorFactory | None
    fingerprint_factory: FingerprintFactory | None = None
    subscribers: int = 0
    task: asyncio.Task[None] | None = None
    invalidate_event: asyncio.Event = field(default_factory=asyncio.Event)
    latest_frame: dict[str, Any] | None = None
    latest_hash: str | None = None
    latest_data: Mapping[str, Any] | None = None
    sequence: int = 0
    collection_count: int = 0
    changed_at: str | None = None
    failed: bool = False
    collect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SharedSectionHub:
    """Collect each section once and fan snapshots out over SSE frames.

    Collection exists only while a browser is subscribed to that topic. The
    next collection starts interval seconds after the previous one starts and
    never overlaps it. Late and reconnecting subscribers receive the retained
    full state immediately.
    """

    def __init__(
        self,
        *,
        job_manager: JobManager | None = None,
        default_interval: float = 0.5,
        queue_size: int = 4,
        heartbeat_interval: float = 15.0,
        max_error_backoff: float = 30.0,
        error_factory: ErrorFactory | None = None,
    ) -> None:
        if default_interval < 0 or queue_size < 1 or heartbeat_interval <= 0:
            raise ValueError("Invalid shared section hub configuration")
        self.job_manager = job_manager
        self.default_interval = float(default_interval)
        self.queue_size = queue_size
        self.heartbeat_interval = float(heartbeat_interval)
        self.max_error_backoff = max(
            self.default_interval,
            float(max_error_backoff),
        )
        self.error_factory = error_factory
        self._sections: dict[str, _Section] = {}
        self._subscribers: dict[
            asyncio.Queue[dict[str, Any]],
            frozenset[str],
        ] = {}
        self._job_task: asyncio.Task[None] | None = None
        self._sequence = 0
        self._running = False
        self._closed = False

    def register(
        self,
        topic: str,
        snapshot_factory: SnapshotFactory,
        *,
        interval: float | None = None,
        error_factory: ErrorFactory | None = None,
        fingerprint_factory: FingerprintFactory | None = None,
    ) -> None:
        normalized = self._normalize_topic(topic)
        if normalized in self._sections:
            raise ValueError(f"Section already registered: {normalized}")
        if not callable(snapshot_factory):
            raise TypeError("snapshot_factory must be callable")
        if fingerprint_factory is not None and not callable(fingerprint_factory):
            raise TypeError("fingerprint_factory must be callable")
        selected_interval = (
            self.default_interval if interval is None else float(interval)
        )
        if selected_interval < 0:
            raise ValueError("interval must be non-negative")
        self._sections[normalized] = _Section(
            normalized,
            snapshot_factory,
            selected_interval,
            error_factory or self.error_factory,
            fingerprint_factory,
        )

    register_section = register

    def subscriber_count(self, topic: str) -> int:
        """Return the number of streams currently using a section."""
        return self._sections[self._normalize_topic(topic)].subscribers

    async def unregister(self, topic: str) -> bool:
        """Forget an idle section without racing an active stream."""
        normalized = self._normalize_topic(topic)
        section = self._sections.get(normalized)
        if section is None:
            return True
        if section.subscribers:
            return False
        await self._cancel_section_task(section)
        self._sections.pop(normalized, None)
        return True

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("Shared section hub is closed")
        self._running = True

    async def stop(self) -> None:
        self._running = False
        tasks = [
            section.task
            for section in self._sections.values()
            if section.task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for section in self._sections.values():
            section.task = None

    async def close(self) -> None:
        await self.stop()
        self._closed = True
        self._subscribers.clear()
        if self._job_task is not None:
            self._job_task.cancel()
            await asyncio.gather(self._job_task, return_exceptions=True)
            self._job_task = None

    def invalidate(self, topic: str) -> None:
        section = self._sections[self._normalize_topic(topic)]
        section.invalidate_event.set()

    async def refresh(self, topic: str) -> dict[str, Any] | None:
        """Collect a topic immediately, primarily for tests and mutations."""
        section = self._sections[self._normalize_topic(topic)]
        previous = section.collection_count
        if section.subscribers == 0:
            await self._collect_once(section, emit=True)
        else:
            self.invalidate(section.topic)
            for _ in range(100):
                if section.collection_count > previous:
                    break
                await asyncio.sleep(0.05)
        return _copy_public(section.latest_frame)

    def snapshot(self, topic: str) -> dict[str, Any] | None:
        section = self._sections[self._normalize_topic(topic)]
        return _copy_public(section.latest_frame)

    async def stream(
        self,
        topics: list[str] | tuple[str, ...] | set[str],
    ) -> AsyncIterator[dict[str, Any]]:
        if self._closed:
            raise RuntimeError("Shared section hub is closed")
        normalized = tuple(
            dict.fromkeys(self._normalize_topic(topic) for topic in topics)
        )
        unknown = [
            topic for topic in normalized if topic not in self._sections
        ]
        if unknown:
            raise KeyError(f"Unknown section: {unknown[0]}")
        initial_job_records = (
            await self.job_manager.snapshot()
            if self.job_manager is not None
            else []
        )
        job_capacity = (
            self.job_manager.retention
            + self.job_manager.max_workers
            + self.job_manager.max_pending
            if self.job_manager is not None
            else 0
        )
        queue = _LatestFrameQueue(
            maxsize=max(
                self.queue_size,
                2 * len(normalized) + job_capacity + 1,
                len(normalized) + len(initial_job_records),
                1,
            )
        )
        self._subscribers[queue] = frozenset(normalized)
        acquired: list[str] = []
        try:
            self._running = True
            for topic in normalized:
                section = self._sections[topic]
                if section.latest_frame is not None:
                    latest = _copy_public(section.latest_frame) or {}
                    self._put_latest(
                        queue,
                        self._as_snapshot_frame(latest),
                    )
                section.subscribers += 1
                acquired.append(topic)
                await self._ensure_section_task(section)
            self._ensure_job_task()
            for record in initial_job_records:
                self._put_latest(queue, self._job_frame(record))
            while True:
                try:
                    frame = await asyncio.wait_for(
                        queue.get(),
                        timeout=self.heartbeat_interval,
                    )
                except TimeoutError:
                    frame = self._heartbeat_frame()
                yield frame
        finally:
            self._subscribers.pop(queue, None)
            for topic in acquired:
                section = self._sections[topic]
                section.subscribers = max(0, section.subscribers - 1)
                if section.subscribers == 0:
                    await self._cancel_section_task(section)

    async def _collect_once(self, section: _Section, *, emit: bool) -> None:
        if section.collect_lock.locked():
            async with section.collect_lock:
                pass
            return
        async with section.collect_lock:
            await self._collect_once_locked(section, emit=emit)

    async def _collect_once_locked(
        self,
        section: _Section,
        *,
        emit: bool,
    ) -> None:
        try:
            value = section.factory()
            data = await value if inspect.isawaitable(value) else value
            if not isinstance(data, Mapping):
                raise TypeError(
                    "snapshot factory returned non-mapping data"
                )
            public_data = _copy_public(dict(data))
            if not isinstance(public_data, dict):
                raise TypeError("snapshot data is not serializable")
            fingerprint = section.fingerprint_factory() if section.fingerprint_factory else public_data
            fingerprint = await fingerprint if inspect.isawaitable(fingerprint) else fingerprint
            encoded = json.dumps(
                fingerprint,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            digest = hashlib.sha256(
                encoded.encode("utf-8")
            ).hexdigest()
            was_failed = section.failed
            changed = digest != section.latest_hash
            collected_at = _utc_now()
            if changed or section.changed_at is None:
                section.changed_at = collected_at
            section.latest_data = public_data
            section.latest_hash = digest
            section.failed = False
            if changed or was_failed or section.latest_frame is None:
                event = "snapshot" if section.sequence == 0 else "section"
                section.latest_frame = self._section_frame(
                    section,
                    event,
                    public_data,
                    collected_at=collected_at,
                    changed_at=section.changed_at,
                )
                if emit:
                    self._broadcast(section.latest_frame)
            else:
                # Keep a current full state for late subscribers without
                # retransmitting a large unchanged payload to active tabs.
                assert section.latest_frame is not None
                latest = _copy_public(section.latest_frame) or {}
                latest_data = latest.get("data")
                if isinstance(latest_data, dict):
                    # Fingerprints may intentionally omit volatile metadata.
                    # Retain the freshest complete payload for late subscribers
                    # without retransmitting it to current subscribers.
                    latest_data["data"] = public_data
                    latest_data["collectedAt"] = collected_at
                    latest_data["stale"] = False
                section.latest_frame = latest
                if emit:
                    self._broadcast(
                        self._freshness_frame(
                            section,
                            collected_at,
                            stale=False,
                        )
                    )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            section.failed = True
            error = _public_error(
                section.error_factory,
                exc,
                code="collection_failed",
            )
            fallback = (
                dict(section.latest_data)
                if isinstance(section.latest_data, Mapping)
                else {}
            )
            payload = {**fallback, "error": error}
            event = "snapshot" if section.sequence == 0 else "section"
            section.latest_frame = self._section_frame(
                section,
                event,
                payload,
                stale=True,
                changed_at=section.changed_at,
            )
            if emit:
                self._broadcast(section.latest_frame)
        finally:
            section.collection_count += 1

    async def _collect_loop(self, section: _Section) -> None:
        failures = 0
        try:
            while (
                section.subscribers > 0
                and self._running
                and not self._closed
            ):
                started = time.monotonic()
                # A mutation can arrive while the factory is running. Clear
                # before collection so that event survives until the wait.
                section.invalidate_event.clear()
                await self._collect_once(section, emit=True)
                failures = failures + 1 if section.failed else 0
                configured = section.interval * (2 ** min(failures, 6))
                delay = min(self.max_error_backoff, configured)
                elapsed = time.monotonic() - started
                wait_for = max(0.0, delay - elapsed)
                if wait_for:
                    try:
                        await asyncio.wait_for(
                            section.invalidate_event.wait(),
                            timeout=wait_for,
                        )
                    except TimeoutError:
                        pass
        except asyncio.CancelledError:
            raise
        finally:
            if section.task is asyncio.current_task():
                section.task = None

    def _section_frame(
        self,
        section: _Section,
        event: str,
        data: Mapping[str, Any],
        *,
        stale: bool = False,
        collected_at: str | None = None,
        changed_at: str | None = None,
    ) -> dict[str, Any]:
        self._sequence += 1
        section.sequence = self._sequence
        timestamp = collected_at or _utc_now()
        payload: dict[str, Any] = {
            "type": event,
            "topic": section.topic,
            "data": _copy_public(dict(data)),
            "collectedAt": timestamp,
            "sequence": self._sequence,
            "stale": bool(stale),
        }
        if changed_at is not None:
            payload["changedAt"] = changed_at
        return {
            "event": event,
            "id": str(self._sequence),
            "data": payload,
        }

    def _as_snapshot_frame(
        self,
        frame: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = _copy_public(dict(frame.get("data", {}))) or {}
        payload["type"] = "snapshot"
        return {
            "event": "snapshot",
            "id": str(frame.get("id") or payload.get("sequence") or 0),
            "data": payload,
        }

    def _heartbeat_frame(self) -> dict[str, Any]:
        self._sequence += 1
        return {
            "event": "heartbeat",
            "id": str(self._sequence),
            "data": {
                "type": "heartbeat",
                "heartbeatAt": _utc_now(),
                "sequence": self._sequence,
            },
        }

    def _freshness_frame(
        self,
        section: _Section,
        collected_at: str,
        *,
        stale: bool,
    ) -> dict[str, Any]:
        self._sequence += 1
        source_updated_at = self._source_updated_at(section.latest_data)
        data: dict[str, Any] = {
            "type": "freshness",
            "topic": section.topic,
            "collectedAt": collected_at,
            "sequence": self._sequence,
            "stale": bool(stale),
            "unchanged": not stale,
        }
        if source_updated_at is not None:
            data["sourceUpdatedAt"] = source_updated_at
        return {
            "event": "freshness",
            "id": str(self._sequence),
            "data": data,
        }

    @staticmethod
    def _source_updated_at(
        data: Mapping[str, Any] | None,
    ) -> str | None:
        """Return a validated source timestamp without exposing source data."""
        if not isinstance(data, Mapping):
            return None
        for key in (
            "source_scanned_at",
            "sourceScannedAt",
            "refreshedAt",
            "generatedAt",
            "observedAt",
        ):
            value = data.get(key)
            if not isinstance(value, str) or not value or len(value) > 40:
                continue
            try:
                datetime.fromisoformat(
                    value[:-1] + "+00:00" if value.endswith("Z") else value
                )
            except ValueError:
                continue
            return value
        return None

    def _job_frame(
        self,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._sequence += 1
        return {
            "event": "job",
            "id": str(self._sequence),
            "data": {
                "type": "job",
                "job": _copy_public(dict(record)),
            },
        }

    def _broadcast(self, frame: Mapping[str, Any]) -> None:
        payload = frame.get("data")
        topic = payload.get("topic") if isinstance(payload, Mapping) else None
        for queue, topics in tuple(self._subscribers.items()):
            # Heartbeats and jobs are relevant to every subscription. Section
            # frames stay on the queues that requested their topic so a busy
            # topic cannot evict another tab's updates from a bounded queue.
            if topic and frame.get("event") in {"snapshot", "section", "freshness"} and topic not in topics:
                continue
            self._put_latest(
                queue,
                _copy_public(dict(frame)) or {},
            )

    @staticmethod
    def _put_latest(
        queue: asyncio.Queue[dict[str, Any]],
        payload: dict[str, Any],
    ) -> None:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            # Keep the other retained topics/jobs. _LatestFrameQueue replaces
            # updates for the same key before it reaches this fallback.
            pass

    async def _ensure_section_task(self, section: _Section) -> None:
        while True:
            task = section.task
            if task is asyncio.current_task():
                return
            if task is not None and not task.done():
                if not task.cancelling():
                    return
                await asyncio.gather(task, return_exceptions=True)
                if section.task is task:
                    section.task = None
                continue
            if section.subscribers > 0 and not self._closed:
                section.task = asyncio.create_task(self._collect_loop(section))
            return

    @staticmethod
    async def _cancel_section_task(section: _Section) -> None:
        task = section.task
        if task is None or task.done():
            section.task = None
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if section.task is task:
            section.task = None

    def _ensure_job_task(self) -> None:
        if self.job_manager is None or self._job_task is not None:
            return
        self._job_task = asyncio.create_task(self._forward_jobs())

    async def _forward_jobs(self) -> None:
        assert self.job_manager is not None
        try:
            async for payload in self.job_manager.subscribe():
                record = payload.get("job", payload)
                self._broadcast(self._job_frame(record))
        except asyncio.CancelledError:
            raise

    @staticmethod
    def _normalize_topic(topic: str) -> str:
        normalized = str(topic).strip().lower()
        if not normalized or len(normalized) > 80:
            raise ValueError("Invalid section topic")
        return normalized


def format_sse(frame: Mapping[str, Any]) -> str:
    """Encode an SSE frame with one-line JSON data."""
    event = str(frame.get("event") or "message")
    data = json.dumps(
        frame.get("data", {}),
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    lines = []
    if frame.get("id") is not None:
        lines.append(f"id: {frame['id']}")
    lines.extend((f"event: {event}", f"data: {data}", ""))
    return "\n".join(lines) + "\n"


SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
