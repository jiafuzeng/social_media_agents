"""有界队列 + Worker 池的问数任务服务。

队列满时立即抛出 ServiceBusyError（映射为 HTTP 503）；
每个任务最终进入 completed 或 failed。
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Protocol
from uuid import uuid4

from .models import TaskAccepted, TaskCreate, TaskRequest, TaskResult, TaskSnapshot
from .stores import InMemoryEventStore, InMemoryTaskStore


class TaskWorker(Protocol):
    """执行复杂问数工作流的 Worker 协议。"""

    async def execute_complex_task(self, request: TaskRequest) -> TaskResult: ...


class ServiceBusyError(RuntimeError):
    """任务队列已满，客户端应稍后重试。"""

    pass


class QuestionTaskService:
    """受理任务、调度 Worker，并维护任务状态与事件流。"""

    def __init__(
        self,
        *,
        worker: TaskWorker,
        tasks: InMemoryTaskStore,
        events: InMemoryEventStore,
        worker_count: int = 4,
        queue_capacity: int = 32,
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
        self._queue: asyncio.Queue[TaskRequest] = asyncio.Queue(
            maxsize=queue_capacity
        )
        self._runners: list[asyncio.Task[None]] = []

    @property
    def ready(self) -> bool:
        """所有 Worker 协程均已启动且未退出。"""
        return len(self._runners) == self.worker_count and all(
            not runner.done() for runner in self._runners
        )

    async def start(self) -> None:
        """启动固定数量的 Worker 循环。"""
        if self.ready:
            return
        self._runners = [
            asyncio.create_task(
                self._run(worker_index),
                name=f"question-task-worker-{worker_index}",
            )
            for worker_index in range(self.worker_count)
        ]

    async def stop(self) -> None:
        """取消全部 Worker 并等待其退出。"""
        runners, self._runners = self._runners, []
        for runner in runners:
            runner.cancel()
        for runner in runners:
            with suppress(asyncio.CancelledError):
                await runner

    async def join(self) -> None:
        """等待队列中已入队任务全部处理完毕。"""
        await self._queue.join()

    async def submit(self, command: TaskCreate) -> TaskAccepted:
        """创建任务并非阻塞入队；队列满则删除记录并报忙。"""
        task_id = uuid4().hex
        request = TaskRequest(
            task_id=task_id,
            question=command.question,
            requester=command.requester,
            channel=command.channel,
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
            task_url=f"/v1/tasks/{task_id}",
            events_url=f"/v1/tasks/{task_id}/events",
        )

    async def get(self, task_id: str) -> TaskSnapshot | None:
        return await self.tasks.get(task_id)

    async def _run(self, worker_index: int) -> None:
        """单个 Worker：取任务 → 执行 → 发布完成/失败事件。"""
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
                await self.events.publish(
                    request.task_id,
                    "task.completed",
                    {
                        "status": result.status,
                        "data_snapshot_id": result.data_snapshot_id,
                        "trace_ref": result.trace_ref,
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
                self._queue.task_done()
