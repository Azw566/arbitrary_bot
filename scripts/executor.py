"""
Executor
--------
Parallel trade execution with per-token conflict prevention.

Each opportunity locks the tokens it touches so two trades that share a
token cannot run simultaneously (which would cause a nonce conflict or
double-spend).  A semaphore caps the total number of live executions.

MEV Protection
--------------
If the environment variable FLASHBOTS_AUTH_KEY is set the executor will
submit transactions through the Flashbots relay instead of broadcasting
them to the public mempool, providing front-running protection.
If the variable is absent the executor falls back to normal RPC submission.
In dry_run=true mode Flashbots bundles are logged but never actually posted.
"""

import asyncio
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, Set

sys.path.insert(0, os.path.dirname(__file__))

logger = logging.getLogger(__name__)

# Lazily-initialised Flashbots provider (None = use normal RPC)
_fb_provider = None
_fb_provider_initialised = False


def _get_fb_provider(w3):
    """Return the module-level Flashbots provider, initialising it once."""
    global _fb_provider, _fb_provider_initialised
    if not _fb_provider_initialised:
        _fb_provider_initialised = True
        try:
            from flashbots_relay import get_flashbots_provider
            _fb_provider = get_flashbots_provider(w3)
            if _fb_provider:
                logger.info(
                    "Flashbots MEV protection ENABLED | relay=%s",
                    _fb_provider.get("relay_url"),
                )
            else:
                logger.info(
                    "Flashbots MEV protection DISABLED "
                    "(set FLASHBOTS_AUTH_KEY to enable)"
                )
        except Exception as exc:
            logger.warning("Could not load flashbots_relay module: %s", exc)
    return _fb_provider


class Executor:
    """
    Parameters
    ----------
    config           : full config dict (uses 'execution' sub-key)
    contract_address : deployed arbitrage contract address (or None in dry-run)
    """

    def __init__(self, config: dict, contract_address: Optional[str] = None):
        exec_cfg = config.get("execution", {})
        self.dry_run         = exec_cfg.get("dry_run", True)
        self.max_concurrent  = exec_cfg.get("max_concurrent", 3)
        self.min_profit_pct  = exec_cfg.get("min_profit_pct", 0.5)
        self.contract_address = contract_address

        # Asyncio primitives — created lazily inside the event loop
        self._lock: Optional[asyncio.Lock]      = None
        self._semaphore: Optional[asyncio.Semaphore] = None

        # Set of token addresses currently held by a live execution
        self._token_locks: Set[str] = set()

        # Thread pool for the blocking web3 / execute_arbitrage calls
        self._thread_pool = ThreadPoolExecutor(
            max_workers=self.max_concurrent,
            thread_name_prefix="exec",
        )

        self._stats = {
            "executed":         0,
            "dry_run":          0,
            "skipped_conflict": 0,
            "skipped_stale":    0,
            "failed":           0,
        }

    # ------------------------------------------------------------------
    # Lazy initialisation (must be called from inside the running loop)
    # ------------------------------------------------------------------

    def _ensure_primitives(self):
        if self._lock is None:
            self._lock      = asyncio.Lock()
            self._semaphore = asyncio.Semaphore(self.max_concurrent)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def try_execute(self, opportunity: dict) -> bool:
        """
        Attempts to execute an arbitrage opportunity.

        - Returns False immediately if any token is already being traded.
        - Locks the tokens, acquires the concurrency semaphore, then runs
          the blocking execution in the thread pool.
        - Releases tokens on completion regardless of outcome.
        """
        self._ensure_primitives()

        tokens = self._extract_tokens(opportunity)

        async with self._lock:
            if tokens & self._token_locks:
                pair = opportunity.get("pair", "?")
                logger.debug("Skipping %s — token conflict", pair)
                self._stats["skipped_conflict"] += 1
                return False
            self._token_locks |= tokens

        try:
            async with self._semaphore:
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    self._thread_pool,
                    self._execute_sync,
                    opportunity,
                )
        finally:
            async with self._lock:
                self._token_locks -= tokens

    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def shutdown(self):
        self._thread_pool.shutdown(wait=False)

    # ------------------------------------------------------------------
    # Blocking execution (runs in thread pool)
    # ------------------------------------------------------------------

    def _execute_sync(self, opportunity: dict) -> bool:
        from onchainprice import execute_arbitrage

        pair   = opportunity.get("pair", "?")
        net    = opportunity.get("net_profit_percentage", 0.0)
        label  = f"{pair} | net={net:.4f}%"

        if self.dry_run:
            # In dry-run mode also exercise the Flashbots path (log only)
            fb = self._try_get_fb_provider()
            if fb:
                logger.info("[DRY RUN / FLASHBOTS] %s", label)
                self._stats["dry_run"] += 1
                return True
            logger.info("[DRY RUN]  %s", label)
            self._stats["dry_run"] += 1
            return True

        try:
            result = execute_arbitrage(
                opportunity,
                contract_address=self.contract_address,
            )
            if result and result.get("success"):
                # If a signed_tx is available in the result, try Flashbots first
                signed_tx = result.get("signed_tx")
                if signed_tx:
                    submitted = self._submit_via_flashbots(signed_tx, opportunity)
                    if submitted:
                        logger.info("[FLASHBOTS] %s", label)
                        self._stats["executed"] += 1
                        return True
                    # Flashbots failed or not configured — fall through to normal path
                logger.info("[EXECUTED] %s", label)
                self._stats["executed"] += 1
                return True
            else:
                reason = result.get("reason", "unknown") if result else "no result"
                logger.warning("[FAILED]   %s — %s", label, reason)
                self._stats["failed"] += 1
                return False
        except Exception as exc:
            logger.error("[ERROR]    %s — %s", label, exc)
            self._stats["failed"] += 1
            return False

    def _try_get_fb_provider(self):
        """Return Flashbots provider if configured, else None."""
        try:
            from onchainprice import get_web3
            w3 = get_web3()
            return _get_fb_provider(w3)
        except Exception:
            return None

    def _submit_via_flashbots(self, signed_tx, opportunity: dict) -> bool:
        """
        Attempt to submit a signed transaction via the Flashbots relay.

        Returns True if the bundle was accepted, False if Flashbots is not
        configured or the submission failed (caller should fall back to normal tx).
        """
        fb = self._try_get_fb_provider()
        if fb is None:
            return False

        try:
            from onchainprice import get_web3
            from flashbots_relay import build_flashbots_bundle, submit_bundle
            w3 = get_web3()
            block_number = w3.eth.block_number + 1  # target next block
            bundle = build_flashbots_bundle(signed_tx, block_number)
            return submit_bundle(bundle, fb, dry_run=self.dry_run)
        except Exception as exc:
            logger.error("Flashbots submission error: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_tokens(opportunity: dict) -> Set[str]:
        """Returns the set of token addresses touched by this opportunity."""
        tokens: Set[str] = set()
        for pool_key in ("buy_pool", "sell_pool"):
            pool = opportunity.get(pool_key, {})
            for token_key in ("token0", "token1"):
                token = pool.get(token_key, {})
                if isinstance(token, dict):
                    addr = token.get("address", "")
                    if addr:
                        tokens.add(addr.lower())
        return tokens
