from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from integrated_agent.runtimes.matrix.rag.knowledge import KnowledgeStore

from .identity import IdentityStore
from .models import (
    MatrixTaskCreate,
    MatrixTaskRequest,
    MatrixTaskResult,
    TaskAccepted,
    TaskSnapshot,
)
from .stores import InMemoryEventStore, InMemoryTaskStore
from .task_persistence import load_task_snapshot, persist_task_snapshot


class TaskWorker(Protocol):
    async def execute_complex_task(self, request: MatrixTaskRequest) -> MatrixTaskResult: ...


class ServiceBusyError(RuntimeError):
    pass


class MatrixTaskFailed(RuntimeError):
    pass


class MatrixTaskService:
    def __init__(
        self,
        *,
        worker: TaskWorker,
        tasks: InMemoryTaskStore,
        events: InMemoryEventStore,
        identity: IdentityStore,
        knowledge: KnowledgeStore | None = None,
        worker_count: int = 4,
        queue_capacity: int = 32,
        logs_root: Path | None = None,
    ) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be positive")
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be positive")
        self.worker = worker
        self.tasks = tasks
        self.events = events
        self.worker_count = worker_count
        self.queue_capacity = queue_capacity
        self.identity = identity
        self.knowledge = knowledge or KnowledgeStore()
        self.logs_root = logs_root
        self._queue: asyncio.Queue[MatrixTaskRequest] = asyncio.Queue(
            maxsize=queue_capacity
        )
        self._runners: list[asyncio.Task[None]] = []

    @property
    def ready(self) -> bool:
        return len(self._runners) == self.worker_count and all(
            not runner.done() for runner in self._runners
        )

    async def start(self) -> None:
        if self.ready:
            return
        self._runners = [
            asyncio.create_task(
                self._run(worker_index),
                name=f"matrix-task-worker-{worker_index}",
            )
            for worker_index in range(self.worker_count)
        ]

    async def stop(self) -> None:
        runners, self._runners = self._runners, []
        for runner in runners:
            runner.cancel()
        for runner in runners:
            with suppress(asyncio.CancelledError):
                await runner

    async def join(self) -> None:
        await self._queue.join()

    async def submit(
        self, command: MatrixTaskCreate, *, user_id: str | None = None
    ) -> TaskAccepted:
        task_id = uuid4().hex
        request = MatrixTaskRequest(
            task_id=task_id,
            user_id=user_id,
            **command.model_dump(mode="json"),
        )
        await self.tasks.create(task_id)
        try:
            self._queue.put_nowait(request)
        except asyncio.QueueFull as exc:
            await self.tasks.delete(task_id)
            raise ServiceBusyError(
                f"task queue is full ({self.queue_capacity})"
            ) from exc
        await self.events.publish(
            task_id,
            "task.submitted",
            {"requester": request.requester, "channel": request.channel},
        )
        return TaskAccepted(
            task_id=task_id,
            task_url=f"/v1/matrix/tasks/{task_id}",
            events_url=f"/v1/matrix/tasks/{task_id}/events",
        )

    async def get(self, task_id: str) -> TaskSnapshot | None:
        snapshot = await self.tasks.get(task_id)
        if snapshot is not None:
            return snapshot
        if self.logs_root is None:
            return None
        loaded = load_task_snapshot(self.logs_root, task_id)
        if loaded is not None:
            await self.tasks.restore(loaded)
        return loaded

    async def _run(self, worker_index: int) -> None:
        while True:
            request = await self._queue.get()
            try:
                await self.tasks.mark_running(request.task_id)
                await self.events.publish(
                    request.task_id,
                    "worker.started",
                    {"worker_index": worker_index},
                )
                result = await self.worker.execute_complex_task(request)
                await self.tasks.complete(request.task_id, result)
                event_type = (
                    "task.failed" if result.status == "failed" else "task.completed"
                )
                await self.events.publish(
                    request.task_id,
                    event_type,
                    {
                        "status": result.status,
                        "snapshot_id": result.snapshot_id,
                        "trace_ref": result.trace_ref,
                        "summary": result.summary,
                    },
                )
            except Exception as exc:
                await self.tasks.fail(request.task_id, str(exc))
                await self.events.publish(
                    request.task_id,
                    "task.failed",
                    {"error_type": type(exc).__name__, "message": str(exc)},
                )
            finally:
                if self.logs_root is not None:
                    snapshot = await self.tasks.get(request.task_id)
                    if snapshot is not None and snapshot.status in {
                        "completed",
                        "failed",
                    }:
                        persist_task_snapshot(self.logs_root, snapshot)
                self._queue.task_done()
