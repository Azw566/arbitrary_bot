"""
bot.py — Main entry point for the multi-pair parallel arbitrage bot.

Architecture
------------
  monitoring loop  →  asyncio.PriorityQueue  →  dispatch loop  →  Executor
       (all pairs fetched in one multicall batch per tick)

Run:
    python scripts/bot.py                         # uses config.yaml at project root
    BOT_CONFIG=path/to/config.yaml python scripts/bot.py
"""

import asyncio
import logging
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import yaml

# Make scripts/ importable
sys.path.insert(0, os.path.dirname(__file__))

from pair_manager import PairManager
from executor import Executor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bot")


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config(path: Optional[str] = None) -> dict:
    if path is None:
        path = os.environ.get("BOT_CONFIG")
    if path is None:
        # Default: config.yaml at project root (one level above scripts/)
        path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(path) as fh:
        cfg = yaml.safe_load(fh)
    logger.info("Loaded config from %s", path)
    return cfg


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

class Bot:
    """
    Coordinates pair discovery, pool monitoring, and trade execution
    across all configured pairs in parallel.
    """

    def __init__(self, config: dict):
        self.config  = config
        self.running = False

        # Priority queue: items are (neg_profit, timestamp, opportunity_dict)
        # Lower value = higher priority (best profit dispatched first)
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()

        mon_cfg = config.get("monitoring", {})
        self._thread_pool = ThreadPoolExecutor(
            max_workers=mon_cfg.get("max_workers", 8),
            thread_name_prefix="mon",
        )

        self._pair_manager: Optional[PairManager] = None
        self._executor: Optional[Executor]        = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        logger.info("=" * 60)
        logger.info("  Arbitrage Bot — starting up")
        logger.info("=" * 60)

        # Init web3
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._thread_pool, self._init_web3)

        # Init components
        contract_addr = os.getenv("CONTRACT_ADDRESS")
        self._pair_manager = PairManager(self.config)
        self._executor     = Executor(self.config, contract_address=contract_addr)

        # Discover pairs once at startup (blocking subgraph calls)
        logger.info("Discovering pairs from subgraphs…")
        pairs = await loop.run_in_executor(
            self._thread_pool, self._pair_manager.discover_pairs
        )

        if not pairs:
            logger.error("No pairs discovered — check subgraph env vars and connectivity.")
            return

        logger.info("\n%s\n", self._pair_manager.summary())

        self.running = True

        # Run all async tasks concurrently
        await asyncio.gather(
            self._monitoring_loop(),
            self._dispatch_loop(),
            self._stats_loop(),
        )

    async def stop(self):
        logger.info("Shutting down…")
        self.running = False
        if self._executor:
            self._executor.shutdown()
        self._thread_pool.shutdown(wait=False)

    # ------------------------------------------------------------------
    # Monitoring loop — one multicall batch covers all pairs every tick
    # ------------------------------------------------------------------

    async def _monitoring_loop(self):
        from onchainprice import batch_get_pool_data, find_arbitrage_opportunities

        mon_cfg  = self.config.get("monitoring", {})
        exec_cfg = self.config.get("execution", {})
        interval    = mon_cfg.get("interval_seconds", 2)
        min_profit  = exec_cfg.get("min_profit_pct", 0.5)

        pool_addresses = self._pair_manager.get_all_pool_addresses()
        if not pool_addresses:
            logger.error("Pool address list is empty.")
            return

        logger.info(
            "Monitoring loop started — %d pools, interval=%ss",
            len(pool_addresses), interval,
        )

        loop      = asyncio.get_event_loop()
        iteration = 0

        while self.running:
            t_start   = time.monotonic()
            iteration += 1

            try:
                # Fetch all pool data in a single multicall (non-blocking via thread pool)
                pool_data = await loop.run_in_executor(
                    self._thread_pool,
                    lambda: batch_get_pool_data(pool_addresses),
                )

                if not pool_data:
                    await asyncio.sleep(interval)
                    continue

                # Find arbitrage opportunities across all pairs
                opportunities = await loop.run_in_executor(
                    self._thread_pool,
                    lambda: find_arbitrage_opportunities(pool_data, min_profit),
                )

                enqueued = 0
                for opp in opportunities:
                    priority = -opp["net_profit_percentage"]   # best profit = lowest value
                    await self._queue.put((priority, time.monotonic(), opp))
                    enqueued += 1

                elapsed = time.monotonic() - t_start

                if enqueued:
                    logger.info(
                        "[iter %d] %d pools | %d opportunities | %.2fs",
                        iteration, len(pool_data), enqueued, elapsed,
                    )
                else:
                    logger.debug(
                        "[iter %d] %d pools | no opportunities | %.2fs",
                        iteration, len(pool_data), elapsed,
                    )

                await asyncio.sleep(max(0.0, interval - elapsed))

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Monitoring error: %s", exc)
                await asyncio.sleep(interval)

    # ------------------------------------------------------------------
    # Dispatch loop — pulls from queue, fires executions concurrently
    # ------------------------------------------------------------------

    async def _dispatch_loop(self):
        exec_cfg  = self.config.get("execution", {})
        max_age   = exec_cfg.get("max_queue_age_seconds", 10)

        while self.running:
            try:
                priority, enqueued_at, opp = await asyncio.wait_for(
                    self._queue.get(), timeout=5.0
                )

                # Drop stale opportunities — market has moved on
                age = time.monotonic() - enqueued_at
                if age > max_age:
                    logger.debug(
                        "Dropped stale opportunity: %s (%.1fs old)",
                        opp.get("pair", "?"), age,
                    )
                    self._queue.task_done()
                    continue

                # Fire-and-forget: don't await so all pairs run in parallel
                asyncio.ensure_future(self._executor.try_execute(opp))
                self._queue.task_done()

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Dispatch error: %s", exc)

    # ------------------------------------------------------------------
    # Stats loop — periodic summary every 60 s
    # ------------------------------------------------------------------

    async def _stats_loop(self):
        while self.running:
            await asyncio.sleep(60)
            stats = self._executor.stats() if self._executor else {}
            queue_size = self._queue.qsize()
            logger.info(
                "Stats — queue: %d | %s",
                queue_size,
                " | ".join(f"{k}: {v}" for k, v in stats.items()),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _init_web3():
        from onchainprice import get_web3
        get_web3()   # establishes PRIMARY_W3, cached for all subsequent calls


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    config = load_config()
    bot    = Bot(config)
    loop   = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(*_):
        if bot.running:
            loop.create_task(bot.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

    try:
        loop.run_until_complete(bot.start())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        loop.run_until_complete(bot.stop())
        loop.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    main()
