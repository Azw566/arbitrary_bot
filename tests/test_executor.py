"""
tests/test_executor.py
-----------------------
Unit tests for scripts/executor.py — token locking, semaphore, and stats.

All tests are fully offline: no web3, no RPC, no real trade execution.
The Executor is always configured with dry_run=True so _execute_sync()
never touches the network.

Run with:
    python -m pytest tests/test_executor.py -m unit -v
"""

import asyncio
import sys
import os
import time
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from scripts.executor import Executor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WETH_ADDR = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC_ADDR = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
UNI_ADDR  = "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"


def _opp(pair, buy_addr0=WETH_ADDR, buy_addr1=USDC_ADDR,
         sell_addr0=WETH_ADDR, sell_addr1=USDC_ADDR,
         net_profit=0.65):
    """Minimal opportunity dict that Executor._extract_tokens() can parse."""
    return {
        "pair": pair,
        "type": "two_way",
        "buy_pool": {
            "token0": {"address": buy_addr0},
            "token1": {"address": buy_addr1},
        },
        "sell_pool": {
            "token0": {"address": sell_addr0},
            "token1": {"address": sell_addr1},
        },
        "net_profit_percentage": net_profit,
    }


def _make_executor(max_concurrent=3, dry_run=True):
    config = {
        "execution": {
            "dry_run": dry_run,
            "max_concurrent": max_concurrent,
            "min_profit_pct": 0.05,
        }
    }
    return Executor(config)


def _run(coro):
    """Run a coroutine in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ============================================================================
# Token extraction
# ============================================================================

class TestExtractTokens:

    @pytest.mark.unit
    def test_extracts_all_four_token_addresses(self):
        opp = _opp("WETH/USDC", buy_addr0=WETH_ADDR, buy_addr1=USDC_ADDR,
                   sell_addr0=WETH_ADDR, sell_addr1=USDC_ADDR)
        tokens = Executor._extract_tokens(opp)
        assert WETH_ADDR.lower() in tokens
        assert USDC_ADDR.lower() in tokens

    @pytest.mark.unit
    def test_addresses_are_lowercased(self):
        mixed_case_addr = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        opp = _opp("TEST", buy_addr0=mixed_case_addr, buy_addr1=USDC_ADDR,
                   sell_addr0=mixed_case_addr, sell_addr1=USDC_ADDR)
        tokens = Executor._extract_tokens(opp)
        assert mixed_case_addr.lower() in tokens
        assert mixed_case_addr not in tokens  # must be lowercased

    @pytest.mark.unit
    def test_handles_missing_pool_keys(self):
        """Opportunity without buy_pool/sell_pool should return an empty set, not raise."""
        tokens = Executor._extract_tokens({"pair": "X/Y"})
        assert tokens == set()

    @pytest.mark.unit
    def test_deduplicates_shared_tokens(self):
        """Both pools share WETH — should appear only once in the set."""
        opp = _opp("WETH/USDC",
                   buy_addr0=WETH_ADDR, buy_addr1=USDC_ADDR,
                   sell_addr0=WETH_ADDR, sell_addr1=USDC_ADDR)
        tokens = Executor._extract_tokens(opp)
        # WETH and USDC → 2 unique addresses
        assert len(tokens) == 2


# ============================================================================
# Token locking — conflict detection
# ============================================================================

class TestTokenLocking:

    @pytest.mark.unit
    def test_token_lock_prevents_duplicate_trade(self):
        """
        Execute first opportunity (acquires WETH+USDC lock).
        Try second opportunity sharing WETH — should return False immediately.
        """
        executor = _make_executor()

        opp1 = _opp("WETH/USDC")
        opp2 = _opp("WETH/UNI",
                     buy_addr0=WETH_ADDR, buy_addr1=UNI_ADDR,
                     sell_addr0=WETH_ADDR, sell_addr1=UNI_ADDR)

        async def _scenario():
            # Manually inject the lock so opp1 is "in flight"
            executor._ensure_primitives()
            async with executor._lock:
                tokens1 = Executor._extract_tokens(opp1)
                executor._token_locks |= tokens1   # simulate opp1 holding lock

            # Now try opp2 which shares WETH with opp1
            result = await executor.try_execute(opp2)
            return result

        result = _run(_scenario())
        assert result is False, (
            "Second opportunity sharing a locked token should return False"
        )
        assert executor._stats["skipped_conflict"] == 1

    @pytest.mark.unit
    def test_token_lock_released_after_trade(self):
        """
        After a trade completes (even dry-run), the token locks must be released
        so a second opportunity using the same tokens can proceed.
        """
        executor = _make_executor(dry_run=True)

        opp = _opp("WETH/USDC")

        async def _scenario():
            result1 = await executor.try_execute(opp)
            # After completion the lock set must be empty
            return result1, set(executor._token_locks)

        result, locks_after = _run(_scenario())

        assert result is True, "Dry-run should succeed"
        assert locks_after == set(), (
            f"Token locks must be empty after trade completes, got: {locks_after}"
        )

    @pytest.mark.unit
    def test_token_lock_released_after_failed_trade(self):
        """
        Even if the underlying execution raises an exception, the finally block
        must release token locks.
        """
        executor = _make_executor(dry_run=False)

        # Make _execute_sync raise
        executor._execute_sync = MagicMock(side_effect=RuntimeError("simulated failure"))

        opp = _opp("WETH/USDC")

        async def _scenario():
            # try_execute catches the error internally; we just want lock state
            try:
                await executor.try_execute(opp)
            except Exception:
                pass
            return set(executor._token_locks)

        locks_after = _run(_scenario())
        assert locks_after == set(), (
            "Token locks must be released even when execution raises"
        )

    @pytest.mark.unit
    def test_non_conflicting_pairs_run_in_parallel(self):
        """
        Two opportunities sharing NO tokens (WETH/USDC and DAI/UNI) must
        not block each other — both should succeed.
        """
        DAI_ADDR = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
        executor = _make_executor(max_concurrent=2, dry_run=True)

        opp1 = _opp("WETH/USDC")
        opp2 = _opp("DAI/UNI",
                     buy_addr0=DAI_ADDR, buy_addr1=UNI_ADDR,
                     sell_addr0=DAI_ADDR, sell_addr1=UNI_ADDR)

        async def _scenario():
            r1, r2 = await asyncio.gather(
                executor.try_execute(opp1),
                executor.try_execute(opp2),
            )
            return r1, r2

        r1, r2 = _run(_scenario())
        assert r1 is True, "First opportunity should succeed"
        assert r2 is True, "Second opportunity with no shared tokens should also succeed"


# ============================================================================
# Semaphore — max_concurrent cap
# ============================================================================

class TestMaxConcurrent:

    @pytest.mark.unit
    def test_max_concurrent_respected(self):
        """
        With max_concurrent=2 and 5 simultaneous opportunities (no token conflicts),
        the semaphore should limit execution to at most 2 at any instant.

        Strategy: track peak concurrent count via a counter incremented inside
        _execute_sync and decremented after a brief sleep.
        """
        max_concurrent = 2
        executor = _make_executor(max_concurrent=max_concurrent, dry_run=True)

        peak_concurrent = [0]
        current_concurrent = [0]

        original_execute = executor._execute_sync

        def _counting_execute(opp):
            current_concurrent[0] += 1
            peak_concurrent[0] = max(peak_concurrent[0], current_concurrent[0])
            time.sleep(0.05)   # brief sleep to allow overlap to manifest
            current_concurrent[0] -= 1
            return True

        executor._execute_sync = _counting_execute

        # Use tokens that don't conflict: each opp has a unique address set
        opps = [
            _opp(f"TOKEN{i}/USDC",
                 buy_addr0=f"0x{'A' * 39}{i}",
                 buy_addr1=f"0x{'B' * 39}{i}",
                 sell_addr0=f"0x{'A' * 39}{i}",
                 sell_addr1=f"0x{'B' * 39}{i}")
            for i in range(5)
        ]

        async def _scenario():
            await asyncio.gather(*[executor.try_execute(o) for o in opps])

        _run(_scenario())

        assert peak_concurrent[0] <= max_concurrent, (
            f"Peak concurrent={peak_concurrent[0]} exceeded max_concurrent={max_concurrent}"
        )

    @pytest.mark.unit
    def test_semaphore_releases_after_each_trade(self):
        """
        After all trades complete, the semaphore value must be fully restored
        (i.e., all slots are free again).
        """
        max_concurrent = 2
        executor = _make_executor(max_concurrent=max_concurrent, dry_run=True)

        opp = _opp("WETH/USDC")

        async def _scenario():
            await executor.try_execute(opp)
            # Semaphore value should be back to max_concurrent
            return executor._semaphore._value

        sem_value = _run(_scenario())
        assert sem_value == max_concurrent, (
            f"Semaphore should be fully released after trade; got {sem_value}"
        )


# ============================================================================
# Stats tracking
# ============================================================================

class TestStats:

    @pytest.mark.unit
    def test_dry_run_increments_dry_run_stat(self):
        executor = _make_executor(dry_run=True)
        opp = _opp("WETH/USDC")

        _run(executor.try_execute(opp))

        stats = executor.stats()
        assert stats["dry_run"] == 1
        assert stats["executed"] == 0

    @pytest.mark.unit
    def test_skipped_conflict_incremented(self):
        executor = _make_executor(dry_run=True)

        opp1 = _opp("WETH/USDC")
        opp2 = _opp("WETH/UNI",
                     buy_addr0=WETH_ADDR, buy_addr1=UNI_ADDR,
                     sell_addr0=WETH_ADDR, sell_addr1=UNI_ADDR)

        async def _scenario():
            executor._ensure_primitives()
            async with executor._lock:
                executor._token_locks |= Executor._extract_tokens(opp1)
            await executor.try_execute(opp2)

        _run(_scenario())

        stats = executor.stats()
        assert stats["skipped_conflict"] == 1

    @pytest.mark.unit
    def test_stats_returns_copy(self):
        """stats() must return a copy — mutating it must not affect internal state."""
        executor = _make_executor()
        s = executor.stats()
        s["dry_run"] = 9999
        assert executor._stats["dry_run"] != 9999

    @pytest.mark.unit
    def test_initial_stats_all_zero(self):
        executor = _make_executor()
        stats = executor.stats()
        for key, value in stats.items():
            assert value == 0, f"Expected stats[{key}]=0 on init, got {value}"


# ============================================================================
# Dry-run vs live mode
# ============================================================================

class TestDryRunMode:

    @pytest.mark.unit
    def test_dry_run_true_returns_true_and_increments_stat(self):
        """
        In dry_run=True mode, try_execute() must return True and increment
        the 'dry_run' stat without calling execute_arbitrage.

        Note: _execute_sync imports execute_arbitrage locally via
            from onchainprice import execute_arbitrage
        The easiest offline approach is to override _execute_sync directly.
        """
        executor = _make_executor(dry_run=True)
        opp = _opp("WETH/USDC")

        # Patch _try_get_fb_provider to return None so no Flashbots path
        executor._try_get_fb_provider = lambda: None

        result = _run(executor.try_execute(opp))

        assert result is True, "dry_run=True should return True"
        assert executor._stats["dry_run"] == 1
        assert executor._stats["executed"] == 0

    @pytest.mark.unit
    def test_dry_run_false_calls_execute_arbitrage(self):
        """
        In dry_run=False mode, execute_arbitrage (imported locally inside
        _execute_sync via 'from onchainprice import execute_arbitrage') must
        be called.  We patch it at the source module level.
        """
        config = {
            "execution": {
                "dry_run": False,
                "max_concurrent": 1,
                "min_profit_pct": 0.05,
            }
        }
        executor = Executor(config)
        opp = _opp("WETH/USDC")

        mock_result = {"success": True}
        # Patch at onchainprice module level since executor does
        # 'from onchainprice import execute_arbitrage' inside _execute_sync
        with patch("onchainprice.execute_arbitrage", return_value=mock_result) as mock_exec:
            result = _run(executor.try_execute(opp))

        assert mock_exec.called, "execute_arbitrage must be called in live mode"
        assert result is True
        assert executor._stats["executed"] == 1
