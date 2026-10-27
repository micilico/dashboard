"""Live server and page flow telemetry.

This module only exposes aggregates that are already safe for a browser:
counters, byte volumes, latencies, normalized route labels and known live
topics. Raw paths, query strings, upstream URLs and credentials never leave the
backend.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterable

from common.performance import safe_error

from ..config import (
    FLOWS_SAMPLE_LIMIT,
    FLOWS_WINDOW_SECONDS,
)
from .monitoring import now_iso, storage_snapshot

_STARTED_AT = time.monotonic()
_SAMPLE_MIN_INTERVAL_SECONDS = 1.5
_MAX_ROUTE_BUCKETS = 8
_ACTIVE_TORRENT_STATES = {
    "downloading",
    "forcedDL",
    "metaDL",
    "uploading",
    "forcedUP",
    "stalledDL",
    "stalledUP",
    "checkingDL",
    "checkingUP",
    "checkingResumeData",
    "queuedDL",
    "queuedUP",
}


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _public_error(code: str, message: str) -> dict[str, str]:
    return safe_error(code=code, message=message, recovery="Réessayer")


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return ordered[index]


class RequestFlowMonitor:
    """Aggregate dashboard HTTP traffic over a bounded rolling window."""

    def __init__(
        self,
        *,
        window_seconds: float = FLOWS_WINDOW_SECONDS,
        max_samples: int = 4000,
        prefixes: Iterable[str] = (),
    ) -> None:
        self.window_seconds = max(5.0, float(window_seconds))
        self.max_samples = max(100, int(max_samples))
        self._prefixes = tuple(
            sorted(
                {str(prefix).rstrip("/") for prefix in prefixes if str(prefix).strip()},
                key=len,
                reverse=True,
            )
        )
        self._samples: deque[tuple[float, str, str, int, float, int]] = deque(
            maxlen=self.max_samples
        )
        self._lock = threading.Lock()
        self._total = 0
        self._errors = 0
        self._bytes = 0

    def label_for(
        self,
        route_path: str,
        request_path: str | None = None,
    ) -> str | None:
        template = str(route_path or "")
        path = str(request_path or template)
        if not path or not template:
            return "autre"
        if path.endswith("/healthz") or path.endswith("/readyz"):
            return None
        if path.rstrip("/").endswith("/api/events"):
            return None
        if path.endswith("/static") or "/static/" in path:
            return "/static/*"

        def strip_prefix(value: str) -> str:
            for prefix in self._prefixes:
                if value == prefix:
                    return "/"
                if value.startswith(prefix + "/"):
                    return value[len(prefix):] or "/"
            return value

        path = strip_prefix(path)
        template = strip_prefix(template)
        if "{" in template:
            static_part = template.split("{", 1)[0].rstrip("/")
            if static_part:
                index = path.find(static_part)
                if index >= 0:
                    base = path[:index]
                    suffix = re.sub(r"\{[^}]+\}", ":id", template[len(static_part):])
                    label = f"{base}{static_part}{suffix}"
                    return label or "/"
            return re.sub(r"\{[^}]+\}", ":id", template) or "/"
        parts = [part for part in path.split("/") if part]
        label = "/".join(parts)
        return f"/{label}" if label else "/"

    def record(
        self,
        *,
        route_path: str,
        method: str,
        status: int,
        duration_ms: float,
        bytes_out: int,
        request_path: str | None = None,
    ) -> None:
        label = self.label_for(route_path, request_path)
        if label is None:
            return
        now = time.monotonic()
        with self._lock:
            self._total += 1
            self._bytes += _non_negative_int(bytes_out)
            if int(status) >= 400:
                self._errors += 1
            self._samples.append(
                (
                    now,
                    label,
                    str(method or "GET").upper()[:8],
                    int(status),
                    max(0.0, float(duration_ms)),
                    _non_negative_int(bytes_out),
                )
            )
            self._purge_locked(now)

    def _purge_locked(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            self._purge_locked(now)
            samples = list(self._samples)
            total = self._total
            errors_since_start = self._errors
            bytes_since_start = self._bytes

        buckets: dict[tuple[str, str], dict[str, Any]] = {}
        durations: list[float] = []
        window_errors = 0
        window_bytes = 0
        for _ts, route, method, status, duration, bytes_out in samples:
            key = (method, route)
            bucket = buckets.setdefault(
                key,
                {
                    "route": route,
                    "method": method,
                    "count": 0,
                    "durationTotal": 0.0,
                    "bytesOut": 0,
                    "errors": 0,
                },
            )
            bucket["count"] += 1
            bucket["durationTotal"] += duration
            bucket["bytesOut"] += bytes_out
            if status >= 400:
                bucket["errors"] += 1
                window_errors += 1
            durations.append(duration)
            window_bytes += bytes_out

        routes = [
            {
                "route": bucket["route"],
                "method": bucket["method"],
                "count": bucket["count"],
                "averageLatencyMs": round(
                    bucket["durationTotal"] / max(1, bucket["count"]), 1
                ),
                "bytesOut": bucket["bytesOut"],
                "errors": bucket["errors"],
            }
            for bucket in buckets.values()
        ]
        routes.sort(key=lambda item: (-item["count"], item["route"], item["method"]))
        window = self.window_seconds
        return {
            "windowSeconds": window,
            "total": len(samples),
            "perMinute": round(len(samples) * 60.0 / window, 1),
            "errors": window_errors,
            "bytesOut": window_bytes,
            "averageLatencyMs": round(sum(durations) / len(durations), 1)
            if durations
            else 0.0,
            "p95LatencyMs": round(_percentile(durations, 0.95), 1),
            "totalSinceStart": total,
            "errorsSinceStart": errors_since_start,
            "bytesSinceStart": bytes_since_start,
            "routes": routes[:_MAX_ROUTE_BUCKETS],
        }

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self._total = 0
            self._errors = 0
            self._bytes = 0


class LiveFlowMonitor:
    """Count SSE connections and frames sent to browsers."""

    def __init__(
        self,
        *,
        window_seconds: float = FLOWS_WINDOW_SECONDS,
        max_events: int = 6000,
    ) -> None:
        self.window_seconds = max(5.0, float(window_seconds))
        self._frame_times: deque[float] = deque(maxlen=max(100, int(max_events)))
        self._connections: dict[str, tuple[str, ...]] = {}
        self._connections_total = 0
        self._frames_total = 0
        self._bytes_total = 0
        self._by_type: dict[str, int] = {}
        self._by_topic: dict[str, int] = {}
        self._last_frame_at: str | None = None
        self._lock = threading.Lock()

    def open(self, topics: Iterable[str]) -> str:
        token = uuid.uuid4().hex
        with self._lock:
            self._connections[token] = tuple(str(topic) for topic in topics)
            self._connections_total += 1
        return token

    def close(self, token: str) -> None:
        with self._lock:
            self._connections.pop(token, None)

    def record_frame(self, frame: Any, bytes_sent: int) -> None:
        payload = frame.get("data") if isinstance(frame, dict) else None
        event = str(
            (frame.get("event") if isinstance(frame, dict) else "")
            or (payload.get("type") if isinstance(payload, dict) else "")
            or "message"
        )[:40]
        topic = str(
            (payload.get("topic") if isinstance(payload, dict) else "") or "system"
        )[:80]
        now = time.monotonic()
        with self._lock:
            self._frame_times.append(now)
            self._frames_total += 1
            self._bytes_total += _non_negative_int(bytes_sent)
            self._by_type[event] = self._by_type.get(event, 0) + 1
            self._by_topic[topic] = self._by_topic.get(topic, 0) + 1
            self._last_frame_at = now_iso()

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            cutoff = now - self.window_seconds
            while self._frame_times and self._frame_times[0] < cutoff:
                self._frame_times.popleft()
            frames_in_window = len(self._frame_times)
            subscribers: dict[str, int] = {}
            for topics in self._connections.values():
                for topic in topics:
                    subscribers[topic] = subscribers.get(topic, 0) + 1
            return {
                "windowSeconds": self.window_seconds,
                "connectionsActive": len(self._connections),
                "connectionsTotal": self._connections_total,
                "framesTotal": self._frames_total,
                "framesInWindow": frames_in_window,
                "framesPerMinute": round(
                    frames_in_window * 60.0 / self.window_seconds, 1
                ),
                "bytesTotal": self._bytes_total,
                "lastFrameAt": self._last_frame_at,
                "byType": dict(sorted(self._by_type.items())),
                "byTopic": dict(sorted(self._by_topic.items())),
                "subscribersByTopic": dict(sorted(subscribers.items())),
            }

    def reset(self) -> None:
        with self._lock:
            self._frame_times.clear()
            self._connections.clear()
            self._connections_total = 0
            self._frames_total = 0
            self._bytes_total = 0
            self._by_type.clear()
            self._by_topic.clear()
            self._last_frame_at = None


class HostNetworkSampler:
    """Read host interface throughput from /proc with a safe fallback."""

    def __init__(
        self,
        *,
        proc_root: str | Path = "/proc",
        interface: str = "",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.proc_root = Path(proc_root)
        self.interface = str(interface or "").strip()
        self._clock = clock
        self._last: tuple[float, str, int, int] | None = None
        self._lock = threading.Lock()

    def _read_counters(self) -> dict[str, tuple[int, int]]:
        text = (self.proc_root / "net" / "dev").read_text(encoding="utf-8")
        counters: dict[str, tuple[int, int]] = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            name, values = line.split(":", 1)
            fields = values.split()
            if len(fields) < 9:
                continue
            metrics = []
            try:
                metrics = [int(fields[0]), int(fields[8])]
            except ValueError:
                continue
            counters[name.strip()] = (metrics[0], metrics[1])
        if not counters:
            raise OSError("no network counters")
        return counters

    def _resolve_interface(self, counters: dict[str, tuple[int, int]]) -> str:
        if self.interface:
            return self.interface if self.interface in counters else ""
        try:
            for line in (self.proc_root / "net" / "route").read_text(
                encoding="utf-8"
            ).splitlines()[1:]:
                fields = line.split()
                if len(fields) >= 2 and fields[1] == "00000000" and fields[0] in counters:
                    return fields[0]
        except OSError:
            pass
        for name in counters:
            if name == "lo" or name.startswith(("docker", "veth", "br-", "virbr")):
                continue
            return name
        return ""

    @staticmethod
    def _unavailable(interface: str = "") -> dict[str, Any]:
        return {
            "available": False,
            "interface": interface,
            "receiveBytesPerSecond": 0,
            "transmitBytesPerSecond": 0,
        }

    def sample(self) -> dict[str, Any]:
        with self._lock:
            try:
                counters = self._read_counters()
            except (OSError, ValueError):
                self._last = None
                return self._unavailable(self.interface)
            interface = self._resolve_interface(counters)
            if not interface:
                self._last = None
                return self._unavailable(self.interface)
            receive, transmit = counters[interface]
            now = self._clock()
            receive_rate = 0
            transmit_rate = 0
            if self._last is not None:
                previous_time, previous_interface, previous_receive, previous_transmit = self._last
                elapsed = now - previous_time
                if (
                    previous_interface == interface
                    and 0 < elapsed <= 30.0
                ):
                    receive_rate = max(0, receive - previous_receive) / elapsed
                    transmit_rate = max(0, transmit - previous_transmit) / elapsed
            self._last = (now, interface, receive, transmit)
            return {
                "available": True,
                "interface": interface,
                "receiveBytesPerSecond": int(receive_rate),
                "transmitBytesPerSecond": int(transmit_rate),
            }

    def reset(self) -> None:
        with self._lock:
            self._last = None


class FlowHistory:
    """Bounded in-memory sample history for the live sparkline."""

    def __init__(
        self,
        *,
        limit: int = FLOWS_SAMPLE_LIMIT,
        min_interval_seconds: float = _SAMPLE_MIN_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._samples: deque[dict[str, Any]] = deque(maxlen=max(2, int(limit)))
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._clock = clock
        self._last_recorded_at: float | None = None
        self._lock = threading.Lock()

    def record(self, sample: dict[str, Any]) -> bool:
        now = self._clock()
        with self._lock:
            if self._last_recorded_at is not None:
                elapsed = now - self._last_recorded_at
                if elapsed > 30.0:
                    # A long pause (page closed) must not draw a fake flat line.
                    self._samples.clear()
                elif elapsed < self.min_interval_seconds:
                    return False
            self._samples.append(dict(sample))
            self._last_recorded_at = now
            return True

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(sample) for sample in self._samples]

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self._last_recorded_at = None


async def _collect_torrents(qbit: Any) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    if qbit is None:
        return [], _public_error(
            "qbit_unavailable", "qBittorrent n’est pas configuré."
        )
    try:
        values = await qbit.torrents()
    except Exception:
        return [], _public_error(
            "qbit_unavailable", "qBittorrent est momentanément indisponible."
        )
    if not isinstance(values, list):
        return [], None
    return [item for item in values if isinstance(item, dict)], None


async def _collect_transfer(qbit: Any) -> tuple[dict[str, Any], dict[str, str] | None]:
    if qbit is None or not hasattr(qbit, "transfer_info"):
        return {}, None
    try:
        value = await qbit.transfer_info()
    except Exception:
        return {}, _public_error(
            "qbit_transfer_unavailable",
            "Les compteurs de transfert qBittorrent sont indisponibles.",
        )
    return value if isinstance(value, dict) else {}, None


async def _collect_storage(app: Any) -> tuple[dict[str, Any], dict[str, str] | None]:
    try:
        value = await storage_snapshot(app)
    except Exception:
        return {}, _public_error(
            "rclone_unavailable", "Les statistiques rclone sont indisponibles."
        )
    return value if isinstance(value, dict) else {}, None


def _rclone_snapshot(storage: dict[str, Any]) -> dict[str, Any]:
    raw = storage.get("rclone") if isinstance(storage.get("rclone"), dict) else {}
    return {
        "status": str(raw.get("status") or "unknown")[:20],
        "speedBytesPerSecond": _non_negative_int(raw.get("speedBytes")),
        "bytesTransferred": _non_negative_int(raw.get("bytesTransferred")),
        "transfersActive": _non_negative_int(raw.get("transfersActive")),
        "queue": _non_negative_int(raw.get("queue")),
        "errors": _non_negative_int(raw.get("errors")),
    }


def _totals_snapshot(stats_store: Any) -> tuple[dict[str, Any], dict[str, str] | None]:
    if stats_store is None:
        return {
            "downloadedBytes": 0,
            "uploadedBytes": 0,
            "ratio": 0.0,
            "observedDays": 0,
        }, None
    try:
        snapshot = stats_store.snapshot()
        if not isinstance(snapshot, dict):
            raise TypeError("invalid stats snapshot")
    except Exception:
        return {
            "downloadedBytes": 0,
            "uploadedBytes": 0,
            "ratio": 0.0,
            "observedDays": 0,
        }, _public_error(
            "stats_unavailable", "L’historique statistique est indisponible."
        )
    totals = snapshot.get("totals") if isinstance(snapshot.get("totals"), dict) else {}
    try:
        ratio = round(float(totals.get("ratio") or 0.0), 2)
    except (TypeError, ValueError, OverflowError):
        ratio = 0.0
    return {
        "downloadedBytes": _non_negative_int(totals.get("downloaded")),
        "uploadedBytes": _non_negative_int(totals.get("uploaded")),
        "ratio": ratio,
        "observedDays": _non_negative_int(totals.get("observedDays")),
    }, None


def _peak(points: list[dict[str, Any]], current: dict[str, Any], key: str) -> int:
    values = [_non_negative_int(point.get(key)) for point in points]
    values.append(_non_negative_int(current.get(key)))
    return max(values) if values else 0


def _empty_requests() -> dict[str, Any]:
    return {
        "windowSeconds": FLOWS_WINDOW_SECONDS,
        "total": 0,
        "perMinute": 0.0,
        "errors": 0,
        "bytesOut": 0,
        "averageLatencyMs": 0.0,
        "p95LatencyMs": 0.0,
        "routes": [],
    }


def _empty_live() -> dict[str, Any]:
    return {
        "windowSeconds": FLOWS_WINDOW_SECONDS,
        "connectionsActive": 0,
        "connectionsTotal": 0,
        "framesTotal": 0,
        "framesInWindow": 0,
        "framesPerMinute": 0.0,
        "bytesTotal": 0,
        "lastFrameAt": None,
        "byType": {},
        "byTopic": {},
        "subscribersByTopic": {},
    }


async def data_snapshot(app: Any) -> dict[str, Any]:
    """Build the public payload for the « Données » sections of Statistiques."""
    generated_at = now_iso()
    qbit = getattr(app.state, "qbit", None)

    torrents_result, transfer_result, storage_result = await asyncio.gather(
        _collect_torrents(qbit),
        _collect_transfer(qbit),
        _collect_storage(app),
    )
    torrents, torrents_error = torrents_result
    transfer, transfer_error = transfer_result
    storage, storage_error = storage_result

    errors = [
        error
        for error in (torrents_error, transfer_error, storage_error)
        if error is not None
    ]
    totals, totals_error = _totals_snapshot(getattr(app.state, "stats", None))
    if totals_error is not None:
        errors.append(totals_error)

    derived_download = sum(
        _non_negative_int(item.get("downloadSpeed")) for item in torrents
    )
    derived_upload = sum(
        _non_negative_int(item.get("uploadSpeed")) for item in torrents
    )
    download_speed = (
        _non_negative_int(transfer.get("downloadSpeedBytes"))
        if "downloadSpeedBytes" in transfer
        else derived_download
    )
    upload_speed = (
        _non_negative_int(transfer.get("uploadSpeedBytes"))
        if "uploadSpeedBytes" in transfer
        else derived_upload
    )
    active_torrents = sum(
        1
        for item in torrents
        if str(item.get("state") or "") in _ACTIVE_TORRENT_STATES
    )
    connected_seeders = sum(
        _non_negative_int(item.get("seeders")) for item in torrents
    )
    connected_leechers = sum(
        _non_negative_int(item.get("leechers")) for item in torrents
    )

    sampler = getattr(app.state, "host_network", None)
    if sampler is not None:
        try:
            network = sampler.sample()
        except Exception:
            network = HostNetworkSampler._unavailable()
    else:
        network = HostNetworkSampler._unavailable()
    if not isinstance(network, dict):
        network = HostNetworkSampler._unavailable()
    rclone = _rclone_snapshot(storage)
    if rclone["status"] == "error" and storage_error is None:
        errors.append(
            _public_error(
                "rclone_unavailable",
                "Les statistiques rclone sont indisponibles.",
            )
        )

    sample = {
        "at": generated_at,
        "downloadBytesPerSecond": download_speed,
        "uploadBytesPerSecond": upload_speed,
        "rcloneBytesPerSecond": rclone["speedBytesPerSecond"],
        "hostReceiveBytesPerSecond": _non_negative_int(
            network.get("receiveBytesPerSecond")
        ),
        "hostTransmitBytesPerSecond": _non_negative_int(
            network.get("transmitBytesPerSecond")
        ),
    }
    history_store = getattr(app.state, "flow_history", None)
    if history_store is not None:
        try:
            history_store.record(sample)
            history = history_store.snapshot()
        except Exception:
            history = [sample]
    else:
        history = [sample]

    server = {
        "status": "degraded" if errors else "ok",
        "network": network,
        "qbit": {
            "status": "degraded" if torrents_error or transfer_error else "ok",
            "downloadBytesPerSecond": download_speed,
            "uploadBytesPerSecond": upload_speed,
            "sessionDownloadedBytes": _non_negative_int(
                transfer.get("sessionDownloadedBytes")
            ),
            "sessionUploadedBytes": _non_negative_int(
                transfer.get("sessionUploadedBytes")
            ),
            "downloadLimitBytes": _non_negative_int(
                transfer.get("downloadLimitBytes")
            ),
            "uploadLimitBytes": _non_negative_int(transfer.get("uploadLimitBytes")),
            "connectionStatus": str(
                transfer.get("connectionStatus") or "inconnu"
            )[:40],
            "activeTorrents": active_torrents,
            "connectedSeeders": connected_seeders,
            "connectedLeechers": connected_leechers,
        },
        "rclone": rclone,
        "totals": totals,
        "history": history,
        "peaks": {
            "downloadBytesPerSecond": _peak(
                history, sample, "downloadBytesPerSecond"
            ),
            "uploadBytesPerSecond": _peak(history, sample, "uploadBytesPerSecond"),
            "rcloneBytesPerSecond": _peak(
                history, sample, "rcloneBytesPerSecond"
            ),
            "hostReceiveBytesPerSecond": _peak(
                history, sample, "hostReceiveBytesPerSecond"
            ),
            "hostTransmitBytesPerSecond": _peak(
                history, sample, "hostTransmitBytesPerSecond"
            ),
        },
        "errors": errors,
    }

    request_monitor = getattr(app.state, "flow_monitor", None)
    live_monitor = getattr(app.state, "live_flows", None)
    requests = _empty_requests()
    if request_monitor is not None:
        try:
            requests = request_monitor.snapshot()
        except Exception:
            requests = _empty_requests()
    live = _empty_live()
    if live_monitor is not None:
        try:
            live = live_monitor.snapshot()
        except Exception:
            live = _empty_live()
    page = {
        "status": "ok",
        "windowSeconds": getattr(
            request_monitor, "window_seconds", FLOWS_WINDOW_SECONDS
        ),
        "requests": requests,
        "live": live,
        "processUptimeSeconds": int(max(0.0, time.monotonic() - _STARTED_AT)),
    }

    return {
        "generatedAt": generated_at,
        "server": server,
        "page": page,
    }
