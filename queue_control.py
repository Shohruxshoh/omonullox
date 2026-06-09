import asyncio
from contextlib import asynccontextmanager


def pop_first_unlocked(candidates: list[tuple]):
    for index, item in enumerate(candidates):
        if not item[2].locked():
            return candidates.pop(index)
    return None


class SponsoredPriorityController:
    """Sponsored ishlar navbatda yoki running bo'lsa normal ishlarni boshlatmaydi."""

    def __init__(self):
        self._normal_allowed = asyncio.Event()
        self._normal_allowed.set()
        self._sponsored_queue_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()

    @property
    def sponsored_active(self) -> bool:
        return not self._normal_allowed.is_set()

    async def enqueue_sponsored(self, queue: asyncio.Queue, item) -> None:
        async with self._start_lock:
            async with self._sponsored_queue_lock:
                self._normal_allowed.clear()
                await queue.put(item)

    async def sponsored_finished(self, queue: asyncio.Queue) -> None:
        async with self._sponsored_queue_lock:
            if queue.empty():
                self._normal_allowed.set()

    async def get_normal_item(self, queue: asyncio.Queue):
        """Sponsored ustunligini saqlab, normal queue itemini tartibini buzmasdan oladi."""
        while True:
            item = await queue.get()
            requeued = False
            try:
                async with self._start_lock:
                    if self._normal_allowed.is_set():
                        return item
                    queue.put_nowait(item)
                    queue.task_done()
                    requeued = True
            except BaseException:
                if not requeued:
                    queue.put_nowait(item)
                    queue.task_done()
                raise
            await self._normal_allowed.wait()

    @asynccontextmanager
    async def normal_account_lock(self, account_lock: asyncio.Lock):
        """Sponsored faol bo'lsa normal account operatsiyasini boshlatmaydi."""
        acquired = False
        try:
            while True:
                await self._normal_allowed.wait()
                await account_lock.acquire()
                acquired = True
                async with self._start_lock:
                    if self._normal_allowed.is_set():
                        break
                account_lock.release()
                acquired = False

            yield
        finally:
            if acquired:
                account_lock.release()
