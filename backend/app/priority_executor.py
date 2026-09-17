from __future__ import annotations

import itertools
import threading
from concurrent.futures import Future
from queue import Empty, PriorityQueue
from typing import Any, Callable


class PriorityExecutor:
    """Small fixed worker pool whose queued Futures can be resubmitted at priority."""

    def __init__(self, max_workers: int, *, thread_name_prefix: str) -> None:
        self._queue: PriorityQueue[tuple[int, int, Any, Any, Any, Any]] = PriorityQueue()
        self._sequence = itertools.count()
        self._closed = False
        self._lock = threading.Lock()
        self._threads = [
            threading.Thread(
                target=self._worker,
                name=f"{thread_name_prefix}-{index + 1}",
                daemon=True,
            )
            for index in range(max_workers)
        ]
        for thread in self._threads:
            thread.start()

    def submit(
        self,
        fn: Callable[..., Any],
        /,
        *args: Any,
        queue_priority: int = 100,
        **kwargs: Any,
    ) -> Future[Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("cannot schedule new futures after shutdown")
            future: Future[Any] = Future()
            self._queue.put(
                (int(queue_priority), next(self._sequence), future, fn, args, kwargs)
            )
            return future

    def _worker(self) -> None:
        while True:
            _priority, _sequence, future, fn, args, kwargs = self._queue.get()
            try:
                if future is None:
                    return
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    result = fn(*args, **kwargs)
                except BaseException as exc:
                    future.set_exception(exc)
                else:
                    future.set_result(result)
            finally:
                self._queue.task_done()

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if cancel_futures:
                while True:
                    try:
                        item = self._queue.get_nowait()
                    except Empty:
                        break
                    future = item[2]
                    if future is not None:
                        future.cancel()
                    self._queue.task_done()
            for _thread in self._threads:
                self._queue.put((10**9, next(self._sequence), None, None, None, None))
        if wait:
            for thread in self._threads:
                thread.join()
