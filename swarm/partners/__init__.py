"""One module per partner API."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="async-bridge")


def run_async(coro):
    """Run a coroutine from sync code, even when the caller sits inside a running loop (a Flow step)."""
    return _POOL.submit(asyncio.run, coro).result()
