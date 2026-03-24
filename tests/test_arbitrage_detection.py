"""
tests/test_arbitrage_detection.py
----------------------------------
Unit tests for find_arbitrage_opportunities() (scripts/onchainprice.py)
and find_triangular_opportunities() (scripts/triangular.py).

All tests are pure offline — no RPC calls, no web3, no network.

Key implementation details verified by reading the source:
  - find_arbitrage_opportunities() also applies dynamic slippage via
    calculate_price_impact() and compute the optimal trade size via
    calculate_optimal_trade_size().
  - V3 liquidity values must be large (≥1e14) so that slippage < 0.1%
    at the $50k optimal trade size, otherwise the slippage cap masks the spread.
  - The result dict contains: pair, buy_pool, sell_pool,
    gross_profit_percentage, total_fees_percentage, gas_cost_pct,
    slippage_pct, net_profit_percentage, optimal_trade_size_usd,
    estimated_profit_usd.
  - MIN_TRADE_SIZE_USD = $1,000 (below this, the pair is skipped as dust).

Run with:
    python -m pytest tests/test_arbitrage_detection.py -m unit -v
"""

import pytest

from scripts.onchainprice import find_arbitrage_opportunities
from scripts.triangular import find_triangular_opportunities

# ============================================================================
# Pool-building helpers
# ============================================================================

WETH_ADDR = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC_ADDR = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
UNI_ADDR  = "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"

# Realistic V3 liquidity — large enough that slippage stays below 0.1% at $50k
LIQUID_V3 = int(1e14)


def _v3(pool_address, price, fee_pct=0.05, liquidity=LIQUID_V3,
        pair="WETH/USDC",
        token0_sym="WETH", token0_addr=WETH_ADDR,
        token1_sym="USDC", token1_addr=USDC_ADDR):
    """Build a V3 pool dict matching the structure from batch_get_pool_data()."""
    return {
        "pool_address": pool_address,
        "version": "V3",
        "dex": "UniV3",
        "token0": {"address": token0_addr, "symbol": token0_sym,
                   "decimals": 18, "reserve": 0},
        "token1": {"address": token1_addr, "symbol": token1_sym,
                   "decimals": 6, "reserve": 0},
        "price_0_in_1": price,
        "price_1_in_0": 1.0 / price if price else 0.0,
        "fee_percentage": fee_pct,
        "fee_tier": int(fee_pct * 10_000),
        "liquidity": liquidity,
        "pair": pair,
    }


def _v2(pool_address, weth_reserve, usdc_reserve, fee_pct=0.3,
        pair="WETH/USDC",
        token0_sym="WETH", token0_addr=WETH_ADDR,
        token1_sym="USDC", token1_addr=USDC_ADDR):
    """Build a V2 pool dict matching the structure from batch_get_pool_data()."""
    price = usdc_reserve / weth_reserve if weth_reserve else 0.0
    return {
        "pool_address": pool_address,
        "version": "V2",
        "dex": "Sushi",
        "token0": {"address": token0_addr, "symbol": token0_sym,
                   "decimals": 18, "reserve": weth_reserve},
        "token1": {"address": token1_addr, "symbol": token1_sym,
                   "decimals": 6, "reserve": usdc_reserve},
        "price_0_in_1": price,
        "price_1_in_0": 1.0 / price if price else 0.0,
        "fee_percentage": fee_pct,
        "fee_tier": int(fee_pct * 10_000),
        "pair": pair,
    }


# ============================================================================
# Two-way arbitrage — find_arbitrage_opportunities()
# ============================================================================

class TestTwoWayArbitrage:

    @pytest.mark.unit
    def test_finds_profit_when_price_differs_across_dex(self):
        """
        Two liquid V3 WETH/USDC pools with a 1% gross spread.
            fee1 = 0.05%, fee2 = 0.30%, combined = 0.35%
            slippage at liq=1e14 ≈ 0.10% (both pools, capped at 2% each)
            net ≈ 1.0 - 0.35 - 0.10 = 0.55% > min_profit_pct=0.05 → detected.
        """
        cheap     = _v3("0xCHEAP",     2000.0, fee_pct=0.05, liquidity=LIQUID_V3)
        expensive = _v3("0xEXPENSIVE", 2020.0, fee_pct=0.30, liquidity=LIQUID_V3)

        opps = find_arbitrage_opportunities([cheap, expensive], min_profit_percentage=0.05)

        assert len(opps) == 1, f"Expected 1 opportunity, got {len(opps)}"
        opp = opps[0]
        assert opp["pair"] == "WETH/USDC"
        assert opp["buy_pool"]["pool_address"]  == "0xCHEAP"
        assert opp["sell_pool"]["pool_address"] == "0xEXPENSIVE"
        assert opp["net_profit_percentage"] > 0.05
        assert opp["gross_profit_percentage"] > 0.0

    @pytest.mark.unit
    def test_no_opportunity_when_spread_below_fees(self):
        """
        0.05% fee V3 at 2000 vs 0.30% fee V3 at 2004 — gross spread 0.2%.
        Combined fees 0.35% > 0.2% spread → net is negative → no opportunity.
        """
        cheap     = _v3("0xCHEAP2",     2000.0, fee_pct=0.05, liquidity=LIQUID_V3)
        expensive = _v3("0xEXPENSIVE2", 2004.0, fee_pct=0.30, liquidity=LIQUID_V3)

        opps = find_arbitrage_opportunities([cheap, expensive], min_profit_percentage=0.05)

        assert opps == [], f"Expected no opportunities, got {opps}"

    @pytest.mark.unit
    def test_filters_illiquid_v3_pools(self):
        """
        A V3 pool with liquidity=0 must be excluded before price comparison.
        If the only expensive pool has zero liquidity, no pair of liquid
        pools remains → no opportunity.
        """
        liquid   = _v3("0xLIQUID",   2000.0, fee_pct=0.05, liquidity=LIQUID_V3)
        illiquid = _v3("0xILLIQUID", 2020.0, fee_pct=0.30, liquidity=0)

        opps = find_arbitrage_opportunities([liquid, illiquid], min_profit_percentage=0.05)

        assert opps == [], (
            "Pool with liquidity=0 should be filtered — no opportunity should be detected"
        )

    @pytest.mark.unit
    def test_filters_low_reserve_v2_pools(self):
        """
        A V2 pool whose WETH reserve < MIN_WETH_RESERVE (0.5 ETH) is excluded.
        With only 0.01 WETH the stale pool is filtered, leaving only one
        liquid pool → cannot form a pair → no opportunity.
        """
        liquid   = _v3("0xLIQUID2", 2000.0, fee_pct=0.05, liquidity=LIQUID_V3)
        # 0.01 WETH reserve — well below the 0.5 ETH threshold
        low_res  = _v2("0xLOWRES", weth_reserve=0.01, usdc_reserve=20.2)

        opps = find_arbitrage_opportunities([liquid, low_res], min_profit_percentage=0.05)

        assert opps == [], (
            "V2 pool with WETH reserve < 0.5 ETH should be filtered"
        )

    @pytest.mark.unit
    def test_rejects_outlier_spread_above_50pct(self):
        """
        Gross spread > MAX_GROSS_SPREAD (50%) is a data artefact and should be
        rejected even if it would otherwise be profitable.
        2000 vs 3200 USDC = 60% spread → rejected.
        """
        cheap   = _v3("0xCHEAP3", 2000.0, fee_pct=0.05, liquidity=LIQUID_V3)
        outlier = _v3("0xOUTLIER", 3200.0, fee_pct=0.30, liquidity=LIQUID_V3)

        opps = find_arbitrage_opportunities([cheap, outlier], min_profit_percentage=0.05)

        assert opps == [], (
            "Gross spread of 60% should be rejected as a data artefact (MAX_GROSS_SPREAD=50%)"
        )

    @pytest.mark.unit
    def test_accounts_for_gas_cost(self):
        """
        gas_cost_pct is subtracted from net profit.
        Baseline net ≈ 0.55% (1% gross, 0.35% fees, ~0.10% slippage).
        Adding gas_cost_pct=0.40 should reduce net by 0.40 → ≈ 0.15%.
        """
        cheap     = _v3("0xCHEAP4",     2000.0, fee_pct=0.05, liquidity=LIQUID_V3)
        expensive = _v3("0xEXPENSIVE4", 2020.0, fee_pct=0.30, liquidity=LIQUID_V3)

        # Reference run without gas cost
        opps_no_gas = find_arbitrage_opportunities(
            [cheap, expensive],
            min_profit_percentage=0.05,
            gas_cost_pct=0.0,
        )
        assert len(opps_no_gas) == 1
        baseline_net = opps_no_gas[0]["net_profit_percentage"]

        # Same run with 0.40% gas cost
        gas_cost_pct = 0.40
        opps_gas = find_arbitrage_opportunities(
            [cheap, expensive],
            min_profit_percentage=0.05,
            gas_cost_pct=gas_cost_pct,
        )

        assert len(opps_gas) == 1, (
            f"Expected 1 opportunity after gas deduction, got {len(opps_gas)}"
        )
        opp = opps_gas[0]
        # Net must be reduced by exactly gas_cost_pct
        expected_net = round(baseline_net - gas_cost_pct, 6)
        assert abs(opp["net_profit_percentage"] - expected_net) < 0.001, (
            f"net={opp['net_profit_percentage']:.4f}% expected ~{expected_net:.4f}% "
            f"(baseline={baseline_net:.4f}% - gas={gas_cost_pct}%)"
        )
        assert opp["gas_cost_pct"] == round(gas_cost_pct, 4)

    @pytest.mark.unit
    def test_gas_cost_eliminates_marginal_opportunity(self):
        """
        When gas_cost_pct is large enough the opportunity falls below min_profit
        and should be removed from results entirely.
        """
        cheap     = _v3("0xCHEAP5",     2000.0, fee_pct=0.05, liquidity=LIQUID_V3)
        expensive = _v3("0xEXPENSIVE5", 2020.0, fee_pct=0.30, liquidity=LIQUID_V3)

        opps = find_arbitrage_opportunities(
            [cheap, expensive],
            min_profit_percentage=0.05,
            gas_cost_pct=5.0,   # 5% gas swamps the entire spread
        )

        assert opps == [], "High gas should eliminate the marginal opportunity"

    @pytest.mark.unit
    def test_result_sorted_by_net_profit_descending(self):
        """
        When multiple pairs have opportunities, results are sorted best-first.
        """
        # WETH/USDC: 1% gross spread
        cheap_wu   = _v3("0xCWU",  2000.0, fee_pct=0.05, liquidity=LIQUID_V3)
        exp_wu     = _v3("0xEWU",  2020.0, fee_pct=0.30, liquidity=LIQUID_V3)

        # UNI/WETH: 2% gross spread (more profitable)
        cheap_uw   = _v3(
            "0xCUW", 0.010, fee_pct=0.05, liquidity=LIQUID_V3,
            pair="UNI/WETH",
            token0_sym="UNI",  token0_addr=UNI_ADDR,
            token1_sym="WETH", token1_addr=WETH_ADDR,
        )
        exp_uw     = _v3(
            "0xEUW", 0.01020, fee_pct=0.30, liquidity=LIQUID_V3,
            pair="UNI/WETH",
            token0_sym="UNI",  token0_addr=UNI_ADDR,
            token1_sym="WETH", token1_addr=WETH_ADDR,
        )

        opps = find_arbitrage_opportunities(
            [cheap_wu, exp_wu, cheap_uw, exp_uw],
            min_profit_percentage=0.05,
        )

        assert len(opps) >= 1
        profits = [o["net_profit_percentage"] for o in opps]
        assert profits == sorted(profits, reverse=True), (
            "Opportunities must be sorted by net_profit_percentage descending"
        )

    @pytest.mark.unit
    def test_empty_pool_list_returns_empty(self):
        """Edge case: empty input → empty output, no exception."""
        opps = find_arbitrage_opportunities([], min_profit_percentage=0.05)
        assert opps == []

    @pytest.mark.unit
    def test_single_pool_per_pair_returns_empty(self):
        """Only one liquid pool per pair → no price comparison possible → no opp."""
        single = _v3("0xSINGLE", 2000.0, fee_pct=0.05, liquidity=LIQUID_V3)
        opps = find_arbitrage_opportunities([single], min_profit_percentage=0.05)
        assert opps == []

    @pytest.mark.unit
    def test_result_dict_contains_all_required_keys(self):
        """
        Opportunity dict must contain all keys used downstream by bot.py
        and the Executor.
        """
        REQUIRED_KEYS = {
            "pair", "buy_pool", "sell_pool",
            "gross_profit_percentage", "total_fees_percentage",
            "gas_cost_pct", "slippage_pct",
            "net_profit_percentage",
            "optimal_trade_size_usd", "estimated_profit_usd",
        }
        cheap     = _v3("0xCHEAPKEY",     2000.0, fee_pct=0.05, liquidity=LIQUID_V3)
        expensive = _v3("0xEXPENSIVEKEY", 2020.0, fee_pct=0.30, liquidity=LIQUID_V3)

        opps = find_arbitrage_opportunities([cheap, expensive], min_profit_percentage=0.05)
        assert len(opps) == 1
        missing = REQUIRED_KEYS - set(opps[0].keys())
        assert not missing, f"Opportunity dict is missing keys: {missing}"

    @pytest.mark.unit
    def test_buy_pool_has_lower_price_than_sell_pool(self):
        """buy_pool price must always be lower than sell_pool price (buy cheap, sell dear)."""
        cheap     = _v3("0xCHEAPDIR",     2000.0, fee_pct=0.05, liquidity=LIQUID_V3)
        expensive = _v3("0xEXPENSIVEDIR", 2020.0, fee_pct=0.30, liquidity=LIQUID_V3)

        opps = find_arbitrage_opportunities([cheap, expensive], min_profit_percentage=0.05)
        assert len(opps) == 1
        opp = opps[0]
        assert opp["buy_pool"]["price_0_in_1"] < opp["sell_pool"]["price_0_in_1"], (
            "buy_pool must have the lower price"
        )


# ============================================================================
# Triangular arbitrage — find_triangular_opportunities()
# ============================================================================

class TestTriangularArbitrage:

    @pytest.mark.unit
    def test_detects_profitable_triangle(self, triangular_profitable_pools):
        """
        A well-calibrated WETH→USDC→UNI→WETH cycle with product > 1.0 + fees
        should be detected and returned.
        """
        opps = find_triangular_opportunities(
            triangular_profitable_pools,
            min_profit_pct=0.05,
            gas_cost_pct=0.0,
        )

        assert len(opps) >= 1, (
            "Expected at least 1 triangular opportunity with profitable rates"
        )
        opp = opps[0]
        assert opp["type"] == "triangular"
        assert opp["net_profit_percentage"] > 0.05
        assert "legs" in opp and len(opp["legs"]) == 3

    @pytest.mark.unit
    def test_rejects_unprofitable_triangle(self, triangular_unprofitable_pools):
        """
        When the product of fee-adjusted rates is below 1.0, no opportunity
        should be returned.
        """
        opps = find_triangular_opportunities(
            triangular_unprofitable_pools,
            min_profit_pct=0.05,
            gas_cost_pct=0.0,
        )

        assert opps == [], (
            f"Unprofitable triangle should return no opportunities, got: {opps}"
        )

    @pytest.mark.unit
    def test_deduplicates_cycles(self, triangular_profitable_pools):
        """
        The same 3-token cycle should appear only once regardless of traversal
        direction. Passing the same pool list twice gives the graph bidirectional
        edges for free; the frozenset deduplication must prevent double-counting.
        """
        single_pass = find_triangular_opportunities(
            triangular_profitable_pools,
            min_profit_pct=0.05,
            gas_cost_pct=0.0,
        )
        # Supply pools twice — same edges, seen set should dedup
        double_pass = find_triangular_opportunities(
            triangular_profitable_pools + triangular_profitable_pools,
            min_profit_pct=0.05,
            gas_cost_pct=0.0,
        )

        assert len(double_pass) == len(single_pass), (
            f"Deduplication failed: single={len(single_pass)} vs double={len(double_pass)}"
        )

    @pytest.mark.unit
    def test_gas_cost_eliminates_marginal_triangle(self, triangular_profitable_pools):
        """
        A very high gas cost should turn a ~2.6% gross into a negative net,
        dropping the opportunity entirely.
        """
        opps = find_triangular_opportunities(
            triangular_profitable_pools,
            min_profit_pct=0.05,
            gas_cost_pct=5.0,   # 5% gas swamps the ~2.6% gross profit
        )

        assert opps == [], (
            "5% gas cost should eliminate all opportunities in the profitable fixture"
        )

    @pytest.mark.unit
    def test_result_sorted_best_first(self, triangular_profitable_pools):
        """Results must be sorted by net_profit_percentage descending."""
        opps = find_triangular_opportunities(
            triangular_profitable_pools,
            min_profit_pct=0.0,
            gas_cost_pct=0.0,
        )
        if len(opps) > 1:
            profits = [o["net_profit_percentage"] for o in opps]
            assert profits == sorted(profits, reverse=True)

    @pytest.mark.unit
    def test_triangular_required_keys_present(self, triangular_profitable_pools):
        """Opportunity dict must contain all required keys."""
        REQUIRED = {
            "type", "pair", "legs",
            "product", "gross_profit_percentage",
            "gas_cost_pct", "net_profit_percentage",
        }
        opps = find_triangular_opportunities(
            triangular_profitable_pools,
            min_profit_pct=0.05,
            gas_cost_pct=0.0,
        )
        assert len(opps) >= 1
        missing = REQUIRED - set(opps[0].keys())
        assert not missing, f"Missing keys in triangular result: {missing}"

    @pytest.mark.unit
    def test_each_leg_has_required_fields(self, triangular_profitable_pools):
        """Every leg in the opportunity must have: from, to, rate, fee_pct, pool, dex."""
        LEG_KEYS = {"from", "to", "rate", "fee_pct", "pool", "dex"}
        opps = find_triangular_opportunities(
            triangular_profitable_pools,
            min_profit_pct=0.05,
            gas_cost_pct=0.0,
        )
        assert len(opps) >= 1
        for leg in opps[0]["legs"]:
            missing = LEG_KEYS - set(leg.keys())
            assert not missing, f"Leg missing keys: {missing}"

    @pytest.mark.unit
    def test_empty_pool_list_returns_empty(self):
        """Edge case: empty input → empty output, no exception."""
        opps = find_triangular_opportunities([], min_profit_pct=0.05)
        assert opps == []

    @pytest.mark.unit
    def test_v2_low_reserve_skipped_in_triangular(self):
        """
        A V2 pool with WETH reserve below 0.5 ETH must be excluded from the
        graph edges. With that pool gone, the triangle cannot be completed.
        """
        # Good V3 WETH/USDC pool
        weth_usdc = _v3("0xWUGOOD", 2000.0, fee_pct=0.05, liquidity=LIQUID_V3,
                        pair="WETH/USDC",
                        token0_sym="WETH", token0_addr=WETH_ADDR,
                        token1_sym="USDC", token1_addr=USDC_ADDR)
        # Stale V2 USDC/WETH — near-zero WETH reserve
        stale_v2 = {
            "pool_address": "0xSTALE",
            "version": "V2",
            "dex": "Sushi",
            "token0": {"address": USDC_ADDR, "symbol": "USDC",
                       "decimals": 6, "reserve": 10.0},
            "token1": {"address": WETH_ADDR, "symbol": "WETH",
                       "decimals": 18, "reserve": 0.001},  # << 0.5 ETH threshold
            "price_0_in_1": 0.005,
            "price_1_in_0": 200.0,
            "fee_percentage": 0.3,
            "pair": "USDC/WETH",
        }
        # Good V3 UNI/WETH pool
        uni_weth = _v3("0xUWGOOD", 0.0128, fee_pct=0.30, liquidity=LIQUID_V3,
                       pair="UNI/WETH",
                       token0_sym="UNI",  token0_addr=UNI_ADDR,
                       token1_sym="WETH", token1_addr=WETH_ADDR)

        opps = find_triangular_opportunities(
            [weth_usdc, stale_v2, uni_weth],
            min_profit_pct=0.05,
            gas_cost_pct=0.0,
        )
        # The stale V2 pool was the only USDC→WETH edge — without it the
        # WETH→USDC→UNI→WETH cycle cannot be completed
        assert opps == [], (
            "Stale V2 pool (low WETH reserve) should be excluded from triangular graph"
        )
