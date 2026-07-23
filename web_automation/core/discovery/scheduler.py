"""Generic scheduler for rate limiting, cooldowns, and periodic tasks."""

import asyncio
import time
from typing import Dict, Callable, Awaitable, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


@dataclass
class Task:
    """A scheduled task with cooldown tracking."""
    name: str
    coro: Callable[..., Awaitable[Any]]
    interval: float  # seconds
    cooldown: float = 0  # additional cooldown after execution
    last_run: float = 0
    last_success: bool = True
    run_count: int = 0
    enabled: bool = True
    metadata: dict = field(default_factory=dict)

    def can_run(self) -> bool:
        """Check if task can run now."""
        if not self.enabled:
            return False
        if self.last_run == 0:
            return True
        elapsed = time.time() - self.last_run
        return elapsed >= self.interval + self.cooldown

    def next_run_in(self) -> float:
        """Seconds until next run."""
        if self.last_run == 0:
            return 0
        remaining = (self.interval + self.cooldown) - (time.time() - self.last_run)
        return max(0, remaining)


class GenericScheduler:
    """Manages scheduled tasks with cooldowns and rate limiting."""

    def __init__(self, max_concurrent: int = 3):
        self.tasks: Dict[str, Task] = {}
        self.max_concurrent = max_concurrent
        self._running: Dict[str, asyncio.Task] = {}
        self._shutdown = False
        self._lock = asyncio.Lock()

    def add_task(
        self,
        name: str,
        coro: Callable[..., Awaitable[Any]],
        interval: float,
        cooldown: float = 0,
        **metadata,
    ) -> Task:
        """Add or update a scheduled task."""
        task = Task(
            name=name,
            coro=coro,
            interval=interval,
            cooldown=cooldown,
            metadata=metadata,
        )
        self.tasks[name] = task
        logger.info(f"Scheduled task '{name}' every {interval}s")
        return task

    def remove_task(self, name: str):
        """Remove a task."""
        if name in self.tasks:
            self.tasks[name].enabled = False
            del self.tasks[name]
            logger.info(f"Removed task '{name}'")

    def enable_task(self, name: str):
        """Enable a task."""
        if name in self.tasks:
            self.tasks[name].enabled = True

    def disable_task(self, name: str):
        """Disable a task."""
        if name in self.tasks:
            self.tasks[name].enabled = False

    def set_cooldown(self, name: str, cooldown: float):
        """Set additional cooldown for a task."""
        if name in self.tasks:
            self.tasks[name].cooldown = cooldown

    def get_task(self, name: str) -> Optional[Task]:
        """Get task by name."""
        return self.tasks.get(name)

    def get_ready_tasks(self) -> list[Task]:
        """Get all tasks that are ready to run."""
        return [t for t in self.tasks.values() if t.can_run()]

    async def run_task(self, name: str, *args, **kwargs) -> Any:
        """Run a specific task immediately, bypassing schedule."""
        task = self.tasks.get(name)
        if not task:
            raise ValueError(f"Task '{name}' not found")
        
        # Check concurrency limit
        while len(self._running) >= self.max_concurrent:
            await asyncio.sleep(0.1)
        
        return await self._execute_task(task, *args, **kwargs)

    async def _execute_task(self, task: Task, *args, **kwargs) -> Any:
        """Execute a task and update its state."""
        async with self._lock:
            if task.name in self._running:
                logger.warning(f"Task '{task.name}' already running")
                return None
            
            running_task = asyncio.create_task(task.coro(*args, **kwargs))
            self._running[task.name] = running_task
        
        try:
            start = time.time()
            result = await running_task
            task.last_run = time.time()
            task.last_success = True
            task.run_count += 1
            logger.debug(f"Task '{task.name}' completed in {time.time() - start:.2f}s")
            return result
        except Exception as e:
            task.last_run = time.time()
            task.last_success = False
            task.run_count += 1
            logger.error(f"Task '{task.name}' failed: {e}")
            raise
        finally:
            self._running.pop(task.name, None)

    async def run_loop(self):
        """Main scheduler loop - runs ready tasks."""
        logger.info("Scheduler started")
        while not self._shutdown:
            ready = self.get_ready_tasks()
            
            for task in ready:
                if self._shutdown:
                    break
                if len(self._running) >= self.max_concurrent:
                    break
                
                asyncio.create_task(self._execute_task(task))
            
            await asyncio.sleep(1)  # Check every second

    def shutdown(self):
        """Shutdown the scheduler."""
        self._shutdown = True
        logger.info("Scheduler shutting down...")

    def get_status(self) -> Dict[str, Any]:
        """Get scheduler status."""
        return {
            "total_tasks": len(self.tasks),
            "running": len(self._running),
            "ready": len(self.get_ready_tasks()),
            "tasks": {
                name: {
                    "enabled": t.enabled,
                    "interval": t.interval,
                    "cooldown": t.cooldown,
                    "last_run": t.last_run,
                    "run_count": t.run_count,
                    "next_run_in": t.next_run_in(),
                }
                for name, t in self.tasks.items()
            }
        }


class CooldownManager:
    """Manages per-key cooldowns (e.g., per faucet, per API key)."""

    def __init__(self, default_cooldown: float = 3600):
        self.default_cooldown = default_cooldown
        self.cooldowns: Dict[str, float] = {}
        self.custom_cooldowns: Dict[str, float] = {}

    def set_cooldown(self, key: str, cooldown: float = None):
        """Set cooldown for a key."""
        self.cooldowns[key] = time.time() + (cooldown or self.default_cooldown)

    def set_custom_cooldown(self, key: str, cooldown: float):
        """Set custom cooldown duration for a key."""
        self.custom_cooldowns[key] = cooldown

    def is_cooling(self, key: str) -> bool:
        """Check if key is in cooldown."""
        if key not in self.cooldowns:
            return False
        if time.time() >= self.cooldowns[key]:
            del self.cooldowns[key]
            return False
        return True

    def remaining(self, key: str) -> float:
        """Seconds remaining in cooldown."""
        if key not in self.cooldowns:
            return 0
        remaining = self.cooldowns[key] - time.time()
        return max(0, remaining)

    def get_cooldown_duration(self, key: str) -> float:
        """Get the cooldown duration for a key."""
        return self.custom_cooldowns.get(key, self.default_cooldown)

    def clear(self, key: str = None):
        """Clear cooldown for key or all."""
        if key:
            self.cooldowns.pop(key, None)
            self.custom_cooldowns.pop(key, None)
        else:
            self.cooldowns.clear()
            self.custom_cooldowns.clear()