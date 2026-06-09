import asyncio
import unittest
from unittest.mock import Mock, patch

import account_queue
from queue_control import SponsoredPriorityController, pop_first_unlocked
import worker_guard


class AccountLockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        account_queue._account_locks.clear()

    async def test_same_account_uses_same_lock(self):
        first = await account_queue.get_lock({"number": "998900000000", "session": "first"})
        second = await account_queue.get_lock({"number": "998900000000", "session": "second"})

        self.assertIs(first, second)

    async def test_same_uid_uses_same_lock_when_phone_changes(self):
        first = await account_queue.get_lock({"uid": "account-1", "number": "111"})
        second = await account_queue.get_lock({"uid": "account-1", "number": "222"})

        self.assertIs(first, second)

    async def test_same_account_operations_do_not_overlap(self):
        active = 0
        max_active = 0

        async def run_one():
            nonlocal active, max_active
            lock = await account_queue.get_lock({"number": "998900000000"})
            async with lock:
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.01)
                active -= 1

        await asyncio.gather(run_one(), run_one(), run_one())

        self.assertEqual(max_active, 1)


class WorkerGuardTests(unittest.TestCase):
    def test_second_worker_is_rejected(self):
        connection = Mock()
        connection.execute.return_value.scalar.return_value = False

        with patch.object(worker_guard.engine, "connect", return_value=connection):
            with self.assertRaises(worker_guard.WorkerAlreadyRunningError):
                worker_guard.acquire_worker_lock()

        connection.close.assert_called_once()

    def test_first_worker_holds_connection_until_release(self):
        connection = Mock()
        connection.execute.return_value.scalar.return_value = True

        with patch.object(worker_guard.engine, "connect", return_value=connection):
            acquired_connection = worker_guard.acquire_worker_lock()

        self.assertIs(acquired_connection, connection)
        connection.close.assert_not_called()

        worker_guard.release_worker_lock(acquired_connection)
        connection.close.assert_called_once()
        self.assertIn("pg_advisory_unlock", connection.execute.call_args.args[0].text)


class SponsoredPriorityControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controller = SponsoredPriorityController()
        self.normal_queue = asyncio.PriorityQueue()
        self.sponsored_queue = asyncio.PriorityQueue()

    async def test_normal_item_stays_queued_while_sponsored_is_active(self):
        normal_item = (1000, 1.0, "normal-1", {})
        await self.normal_queue.put(normal_item)
        await self.controller.enqueue_sponsored(
            self.sponsored_queue,
            (0, 1.0, "sponsored-1", {}),
        )

        normal_get = asyncio.create_task(self.controller.get_normal_item(self.normal_queue))
        await asyncio.sleep(0.01)

        self.assertFalse(normal_get.done())
        self.assertEqual(self.normal_queue.qsize(), 1)

        await self.sponsored_queue.get()
        self.sponsored_queue.task_done()
        await self.controller.sponsored_finished(self.sponsored_queue)

        self.assertEqual(await asyncio.wait_for(normal_get, 0.1), normal_item)
        self.normal_queue.task_done()

    async def test_higher_priority_normal_item_is_not_bypassed_during_sponsored(self):
        low_priority = (1000, 1.0, "low", {})
        high_priority = (1, 2.0, "high", {})
        await self.normal_queue.put(low_priority)
        await self.controller.enqueue_sponsored(
            self.sponsored_queue,
            (0, 1.0, "sponsored-1", {}),
        )

        normal_get = asyncio.create_task(self.controller.get_normal_item(self.normal_queue))
        await asyncio.sleep(0.01)
        await self.normal_queue.put(high_priority)

        await self.sponsored_queue.get()
        self.sponsored_queue.task_done()
        await self.controller.sponsored_finished(self.sponsored_queue)

        self.assertEqual(await asyncio.wait_for(normal_get, 0.1), high_priority)
        self.normal_queue.task_done()

    async def test_cancelled_normal_get_requeues_item(self):
        normal_item = (1000, 1.0, "normal-1", {})
        await self.normal_queue.put(normal_item)
        await self.controller._start_lock.acquire()

        normal_get = asyncio.create_task(self.controller.get_normal_item(self.normal_queue))
        await asyncio.sleep(0.01)
        self.assertEqual(self.normal_queue.qsize(), 0)

        normal_get.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await normal_get

        self.assertEqual(self.normal_queue.qsize(), 1)
        self.controller._start_lock.release()
        self.assertEqual(await self.normal_queue.get(), normal_item)
        self.normal_queue.task_done()

    async def test_normal_account_operation_waits_for_sponsored(self):
        account_lock = asyncio.Lock()
        entered = asyncio.Event()
        await self.controller.enqueue_sponsored(
            self.sponsored_queue,
            (0, 1.0, "sponsored-1", {}),
        )

        async def normal_operation():
            async with self.controller.normal_account_lock(account_lock):
                entered.set()

        operation = asyncio.create_task(normal_operation())
        await asyncio.sleep(0.01)
        self.assertFalse(entered.is_set())

        await self.sponsored_queue.get()
        self.sponsored_queue.task_done()
        await self.controller.sponsored_finished(self.sponsored_queue)
        await asyncio.wait_for(operation, 0.1)

        self.assertTrue(entered.is_set())

    async def test_cancelled_normal_account_operation_releases_lock(self):
        account_lock = asyncio.Lock()
        self.controller._start_lock = asyncio.Lock()
        await self.controller._start_lock.acquire()

        async def normal_operation():
            async with self.controller.normal_account_lock(account_lock):
                pass

        operation = asyncio.create_task(normal_operation())
        await asyncio.sleep(0.01)
        self.assertTrue(account_lock.locked())

        operation.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await operation

        self.assertFalse(account_lock.locked())
        self.controller._start_lock.release()

    async def test_priority_stays_active_until_last_sponsored_finishes(self):
        first = (0, 1.0, "sponsored-1", {})
        second = (0, 2.0, "sponsored-2", {})
        await self.controller.enqueue_sponsored(self.sponsored_queue, first)
        await self.controller.enqueue_sponsored(self.sponsored_queue, second)

        self.assertTrue(self.controller.sponsored_active)
        await self.sponsored_queue.get()
        self.sponsored_queue.task_done()
        await self.controller.sponsored_finished(self.sponsored_queue)
        self.assertTrue(self.controller.sponsored_active)

        await self.sponsored_queue.get()
        self.sponsored_queue.task_done()
        await self.controller.sponsored_finished(self.sponsored_queue)
        self.assertFalse(self.controller.sponsored_active)

    async def test_equal_priority_items_use_task_id_as_tiebreaker(self):
        await self.normal_queue.put((1000, 1.0, "task-b", {"value": "b"}))
        await self.normal_queue.put((1000, 1.0, "task-a", {"value": "a"}))

        first = await self.normal_queue.get()
        second = await self.normal_queue.get()

        self.assertEqual(first[2], "task-a")
        self.assertEqual(second[2], "task-b")
        self.normal_queue.task_done()
        self.normal_queue.task_done()

    async def test_sponsored_scheduler_selects_unlocked_session_first(self):
        locked = asyncio.Lock()
        await locked.acquire()
        free = asyncio.Lock()
        candidates = [
            ({"uid": "busy"}, "busy", locked),
            ({"uid": "free"}, "free", free),
        ]

        selected = pop_first_unlocked(candidates)

        self.assertEqual(selected[1], "free")
        self.assertEqual(candidates[0][1], "busy")
        locked.release()


if __name__ == "__main__":
    unittest.main()
