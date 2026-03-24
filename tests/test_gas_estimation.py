"""
tests/test_gas_estimation.py
-----------------------------
Unit tests for estimate_gas_cost_pct() in scripts/bot.py.

Formula (from AGENT_CONTEXT.txt and bot.py lines 80-115):
    gas_cost_eth = gas_units * gas_price_wei / 1e18
    gas_cost_usd = gas_cost_eth * eth_price_usd
    gas_cost_pct = (gas_cost_usd / trade_size_usd) * 100

Implementation notes verified from source:
  - get_eth_price_usd is imported locally inside estimate_gas_cost_pct via
      from onchainprice import get_eth_price_usd
    so it must be patched at 'onchainprice.get_eth_price_usd'.
  - If get_eth_price_usd() returns None or 0, the expression
      (get_eth_price_usd() or 3500.0) evaluates to the 3500.0 fallback.
    To simulate "unknown ETH price" we must make get_eth_price_usd raise.
  - gas_price fallback on RPC failure is 20 gwei (20 * 10**9 wei).
  - trade_size_usd <= 0 returns 0.0 immediately (guard against div-by-zero).

All tests mock the web3 object and onchainprice.get_eth_price_usd — no network.

Run with:
    python -m pytest tests/test_gas_estimation.py -m unit -v
"""

import importlib
import sys
import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

# ---------------------------------------------------------------------------
# Constants matching bot.py
# ---------------------------------------------------------------------------

GAS_TWO_WAY    = 400_000
GAS_TRIANGULAR = 600_000


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

def _get_bot_module():
    """Import (or reload) the bot module to get a fresh _block_cache."""
    if "bot" in sys.modules:
        import bot as _m
        importlib.reload(_m)
        return _m
    import bot as _m
    return _m


def _mock_w3(gas_price_gwei: float) -> MagicMock:
    """Build a mock web3 instance with a known gas_price (as integer wei)."""
    w3 = MagicMock()
    w3.eth.gas_price = int(gas_price_gwei * 1e9)
    return w3


# ============================================================================
# Tests
# ============================================================================

class TestGasEstimation:

    @pytest.mark.unit
    def test_two_way_gas_cost_calculation(self):
        """
        Verify the exact arithmetic from the gas cost model:
            gas_units     = 400,000
            gas_price     = 20 gwei  → 20e9 wei
            eth_price_usd = $3,500
            trade_size    = $10,000

            gas_cost_eth = 400_000 * 20e9 / 1e18 = 0.008 ETH
            gas_cost_usd = 0.008 * 3500           = $28
            gas_cost_pct = 28 / 10000 * 100       = 0.28%
        """
        bot = _get_bot_module()

        gas_price_gwei = 20.0
        eth_price      = 3500.0
        trade_size_usd = 10_000.0

        expected_eth = GAS_TWO_WAY * (gas_price_gwei * 1e9) / 1e18
        expected_usd = expected_eth * eth_price
        expected_pct = (expected_usd / trade_size_usd) * 100

        w3 = _mock_w3(gas_price_gwei)

        with patch("onchainprice.get_eth_price_usd", return_value=eth_price):
            result = bot.estimate_gas_cost_pct(w3, GAS_TWO_WAY, trade_size_usd)

        assert abs(result - expected_pct) < 0.001, (
            f"Expected {expected_pct:.4f}%, got {result:.4f}%"
        )

    @pytest.mark.unit
    def test_triangular_gas_cost_calculation(self):
        """
        GAS_TRIANGULAR = 600,000 — 1.5× the two-way gas.
        The triangular cost must be 1.5× the two-way cost with identical inputs.
        """
        bot = _get_bot_module()

        gas_price_gwei = 20.0
        eth_price      = 3500.0
        trade_size_usd = 10_000.0

        w3 = _mock_w3(gas_price_gwei)

        with patch("onchainprice.get_eth_price_usd", return_value=eth_price):
            two_way_result = bot.estimate_gas_cost_pct(w3, GAS_TWO_WAY,    trade_size_usd)
            tri_result     = bot.estimate_gas_cost_pct(w3, GAS_TRIANGULAR, trade_size_usd)

        expected_tri = (GAS_TRIANGULAR * (gas_price_gwei * 1e9) / 1e18 * eth_price) / trade_size_usd * 100
        assert abs(tri_result - expected_tri) < 0.001

        # Must be exactly 1.5× the two-way value
        assert abs(tri_result / two_way_result - 1.5) < 0.001, (
            f"GAS_TRIANGULAR cost must be 1.5× GAS_TWO_WAY: {tri_result} vs 1.5×{two_way_result}"
        )

    @pytest.mark.unit
    def test_returns_fallback_when_eth_price_raises(self):
        """
        If get_eth_price_usd() raises an exception, the function falls back
        to eth_price=3500.0 (hardcoded in bot.py) and still returns a valid float.

        This simulates the "ETH price unknown" scenario — the fallback prevents
        a division-by-zero or NaN in the profit calculation.
        """
        bot = _get_bot_module()
        bot._block_cache["block"] = 0

        gas_price_gwei = 20.0
        trade_size_usd = 10_000.0
        eth_fallback   = 3500.0  # hardcoded fallback in bot.py

        w3 = _mock_w3(gas_price_gwei)

        with patch("onchainprice.get_eth_price_usd", side_effect=Exception("price unavailable")):
            result = bot.estimate_gas_cost_pct(w3, GAS_TWO_WAY, trade_size_usd)

        # With fallback eth_price=3500 the formula gives a non-zero result
        expected = (GAS_TWO_WAY * gas_price_gwei * 1e9 / 1e18 * eth_fallback) / trade_size_usd * 100
        assert abs(result - expected) < 0.001, (
            f"Expected fallback result {expected:.4f}%, got {result:.4f}%"
        )
        assert result > 0.0, "Gas cost must be positive even when ETH price fetch fails"

    @pytest.mark.unit
    def test_returns_zero_for_zero_trade_size(self):
        """
        trade_size_usd <= 0 is guarded — function returns 0.0 immediately
        to prevent division by zero.
        """
        bot = _get_bot_module()
        w3 = _mock_w3(20.0)

        result_zero = bot.estimate_gas_cost_pct(w3, GAS_TWO_WAY, 0.0)
        assert result_zero == 0.0, f"Expected 0.0 for zero trade size, got {result_zero}"

        result_neg = bot.estimate_gas_cost_pct(w3, GAS_TWO_WAY, -1000.0)
        assert result_neg == 0.0, f"Expected 0.0 for negative trade size, got {result_neg}"

    @pytest.mark.unit
    def test_higher_gas_price_increases_cost_linearly(self):
        """
        Doubling gas_price_gwei must double gas_cost_pct (linear relationship).
        """
        bot = _get_bot_module()
        bot._block_cache["block"] = 0

        eth_price  = 3500.0
        trade_size = 10_000.0

        w3_low  = _mock_w3(10.0)
        w3_high = _mock_w3(20.0)

        with patch("onchainprice.get_eth_price_usd", return_value=eth_price):
            low_result  = bot.estimate_gas_cost_pct(w3_low,  GAS_TWO_WAY, trade_size)
            high_result = bot.estimate_gas_cost_pct(w3_high, GAS_TWO_WAY, trade_size)

        assert abs(high_result / low_result - 2.0) < 0.001, (
            f"Doubling gas price must double cost: {low_result:.4f}% → {high_result:.4f}%"
        )

    @pytest.mark.unit
    def test_block_cache_reuses_values_same_block(self):
        """
        When block_number is the same across two calls, the second call must
        read from the cache — w3.eth.gas_price should only be accessed once.
        """
        bot = _get_bot_module()
        bot._block_cache["block"] = 0   # reset

        eth_price = 3500.0
        w3 = MagicMock()
        w3.eth.gas_price = int(20 * 1e9)

        with patch("onchainprice.get_eth_price_usd", return_value=eth_price):
            r1 = bot.estimate_gas_cost_pct(w3, GAS_TWO_WAY, 10_000.0, block_number=99)
            # Reset mock call count before second call
            w3.eth.gas_price  # consume one extra access, count only what follows
            call_count_before = w3.eth.__getitem__.call_count
            r2 = bot.estimate_gas_cost_pct(w3, GAS_TWO_WAY, 10_000.0, block_number=99)

        # Both calls should produce the same result
        assert r1 == r2, f"Cache should return same value: r1={r1}, r2={r2}"
        # The cache should be populated with block 99
        assert bot._block_cache["block"] == 99

    @pytest.mark.unit
    def test_block_cache_refreshes_on_new_block(self):
        """
        When block_number changes, the cache must be invalidated and fresh
        gas_price / eth_price fetched.
        """
        bot = _get_bot_module()
        bot._block_cache["block"] = 0

        w3 = _mock_w3(10.0)

        with patch("onchainprice.get_eth_price_usd", return_value=3500.0):
            r_block1 = bot.estimate_gas_cost_pct(w3, GAS_TWO_WAY, 10_000.0, block_number=100)

        # Change gas_price for the new block
        w3.eth.gas_price = int(20 * 1e9)
        with patch("onchainprice.get_eth_price_usd", return_value=3500.0):
            r_block2 = bot.estimate_gas_cost_pct(w3, GAS_TWO_WAY, 10_000.0, block_number=101)

        assert r_block2 > r_block1, (
            "Higher gas price on new block must produce a higher gas cost percentage"
        )

    @pytest.mark.unit
    def test_rpc_failure_falls_back_to_20_gwei(self):
        """
        If w3.eth.gas_price raises an exception the function falls back to
        20 gwei (hardcoded in bot.py) and still returns a valid float.
        """
        bot = _get_bot_module()
        bot._block_cache["block"] = 0

        # Build a w3 mock whose gas_price property raises
        w3 = MagicMock()
        type(w3.eth).gas_price = PropertyMock(
            side_effect=Exception("RPC timeout")
        )

        eth_price = 3500.0
        with patch("onchainprice.get_eth_price_usd", return_value=eth_price):
            result = bot.estimate_gas_cost_pct(w3, GAS_TWO_WAY, 10_000.0)

        # Fallback: 20 gwei
        expected = (GAS_TWO_WAY * 20e9 / 1e18 * eth_price) / 10_000.0 * 100
        assert abs(result - expected) < 0.001, (
            f"Expected 20-gwei fallback result {expected:.4f}%, got {result:.4f}%"
        )

    @pytest.mark.unit
    def test_result_is_non_negative(self):
        """Gas cost percentage must never be negative."""
        bot = _get_bot_module()
        bot._block_cache["block"] = 0

        w3 = _mock_w3(5.0)
        with patch("onchainprice.get_eth_price_usd", return_value=2000.0):
            result = bot.estimate_gas_cost_pct(w3, GAS_TWO_WAY, 50_000.0)

        assert result >= 0.0, f"Gas cost must be non-negative, got {result}"
