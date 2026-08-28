import asyncio
from typing import Any

_queues: dict[str, "asyncio.Queue[Any]"] = {}


def _queue(topic: str) -> "asyncio.Queue[Any]":
    if topic not in _queues:
        _queues[topic] = asyncio.Queue()
    return _queues[topic]


async def publish(topic: str, item: Any) -> None:
    await _queue(topic).put(item)


async def consume(topic: str) -> Any:
    return await _queue(topic).get()
