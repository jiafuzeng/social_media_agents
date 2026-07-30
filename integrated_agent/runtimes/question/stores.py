"""问数任务与事件的内存存储。

生产环境可替换为持久化实现；接口保持 create/get/complete 与 publish/wait。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from .models import TaskEvent, TaskResult, TaskSnapshot


class InMemoryTaskStore:
    """任务状态机：accepted → running → completed | failed。

    只保存每个任务的「当前快照」（状态 / 结果 / 错误），
    不保存过程事件；过程流由 InMemoryEventStore 负责。
    """

    def __init__(self) -> None:
        # task_id → 当前 TaskSnapshot
        self._records: dict[str, TaskSnapshot] = {}
        # 多协程（HTTP + Worker）并发读写时互斥
        self._lock = asyncio.Lock()

    async def create(self, task_id: str) -> TaskSnapshot:
        """受理任务时建档，初始状态为 accepted；重复 id 则报错。"""
        async with self._lock:
            if task_id in self._records:
                raise ValueError(f"duplicate task id: {task_id}")
            record = TaskSnapshot(task_id=task_id, status="accepted")
            self._records[task_id] = record
            return record

    async def get(self, task_id: str) -> TaskSnapshot | None:
        """按 id 查询快照；不存在返回 None（HTTP 侧映射为 404）。"""
        async with self._lock:
            return self._records.get(task_id)

    async def delete(self, task_id: str) -> None:
        """入队失败时回滚已创建的任务记录。"""
        async with self._lock:
            self._records.pop(task_id, None)

    async def mark_running(self, task_id: str) -> TaskSnapshot:
        """Worker 取出任务后：accepted → running。"""
        return await self._replace(task_id, status="running")

    async def complete(self, task_id: str, result: TaskResult) -> TaskSnapshot:
        """分析成功：写入结果，状态变为 completed。"""
        return await self._replace(task_id, status="completed", result=result)

    async def fail(self, task_id: str, error: str) -> TaskSnapshot:
        """分析失败：写入错误信息，状态变为 failed。"""
        return await self._replace(task_id, status="failed", error=error)

    async def _replace(self, task_id: str, **updates: Any) -> TaskSnapshot:
        """在锁内用 model_copy 生成新快照并写回；任务不存在则 KeyError。"""
        async with self._lock:
            current = self._records.get(task_id)
            if current is None:
                raise KeyError(task_id)
            updated = current.model_copy(update=updates)
            self._records[task_id] = updated
            return updated


class InMemoryEventStore:
    """按 task_id 追加事件，并用 Condition 唤醒 SSE 订阅者。

    与 TaskStore 分工：这里存进度时间线，供 /v1/tasks/{id}/events 推送。
    """

    def __init__(self) -> None:
        # task_id → 按 sequence 递增的事件列表
        self._events: dict[str, list[TaskEvent]] = defaultdict(list)
        # 异步条件变量：publish 后 notify，wait_for_change 侧等待
        self._condition = asyncio.Condition()

    async def publish(
        self, task_id: str, event_type: str, data: dict[str, Any] | None = None
    ) -> TaskEvent:
        """追加一条事件并通知所有等待者；sequence 从 1 递增。"""
        async with self._condition:
            event = TaskEvent(
                task_id=task_id,
                sequence=len(self._events[task_id]) + 1,
                event_type=event_type,
                data=data or {},
            )
            self._events[task_id].append(event)
            # 叫醒正在 wait_for_change 的 SSE 协程
            self._condition.notify_all()
            return event

    def list_for(self, task_id: str) -> list[TaskEvent]:
        """同步快照当前事件列表（SSE 侧用于游标读取）。"""
        return list(self._events.get(task_id, []))

    async def wait_for_change(
        self, task_id: str, after_sequence: int, timeout: float = 10.0
    ) -> list[TaskEvent]:
        """阻塞直到出现 sequence > after_sequence 的新事件或超时。

        SSE 用它避免空转轮询；超时由调用方发 keep-alive 心跳。
        """
        async with self._condition:
            # 尚无新事件则挂起；publish 的 notify_all 会唤醒
            if len(self._events.get(task_id, [])) <= after_sequence:
                await asyncio.wait_for(
                    self._condition.wait_for(
                        lambda: len(self._events.get(task_id, [])) > after_sequence
                    ),
                    timeout=timeout,
                )
            # 返回游标之后的增量，供断线续传
            return self.list_for(task_id)[after_sequence:]
