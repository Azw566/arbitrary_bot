"""
BlockListener
-------------
Signals an asyncio.Queue whenever a new block is mined.

Transport selection
-------------------
- If RPC_URL starts with ``wss://``: uses w3.eth.subscribe('newHeads') over
  WebSocket — reacts to blocks instantly with no artificial delay.
- Otherwise: polls eth_blockNumber every ~300ms in a background thread (HTTP
  fallback, existing behaviour preserved).
"""

import asyncio
import logging
import os
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
        """Start block listener. Returns the queue that receives block numbers."""
        self._loop    = loop
        self._queue   = asyncio.Queue()
        self._running = True

        rpc_url = os.getenv("RPC_URL", "")
        if rpc_url.startswith("wss://"):
            self._thread = threading.Thread(
                target=self._ws_loop,
                daemon=True,
                name="block-listener-ws",
            )
            logger.info("Block listener started (WebSocket subscription)")
        else:
            self._thread = threading.Thread(
                target=self._poll_loop,
                daemon=True,
                name="block-listener-poll",
            )
            logger.info("Block listener started (HTTP poll interval: %dms)",
                        int(self._poll_interval * 1000))

        self._thread.start()
        return self._queue

    def stop(self):
        self._running = False

    # ------------------------------------------------------------------
    # WebSocket path
    # ------------------------------------------------------------------

    def _ws_loop(self):
        """Subscribe to newHeads over WebSocket; fall back to polling on error."""
        try:
            subscription = self._w3.eth.subscribe("newHeads")
            logger.debug("WebSocket newHeads subscription active")
            for header in subscription:
                if not self._running:
                    break
                try:
                    block_number = int(header["number"], 16) if isinstance(
                        header.get("number"), str
                    ) else int(header["number"])
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning("Could not parse block header: %s", exc)
                    continue
                if block_number > self._last_block:
                    self._last_block = block_number
                    asyncio.run_coroutine_threadsafe(
                        self._queue.put(block_number), self._loop
                    )
                    logger.debug("New block (ws): %d", block_number)
        except Exception as exc:
            logger.warning(
                "WebSocket subscription failed (%s) — falling back to HTTP polling", exc
            )
            self._poll_loop()

    # ------------------------------------------------------------------
    # HTTP polling path (original behaviour)
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
