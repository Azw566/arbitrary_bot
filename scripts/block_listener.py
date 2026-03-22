"""
BlockListener
-------------
Polls eth_blockNumber every ~300ms in a background thread and signals
an asyncio.Queue whenever a new block is mined.

This gives us block-level scan granularity (~12s on mainnet) while
keeping the architecture simple — no WebSocket dependency required.
"""

import asyncio
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class BlockListener:
    """
    Usage
    -----
        listener = BlockListener(w3)
        block_queue = listener.start(loop)   # returns asyncio.Queue[int]

        # inside async code:
        block_number = await block_queue.get()
    """

    def __init__(self, w3, poll_interval_ms: int = 300):
        self._w3              = w3
        self._poll_interval   = poll_interval_ms / 1000
        self._last_block: int = 0
        self._running: bool   = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue] = None

    # ------------------------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        """Start background polling. Returns the queue that receives block numbers."""
        self._loop    = loop
        self._queue   = asyncio.Queue()
        self._running = True
        self._thread  = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="block-listener",
        )
        self._thread.start()
        logger.info("Block listener started (poll interval: %dms)", int(self._poll_interval * 1000))
        return self._queue

    def stop(self):
        self._running = False

    # ------------------------------------------------------------------

    def _poll_loop(self):
        consecutive_errors = 0

        while self._running:
            try:
                block = self._w3.eth.block_number
                if block > self._last_block:
                    self._last_block = block
                    asyncio.run_coroutine_threadsafe(
                        self._queue.put(block), self._loop
                    )
                    logger.debug("New block: %d", block)
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                if consecutive_errors <= 3:
                    logger.warning("Block poll error (%d): %s", consecutive_errors, exc)
                # Back off on repeated failures
                time.sleep(min(self._poll_interval * consecutive_errors, 5.0))
                continue

            time.sleep(self._poll_interval)
