import asyncio
from typing import Dict


class LockManager:
    """
    Manages per-user asyncio.Lock instances to serialize concurrent requests for the same user
    while allowing different users to execute concurrently without blocking each other.
    """

    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()

    async def get_lock(self, user_id: str) -> asyncio.Lock:
        """Returns the specific asyncio.Lock for the user, creating one if not present."""
        async with self._meta_lock:
            if user_id not in self._locks:
                self._locks[user_id] = asyncio.Lock()
            return self._locks[user_id]


# Global lock manager instance for process-level per-user synchronization
user_lock_manager = LockManager()
