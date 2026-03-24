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
from typing import Dict, Optional, Set

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

# Block-scoped cache for gas price and ETH price — refreshed once per new block
_block_cache = {"block": 0, "gas_price": 0, "eth_price": 0.0}


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

def estimate_gas_cost_pct(w3, gas_units: int, trade_size_usd: float,
                          block_number: int = 0) -> float:
    """
    Returns gas cost as a percentage of the assumed trade size.
    e.g. 0.18 means gas will cost 0.18% of the trade.

    Gas price and ETH price are cached per-block to avoid redundant RPC calls.
    Pass block_number to enable the cache; omit (or pass 0) for a fresh fetch.
    """
    if trade_size_usd <= 0:
        return 0.0

    # Refresh cache only when the block changes (or when no block context given)
    if block_number and block_number == _block_cache["block"]:
        gas_price_wei = _block_cache["gas_price"]
        eth_price      = _block_cache["eth_price"]
    else:
        try:
            gas_price_wei = w3.eth.gas_price
        except Exception:
            gas_price_wei = 20 * 10 ** 9  # 20 gwei fallback

        try:
            from onchainprice import get_eth_price_usd
            eth_price = get_eth_price_usd() or 3500.0
        except Exception:
            eth_price = 3500.0

        if block_number:
            _block_cache["block"]     = block_number
            _block_cache["gas_price"] = gas_price_wei
            _block_cache["eth_price"] = eth_price

    gas_cost_eth = gas_units * gas_price_wei / 1e18
    gas_cost_usd = gas_cost_eth * eth_price
    return (gas_cost_usd / trade_size_usd) * 100


def estimate_gas_cost_usd(w3, gas_units: int, block_number: int = 0) -> float:
    """
    Returns the absolute gas cost in USD (not as a percentage).
    Uses the same per-block cache as estimate_gas_cost_pct.
    """
    if block_number and block_number == _block_cache["block"]:
        gas_price_wei = _block_cache["gas_price"]
        eth_price      = _block_cache["eth_price"]
    else:
        try:
            gas_price_wei = w3.eth.gas_price
        except Exception:
            gas_price_wei = 20 * 10 ** 9

        try:
            from onchainprice import get_eth_price_usd
            eth_price = get_eth_price_usd() or 3500.0
        except Exception:
            eth_price = 3500.0

        if block_number:
            _block_cache["block"]     = block_number
            _block_cache["gas_price"] = gas_price_wei
            _block_cache["eth_price"] = eth_price

    return (gas_units * gas_price_wei / 1e18) * eth_price


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

class Bot:

    def __init__(self, config: dict):
        self.config  = config
        self.running = False

        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()

        # Async DB write queue — opportunities are placed here instead of
        # being written synchronously in the hot path.
        self._db_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

        mon_cfg = config.get("monitoring", {})
        self._thread_pool = ThreadPoolExecutor(
            max_workers=mon_cfg.get("max_workers", 8),
            thread_name_prefix="mon",
        )

        self._pair_manager: Optional[PairManager]   = None
        self._executor:     Optional[Executor]       = None
        self._block_listener: Optional[BlockListener] = None
        self._w3 = None

        # Stale pool tracking — pools dead for 3+ consecutive blocks are skipped
        # Key: pool_address (lowercase), Value: consecutive dead-block count
        self._pool_dead_count: Dict[str, int] = {}
        # Addresses of pools currently marked stale (excluded from scanning)
        self.STALE_POOLS: Set[str] = set()
        # Block number when each pool was marked stale (for auto-reset after 1000 blocks)
        self._stale_since: Dict[str, int] = {}

        # ── Circuit breaker: RPC failure tracking ────────────────────────────
        # Build the RPC URL list from environment variables plus the built-in
        # onchainprice.RPC_PROVIDERS list.
        self._rpc_failure_count: int = 0
        self._rpc_urls: list = self._build_rpc_url_list()
        self._rpc_index: int = 0  # index of the active provider in _rpc_urls
        self._RPC_FAILURE_THRESHOLD: int = 3   # consecutive failures before switching RPC
        self._RPC_SWITCH_PAUSE: float = 2.0    # seconds to wait after switching provider

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

        db_cfg = self.config.get("database", {})
        db_enabled = db_cfg.get("enabled", False) and db_cfg.get("save_opportunities", False)

        coroutines = [
            self._monitoring_loop(block_queue),
            self._dispatch_loop(),
            self._stats_loop(),
        ]
        if db_enabled:
            coroutines.append(self._db_writer_loop())

        await asyncio.gather(*coroutines)

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

        exec_cfg       = self.config.get("execution", {})
        db_cfg         = self.config.get("database", {})
        min_profit     = exec_cfg.get("min_profit_pct", 0.05)
        trade_size_usd = exec_cfg.get("trade_size_usd", 10_000)
        db_save_opps   = db_cfg.get("enabled", False) and db_cfg.get("save_opportunities", False)

        all_pool_addresses = self._pair_manager.get_all_pool_addresses()
        if not all_pool_addresses:
            logger.error("Pool address list is empty.")
            return

        loop      = asyncio.get_event_loop()
        iteration = 0

        logger.info("Monitoring loop ready — waiting for blocks (%d pools)", len(all_pool_addresses))

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
                # ── Reset stale-pool status after 1000 blocks ────────────
                revived = [
                    addr for addr, since in list(self._stale_since.items())
                    if block_number - since >= 1000
                ]
                for addr in revived:
                    self.STALE_POOLS.discard(addr)
                    self._stale_since.pop(addr, None)
                    self._pool_dead_count.pop(addr, None)
                    logger.info("Pool %s removed from stale list (1000-block reset)", addr)

                # ── Filter out stale pools before fetching ───────────────
                pool_addresses = [
                    a for a in all_pool_addresses
                    if a.lower() not in self.STALE_POOLS
                ]

                # ── Fetch all pool data (one multicall) ──────────────────
                _pa = pool_addresses
                try:
                    pool_data = await loop.run_in_executor(
                        self._thread_pool,
                        lambda: batch_get_pool_data(_pa),
                    )
                    self._rpc_failure_count = 0
                except Exception as rpc_exc:
                    self._rpc_failure_count += 1
                    logger.error(
                        "RPC error (failure %d/%d): %s",
                        self._rpc_failure_count,
                        self._RPC_FAILURE_THRESHOLD,
                        rpc_exc,
                    )
                    if self._rpc_failure_count >= self._RPC_FAILURE_THRESHOLD:
                        self._switch_rpc(str(rpc_exc))
                        await asyncio.sleep(self._RPC_SWITCH_PAUSE)
                    continue

                if not pool_data:
                    continue

                # ── Pool health check every 100 blocks ───────────────────
                if iteration % 100 == 0:
                    _pd  = pool_data
                    _pa2 = pool_addresses
                    _bn2 = block_number
                    await loop.run_in_executor(
                        self._thread_pool,
                        lambda: self._check_pool_health(_pd, _pa2, _bn2),
                    )

                # ── Estimate gas cost for this block (absolute USD value) ──
                _bn = block_number
                gas_2way_usd = await loop.run_in_executor(
                    self._thread_pool,
                    lambda: estimate_gas_cost_usd(self._w3, GAS_TWO_WAY, _bn),
                )
                gas_tri_usd  = gas_2way_usd * (GAS_TRIANGULAR / GAS_TWO_WAY)
                # % forms for logging and triangular (uses reference trade_size_usd)
                gas_2way_pct = (gas_2way_usd / trade_size_usd) * 100 if trade_size_usd > 0 else 0.0
                gas_tri_pct  = (gas_tri_usd  / trade_size_usd) * 100 if trade_size_usd > 0 else 0.0

                # ── Two-way arbitrage ────────────────────────────────────
                _g2usd = gas_2way_usd
                _g2pct = gas_2way_pct
                _tsusd = trade_size_usd
                tw_opps = await loop.run_in_executor(
                    self._thread_pool,
                    lambda: _find_arbitrage_opportunities_with_gas(
                        pool_data, min_profit, _g2pct, _tsusd, _g2usd
                    ),
                )
                for opp in tw_opps:
                    opp["type"] = "two_way"

                # ── Triangular arbitrage ─────────────────────────────────
                _gtpct = gas_tri_pct
                tri_opps = await loop.run_in_executor(
                    self._thread_pool,
                    lambda: find_triangular_opportunities(pool_data, min_profit, _gtpct),
                )

                all_opps = tw_opps + tri_opps
                enqueued = 0
                for opp in all_opps:
                    priority = -opp["net_profit_percentage"]
                    await self._queue.put((priority, time.monotonic(), opp))
                    enqueued += 1

                    # ── Non-blocking DB enqueue ───────────────────────────
                    if db_save_opps:
                        if self._db_queue.full():
                            try:
                                self._db_queue.get_nowait()
                                logger.warning("DB queue full — dropped oldest opportunity")
                            except asyncio.QueueEmpty:
                                pass
                        try:
                            self._db_queue.put_nowait(opp)
                        except asyncio.QueueFull:
                            logger.warning("DB queue still full — opportunity dropped")

                elapsed = time.monotonic() - t_start

                if enqueued:
                    best = max(all_opps, key=lambda o: o["net_profit_percentage"])
                    logger.info(
                        "[block %d | iter %d] %d pools | %d opps "
                        "(2-way:%d tri:%d) | best=%.4f%% (%s) opt=$%.0f | gas=%.3f%% | %.2fs",
                        block_number, iteration, len(pool_data),
                        enqueued, len(tw_opps), len(tri_opps),
                        best["net_profit_percentage"], best.get("pair", "?"),
                        best.get("optimal_trade_size_usd", 0),
                        gas_2way_pct, elapsed,
                    )
                else:
                    logger.info(
                        "[block %d | iter %d] %d pools | no opps | gas=%.3f%% | %.2fs",
                        block_number, iteration, len(pool_data), gas_2way_pct, elapsed,
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
                "Stats — queue: %d | stale_pools: %d | %s",
                queue_size,
                len(self.STALE_POOLS),
                " | ".join(f"{k}: {v}" for k, v in stats.items()),
            )

    # ------------------------------------------------------------------
    # Async DB writer — drains _db_queue in the background
    # ------------------------------------------------------------------

    async def _db_writer_loop(self):
        """
        Background coroutine that pulls opportunities from _db_queue and writes
        them to the database asynchronously, so the hot monitoring path is never
        blocked by I/O.
        """
        from onchainprice import save_to_database
        loop = asyncio.get_event_loop()
        logger.info("DB writer loop started")

        while self.running:
            try:
                opp = await asyncio.wait_for(self._db_queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                _opp = opp  # capture for lambda default argument
                await loop.run_in_executor(
                    self._thread_pool,
                    lambda o=_opp: save_to_database([], [o]),
                )
            except Exception as exc:
                logger.warning("DB write failed: %s", exc)
            finally:
                self._db_queue.task_done()

        logger.info("DB writer loop stopped")

    # ------------------------------------------------------------------
    # Pool health checker — marks dead pools as stale
    # ------------------------------------------------------------------

    def _check_pool_health(self, pool_data: list, pool_addresses: list, block_number: int):
        """
        Called every 100 blocks.  Inspects each pool in pool_data for signs of
        being dead (price == 0 or liquidity == 0).  Pools that are dead for 3+
        consecutive health-check rounds are moved to STALE_POOLS and excluded
        from future multicall batches.  STALE_POOLS entries are automatically
        cleared after 1000 blocks (handled in _monitoring_loop).
        """
        # Build a set of addresses seen in this batch's pool_data
        live_addresses = {p.get("pool_address", "").lower() for p in pool_data}

        for pool in pool_data:
            addr    = pool.get("pool_address", "").lower()
            version = pool.get("version", "")
            price   = pool.get("price_0_in_1", 0) or 0

            is_dead = False
            if version == "V3":
                is_dead = (price == 0) or (pool.get("liquidity", 0) == 0)
            else:
                is_dead = (price == 0)

            if is_dead:
                self._pool_dead_count[addr] = self._pool_dead_count.get(addr, 0) + 1
                if self._pool_dead_count[addr] >= 3 and addr not in self.STALE_POOLS:
                    self.STALE_POOLS.add(addr)
                    self._stale_since[addr] = block_number
                    logger.warning(
                        "Pool %s marked stale (dead for %d consecutive checks at block %d)",
                        addr, self._pool_dead_count[addr], block_number,
                    )
            else:
                # Pool is alive — reset its dead counter
                self._pool_dead_count.pop(addr, None)

        # Also check pools that didn't even appear in pool_data (fetch failed)
        for addr in pool_addresses:
            a = addr.lower()
            if a not in live_addresses and a not in self.STALE_POOLS:
                self._pool_dead_count[a] = self._pool_dead_count.get(a, 0) + 1
                if self._pool_dead_count[a] >= 3:
                    self.STALE_POOLS.add(a)
                    self._stale_since[a] = block_number
                    logger.warning(
                        "Pool %s marked stale (absent from multicall for %d checks at block %d)",
                        a, self._pool_dead_count[a], block_number,
                    )

    # ------------------------------------------------------------------

    def _init_web3(self):
        from onchainprice import get_web3
        self._w3 = get_web3()

    # ------------------------------------------------------------------
    # RPC circuit-breaker helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_rpc_url_list() -> list:
        """
        Builds an ordered list of RPC URLs to cycle through on failure.

        Priority order:
          1. RPC_URL env var (primary)
          2. RPC_URL_FALLBACK env var
          3. Built-in onchainprice.RPC_PROVIDERS list
        """
        urls: list = []
        for env_key in ("RPC_URL", "RPC_URL_FALLBACK"):
            val = os.getenv(env_key, "").strip()
            if val and val not in urls:
                urls.append(val)
        try:
            from onchainprice import RPC_PROVIDERS
            for url in RPC_PROVIDERS:
                if url and url not in urls:
                    urls.append(url)
        except Exception:
            pass
        return urls

    def _switch_rpc(self, reason: str):
        """
        Rotate to the next RPC URL in the list and reinitialise the Web3
        connection.  Called after _RPC_FAILURE_THRESHOLD consecutive failures.

        Logs a warning with the reason and which URL is being tried next.
        If only one URL is configured the switch is a no-op (same URL is
        retried — at least we get a fresh connection object).
        """
        if not self._rpc_urls:
            logger.warning("RPC circuit breaker: no URLs to switch to.")
            return

        previous_index = self._rpc_index
        self._rpc_index = (self._rpc_index + 1) % len(self._rpc_urls)
        next_url = self._rpc_urls[self._rpc_index]

        logger.warning(
            "RPC circuit breaker triggered (%s) — switching from provider #%d to #%d (%s)",
            reason, previous_index, self._rpc_index,
            # Mask API keys: show only the domain portion of the URL
            next_url.split("/")[2] if "/" in next_url else next_url,
        )

        try:
            from web3 import Web3
            self._w3 = Web3(Web3.HTTPProvider(next_url, request_kwargs={"timeout": 10}))
            # Also reset the global onchainprice PRIMARY_W3 so subsequent
            # calls inside that module pick up the new provider
            import onchainprice
            onchainprice.PRIMARY_W3 = self._w3
            logger.info("RPC switch complete — now using provider #%d", self._rpc_index)
        except Exception as exc:
            logger.error("RPC switch failed: %s", exc)

        self._rpc_failure_count = 0


# ---------------------------------------------------------------------------
# Updated find_arbitrage_opportunities wrapper — passes gas + slippage through
# ---------------------------------------------------------------------------

def _find_arbitrage_opportunities_with_gas(
    pool_data,
    min_profit,
    gas_cost_pct,
    trade_size_usd=10_000.0,
    gas_cost_usd=0.0,
):
    """
    Thin wrapper that passes gas_cost_pct, trade_size_usd, and the absolute
    gas_cost_usd into find_arbitrage_opportunities.

    Passing gas_cost_usd (the actual dollar cost for the two-way tx) lets the
    function rescale gas overhead proportionally when it calculates the
    optimal trade size for each pool pair — larger trades pay less % in gas.
    """
    from onchainprice import find_arbitrage_opportunities
    return find_arbitrage_opportunities(
        pool_data,
        min_profit,
        gas_cost_pct=gas_cost_pct,
        trade_size_usd=trade_size_usd,
        gas_cost_usd=gas_cost_usd,
    )


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
