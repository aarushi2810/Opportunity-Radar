"""Async in-process message bus — lightweight Kafka-like pub/sub using asyncio.Queue."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

logger = logging.getLogger("opportunity_radar.bus")


class MessageBus:
    """Topic-based async message bus with multiple subscribers per topic."""

    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._handlers: dict[str, list[Callable[..., Coroutine]]] = defaultdict(list)
        self._running = False
        self._tasks: list[asyncio.Task] = []

    def subscribe(self, topic: str) -> asyncio.Queue:
        """Create a new subscription queue for a topic."""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[topic].append(queue)
        logger.info(f"New subscriber on topic '{topic}' (total: {len(self._subscribers[topic])})")
        return queue

    def on(self, topic: str, handler: Callable[..., Coroutine]):
        """Register an async handler for a topic. Handler is called for each message."""
        self._handlers[topic].append(handler)
        logger.info(f"Handler registered on topic '{topic}'")

    async def publish(self, topic: str, message: Any):
        """Publish a message to all subscribers of a topic."""
        logger.debug(f"Publishing to '{topic}': {type(message).__name__}")
        for queue in self._subscribers[topic]:
            await queue.put(message)
        # Also call direct handlers
        for handler in self._handlers[topic]:
            try:
                await handler(message)
            except Exception as e:
                logger.error(f"Handler error on '{topic}': {e}")

    async def consume(self, topic: str, handler: Callable[..., Coroutine]):
        """Start consuming messages from a topic with a handler (blocking)."""
        queue = self.subscribe(topic)
        logger.info(f"Consumer started on topic '{topic}'")
        while self._running:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=1.0)
                try:
                    await handler(message)
                except Exception as e:
                    logger.error(f"Consumer error on '{topic}': {e}")
            except asyncio.TimeoutError:
                continue

    def start_consumer(self, topic: str, handler: Callable[..., Coroutine]):
        """Start a consumer as a background task."""
        self._running = True
        task = asyncio.create_task(self.consume(topic, handler))
        self._tasks.append(task)
        return task

    async def start(self):
        """Start the message bus."""
        self._running = True
        logger.info("Message bus started")

    async def stop(self):
        """Stop the message bus and all consumers."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("Message bus stopped")


# Singleton instance
bus = MessageBus()
