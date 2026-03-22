"""
bot.py — Main entry point for the multi-pair parallel arbitrage bot.

Architecture
------------
  BlockListener (300ms poll)
       │  new-block signal
       ▼
  monitoring loop  ──►  batch_get_pool_data (one multicall, all pools)
       │
       ├──► find_arbitrage_opportunities  (two-way:  A→B on DEX1, B→A on DEX2)
       └──► find_triangular_opportunities (triangle: A→B→C→A across any DEXes)
                      │
                      ▼
             asyncio.PriorityQueue  (sorted by net profit, best first)
                      │
                      ▼
             Executor  (parallel execution, token-conflict locks)

Gas cost is estimated on every block and subtracted from both strategies.

Run:
    python scripts/bot.py
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

sys.path.insert(0, os.path.dirname(__file__))

from pair_manager import PairManager
from executor import Executor
from block_listener import BlockListener
from triangular import find_triangular_opportunities

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bot")

# Gas units for each strategy (conservative estimates)
GAS_TWO_WAY    = 400_000   # 2 swaps + flash loan overhead
GAS_TRIANGULAR = 600_000   # 3 swaps + flash loan overhead


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: Optional[str] = None) -> dict:
    if path is None:
        path = os.environ.get("BOT_CONFIG")
    if path is None:
        path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(path) as fh:
        cfg = yaml.safe_load(fh)
    logger.info("Loaded config from %s", path)
    return cfg


# ---------------------------------------------------------------------------
# Gas cost estimation
# ---------------------------------------------------------------------------

def estimate_gas_cost_pct(w3, gas_units: int, trade_size_usd: float) -> float:
    """
    Returns gas cost as a percentage of the assumed trade size.
    e.g. 0.18 means gas will cost 0.18% of the trade.
    """
    if trade_size_usd <= 0:
        return 0.0
    try:
        gas_price_wei = w3.eth.gas_price
    except Exception:
        gas_price_wei = 20 * 10 ** 9  # 20 gwei fallback

    try:
        from onchainprice import get_eth_price_usd
        eth_price = get_eth_price_usd() or 3500.0
    except Exception:
        eth_price = 3500.0

    gas_cost_eth = gas_units * gas_price_wei / 1e18
    gas_cost_usd = gas_cost_eth * eth_price
    return (gas_cost_usd / trade_size_usd) * 100


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

class Bot:

    def __init__(self, config: dict):
        self.config  = config
        self.running = False

        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()

        mon_cfg = config.get("monitoring", {})
        self._thread_pool = ThreadPoolExecutor(
            max_workers=mon_cfg.get("max_workers", 8),
            thread_name_prefix="mon",
        )

        self._pair_manager: Optional[PairManager]   = None
        self._executor:     Optional[Executor]       = None
        self._block_listener: Optional[BlockListener] = None
        self._w3 = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        logger.info("=" * 60)
        logger.info("  Arbitrage Bot — starting up")
        logger.info("=" * 60)

        loop = asyncio.get_event_loop()

        # Init web3
        await loop.run_in_executor(self._thread_pool, self._init_web3)

        # Init components
        contract_addr        = os.getenv("CONTRACT_ADDRESS")
        self._pair_manager   = PairManager(self.config)
        self._executor       = Executor(self.config, contract_address=contract_addr)
        self._block_listener = BlockListener(self._w3)

        # Discover pairs
        logger.info("Discovering pairs…")
        pairs = await loop.run_in_executor(
            self._thread_pool, self._pair_manager.discover_pairs
        )
        if not pairs:
            logger.error("No pairs discovered.")
            return

        logger.info("\n%s\n", self._pair_manager.summary())
        self.running = True

        # Start block listener — returns an asyncio.Queue
        block_queue = self._block_listener.start(loop)

        await asyncio.gather(
            self._monitoring_loop(block_queue),
            self._dispatch_loop(),
            self._stats_loop(),
        )

    async def stop(self):
        logger.info("Shutting down…")
        self.running = False
        if self._block_listener:
            self._block_listener.stop()
        if self._executor:
            self._executor.shutdown()
        self._thread_pool.shutdown(wait=False)

    # ------------------------------------------------------------------
    # Monitoring loop
    # ------------------------------------------------------------------

    async def _monitoring_loop(self, block_queue: asyncio.Queue):
        from onchainprice import batch_get_pool_data
        find_arbitrage_opportunities = _find_arbitrage_opportunities_with_gas

        exec_cfg       = self.config.get("execution", {})
        min_profit     = exec_cfg.get("min_profit_pct", 0.05)
        trade_size_usd = exec_cfg.get("trade_size_usd", 10_000)

        pool_addresses = self._pair_manager.get_all_pool_addresses()
        if not pool_addresses:
            logger.error("Pool address list is empty.")
            return

        loop      = asyncio.get_event_loop()
        iteration = 0

        logger.info("Monitoring loop ready — waiting for blocks (%d pools)", len(pool_addresses))

        while self.running:
            try:
                # Wait for the next new block (timeout keeps us from hanging forever)
                block_number = await asyncio.wait_for(block_queue.get(), timeout=20.0)
            except asyncio.TimeoutError:
                logger.warning("No new block in 20s — retrying")
                continue
            except asyncio.CancelledError:
                break

            t_start    = time.monotonic()
            iteration += 1

            try:
                # ── Fetch all pool data (one multicall) ──────────────────
                pool_data = await loop.run_in_executor(
                    self._thread_pool,
                    lambda: batch_get_pool_data(pool_addresses),
                )
                if not pool_data:
                    continue

                # ── Estimate gas cost for this block ─────────────────────
                gas_2way = await loop.run_in_executor(
                    self._thread_pool,
                    lambda: estimate_gas_cost_pct(self._w3, GAS_TWO_WAY, trade_size_usd),
                )
                gas_tri = gas_2way * (GAS_TRIANGULAR / GAS_TWO_WAY)

                # ── Two-way arbitrage ────────────────────────────────────
                tw_opps = await loop.run_in_executor(
                    self._thread_pool,
                    lambda: find_arbitrage_opportunities(pool_data, min_profit, gas_2way),
                )
                for opp in tw_opps:
                    opp["type"] = "two_way"

                # ── Triangular arbitrage ─────────────────────────────────
                tri_opps = await loop.run_in_executor(
                    self._thread_pool,
                    lambda: find_triangular_opportunities(pool_data, min_profit, gas_tri),
                )

                all_opps = tw_opps + tri_opps
                enqueued = 0
                for opp in all_opps:
                    priority = -opp["net_profit_percentage"]
                    await self._queue.put((priority, time.monotonic(), opp))
                    enqueued += 1

                elapsed = time.monotonic() - t_start

                if enqueued:
                    best = max(all_opps, key=lambda o: o["net_profit_percentage"])
                    logger.info(
                        "[block %d | iter %d] %d pools | %d opps "
                        "(2-way:%d tri:%d) | best=%.4f%% (%s) | gas=%.3f%% | %.2fs",
                        block_number, iteration, len(pool_data),
                        enqueued, len(tw_opps), len(tri_opps),
                        best["net_profit_percentage"], best.get("pair", "?"),
                        gas_2way, elapsed,
                    )
                else:
                    logger.info(
                        "[block %d | iter %d] %d pools | no opps | gas=%.3f%% | %.2fs",
                        block_number, iteration, len(pool_data), gas_2way, elapsed,
                    )

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Monitoring error: %s", exc)

    # ------------------------------------------------------------------
    # Dispatch loop
    # ------------------------------------------------------------------

    async def _dispatch_loop(self):
        max_age = self.config.get("execution", {}).get("max_queue_age_seconds", 10)

        while self.running:
            try:
                priority, enqueued_at, opp = await asyncio.wait_for(
                    self._queue.get(), timeout=5.0
                )
                age = time.monotonic() - enqueued_at
                if age > max_age:
                    logger.debug("Dropped stale %s opp: %s (%.1fs old)",
                                 opp.get("type", "?"), opp.get("pair", "?"), age)
                    self._queue.task_done()
                    continue

                asyncio.ensure_future(self._executor.try_execute(opp))
                self._queue.task_done()

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Dispatch error: %s", exc)

    # ------------------------------------------------------------------
    # Stats loop
    # ------------------------------------------------------------------

    async def _stats_loop(self):
        while self.running:
            await asyncio.sleep(60)
            stats      = self._executor.stats() if self._executor else {}
            queue_size = self._queue.qsize()
            logger.info(
                "Stats — queue: %d | %s",
                queue_size,
                " | ".join(f"{k}: {v}" for k, v in stats.items()),
            )

    # ------------------------------------------------------------------

    def _init_web3(self):
        from onchainprice import get_web3
        self._w3 = get_web3()


# ---------------------------------------------------------------------------
# Updated find_arbitrage_opportunities wrapper — passes gas cost through
# ---------------------------------------------------------------------------

def _find_arbitrage_opportunities_with_gas(pool_data, min_profit, gas_cost_pct):
    """
    Thin wrapper that subtracts gas_cost_pct from each opportunity's net profit.
    """
    from onchainprice import find_arbitrage_opportunities
    opps = find_arbitrage_opportunities(pool_data, min_profit)
    for opp in opps:
        opp["gas_cost_pct"]        = round(gas_cost_pct, 4)
        opp["net_profit_percentage"] = round(
            opp["net_profit_percentage"] - gas_cost_pct, 6
        )
    return [o for o in opps if o["net_profit_percentage"] > min_profit]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    config = load_config()

    bot  = Bot(config)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(*_):
        if bot.running:
            loop.create_task(bot.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass

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
