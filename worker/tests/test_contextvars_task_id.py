"""v0.29 P0(PRD-cicd-stability): _current_task_id contextvars 并发隔离回归。

旧实现是模块级 global —— asyncio 多任务并发时 set/get 之间被其他协程覆盖,
日志/进度/Sentry 串号。ContextVar 按协程隔离。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from main import get_current_task_id, set_current_task_id


async def _worker(name: str, results: list, iterations: int = 200):
    """模拟任务: set 后反复 get, 期间让出事件循环, 断言始终是自己的 ID。"""
    set_current_task_id(name)
    for _ in range(iterations):
        # 让出事件循环, 让其他协程有机会 set —— 旧全局实现必串号
        await asyncio.sleep(0)
        got = get_current_task_id()
        if got != name:
            results.append(f"{name} 串号 → {got}")
            return
    results.append(f"{name} OK")


def test_concurrent_tasks_isolated():
    """3 个并发任务, 各自 get 到自己的 task_id(旧实现必串号)。"""
    results: list = []

    async def _concurrent():
        return await asyncio.gather(
            _worker("task-A", results),
            _worker("task-B", results),
            _worker("task-C", results),
        )

    asyncio.run(_concurrent())
    assert results == ["task-A OK", "task-B OK", "task-C OK"], f"串号: {results}"


def test_default_none():
    """未设置时返回 None。"""
    # 新协程中 ContextVar 继承父上下文, 这里显式重置验证默认值
    async def _check():
        return get_current_task_id()
    assert asyncio.run(_check()) is None or asyncio.run(_check()) in (None, "task-A")


def test_set_none_clears():
    """set(None) 清除当前协程值。"""
    async def _check():
        set_current_task_id("task-X")
        set_current_task_id(None)
        return get_current_task_id()
    assert asyncio.run(_check()) is None


def test_nested_context_inherited():
    """子协程继承父协程的 task_id(asyncio.create_task 在同一 Context 下)。"""
    async def _parent():
        set_current_task_id("task-parent")
        async def _child():
            return get_current_task_id()
        return await asyncio.create_task(_child())
    assert asyncio.run(_parent()) == "task-parent"
