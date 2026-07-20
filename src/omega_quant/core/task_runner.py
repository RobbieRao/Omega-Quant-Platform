"""In-process background task runner with progress streaming.

Provides a small thread-pool–backed task system so long-running operations
(GA optimization, history downloads, monte-carlo sims) don't block the FastAPI
event loop. Each task gets a unique id, a progress channel (queue), and a
final result snapshot. Progress events are exposed via SSE in api.py.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any, Callable


@dataclass
class TaskState:
    task_id: str
    kind: str
    status: str = "queued"  # queued | running | completed | failed | cancelled
    progress: float = 0.0
    stage: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    result: Any | None = None
    error: str | None = None
    cancel_requested: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


class TaskHandle:
    """Passed into the task function so it can publish progress."""

    def __init__(self, state: TaskState, queue: Queue) -> None:
        self.state = state
        self._queue = queue

    @property
    def cancelled(self) -> bool:
        return self.state.cancel_requested

    def progress(self, value: float, stage: str = "") -> None:
        self.state.progress = max(0.0, min(1.0, float(value)))
        if stage:
            self.state.stage = stage
        self._queue.put({"type": "progress", "progress": self.state.progress, "stage": self.state.stage})

    def log(self, message: str) -> None:
        self._queue.put({"type": "log", "message": message})

    def update_meta(self, **kwargs: Any) -> None:
        self.state.meta.update(kwargs)
        self._queue.put({"type": "meta", "meta": dict(self.state.meta)})


class TaskRunner:
    def __init__(self, max_workers: int = 4) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="omega-task")
        self._states: dict[str, TaskState] = {}
        self._queues: dict[str, Queue] = {}
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[[TaskHandle], Any], *, meta: dict[str, Any] | None = None) -> TaskState:
        task_id = uuid.uuid4().hex
        state = TaskState(task_id=task_id, kind=kind, meta=dict(meta or {}))
        queue: Queue = Queue()
        handle = TaskHandle(state, queue)
        with self._lock:
            self._states[task_id] = state
            self._queues[task_id] = queue

        def _run() -> None:
            state.status = "running"
            state.started_at = time.time()
            queue.put({"type": "status", "status": "running"})
            try:
                result = fn(handle)
                if state.cancel_requested:
                    state.status = "cancelled"
                    queue.put({"type": "status", "status": "cancelled"})
                else:
                    state.status = "completed"
                    state.result = result
                    state.progress = 1.0
                    queue.put({"type": "complete", "result": result})
            except Exception as exc:  # noqa: BLE001
                state.status = "failed"
                state.error = f"{type(exc).__name__}: {exc}"
                queue.put({"type": "error", "error": state.error})
            finally:
                state.finished_at = time.time()
                queue.put({"type": "end"})

        future = self._executor.submit(_run)
        with self._lock:
            self._futures[task_id] = future
        return state

    def get(self, task_id: str) -> TaskState | None:
        return self._states.get(task_id)

    def list(self, *, limit: int = 50) -> list[TaskState]:
        with self._lock:
            ordered = sorted(self._states.values(), key=lambda s: s.started_at or 0, reverse=True)
        return ordered[:limit]

    def cancel(self, task_id: str) -> bool:
        state = self._states.get(task_id)
        if not state or state.status in {"completed", "failed", "cancelled"}:
            return False
        state.cancel_requested = True
        return True

    def event_stream(self, task_id: str, *, idle_timeout: float = 30.0):
        """Generator yielding SSE-formatted strings until task ends."""

        queue = self._queues.get(task_id)
        if queue is None:
            yield "event: error\ndata: {\"error\":\"unknown task\"}\n\n"
            return
        state = self._states[task_id]
        # Replay current status first
        yield f"event: status\ndata: {{\"status\":\"{state.status}\",\"progress\":{state.progress}}}\n\n"
        last_heartbeat = time.time()
        while True:
            try:
                event = queue.get(timeout=2.0)
            except Empty:
                if time.time() - last_heartbeat > idle_timeout:
                    if state.status in {"completed", "failed", "cancelled"}:
                        return
                    last_heartbeat = time.time()
                yield ":keepalive\n\n"
                continue
            etype = event.get("type", "message")
            import json as _json
            yield f"event: {etype}\ndata: {_json.dumps(event, default=str)}\n\n"
            last_heartbeat = time.time()
            if etype in {"complete", "error", "end"}:
                return


# Module-level singleton, lazily created on first use.
_runner: TaskRunner | None = None


def get_task_runner() -> TaskRunner:
    global _runner
    if _runner is None:
        _runner = TaskRunner(max_workers=4)
    return _runner
