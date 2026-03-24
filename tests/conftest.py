"""
conftest.py — Shared pytest fixtures for the arbitrage bot test suite.

All fixtures here are pure mock data — no network access required.
The pool dicts mirror the exact structure produced by batch_get_pool_data()
as documented in AGENT_CONTEXT.txt and verified in scripts/onchainprice.py.
"""

import os
import sys
import copy

import pytest

# ---------------------------------------------------------------------------
# Exclude pre-existing scripts-as-tests that rely on removed symbols
# (test_monitoring.py and test_price_changes.py import POPULAR_POOLS which no
# longer exists in onchainprice.py — they are run scripts, not pytest modules)
# ---------------------------------------------------------------------------

collect_ignore = [
    "test_monitoring.py",
    "test_price_changes.py",
]

# Make scripts/ importable from tests/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


# ---------------------------------------------------------------------------
# Token address constants (real mainnet checksummed addresses)
# ---------------------------------------------------------------------------

WETH_ADDR  = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC_ADDR  = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
WBTC_ADDR  = "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599"
UNI_ADDR   = "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"
DAI_ADDR   = "0x6B175474E89094C44Da98b954EedeAC495271d0F"


# ---------------------------------------------------------------------------
# Base pool factories
# ---------------------------------------------------------------------------

def _make_v3_pool(
    pair: str,
    dex: str,
    pool_address: str,
    token0_symbol: str,
    token0_address: str,
    token0_decimals: int,
    token1_symbol: str,
    token1_address: str,
    token1_decimals: int,
    price_0_in_1: float,
    fee_pct: float = 0.05,
    liquidity: int = 5_000_000,
) -> dict:
    return {
        "pool_address": pool_address,
        "version": "V3",
        "dex": dex,
        "token0": {
            "address": token0_address,
            "symbol": token0_symbol,
            "decimals": token0_decimals,
            "reserve": 0,   # V3 pools don't use reserve
        },
        "token1": {
            "address": token1_address,
            "symbol": token1_symbol,
            "decimals": token1_decimals,
            "reserve": 0,
        },
        "price_0_in_1": price_0_in_1,
        "price_1_in_0": 1.0 / price_0_in_1 if price_0_in_1 else 0.0,
        "fee_percentage": fee_pct,
        "fee_tier": int(fee_pct * 10_000),
        "liquidity": liquidity,
        "pair": pair,
    }


def _make_v2_pool(
    pair: str,
    dex: str,
    pool_address: str,
    token0_symbol: str,
    token0_address: str,
    token0_decimals: int,
    token0_reserve: float,
    token1_symbol: str,
    token1_address: str,
    token1_decimals: int,
    token1_reserve: float,
    fee_pct: float = 0.3,
) -> dict:
    price_0_in_1 = token1_reserve / token0_reserve if token0_reserve else 0.0
    price_1_in_0 = token0_reserve / token1_reserve if token1_reserve else 0.0
    return {
        "pool_address": pool_address,
        "version": "V2",
        "dex": dex,
        "token0": {
            "address": token0_address,
            "symbol": token0_symbol,
            "decimals": token0_decimals,
            "reserve": token0_reserve,
        },
        "token1": {
            "address": token1_address,
            "symbol": token1_symbol,
            "decimals": token1_decimals,
            "reserve": token1_reserve,
        },
        "price_0_in_1": price_0_in_1,
        "price_1_in_0": price_1_in_0,
        "fee_percentage": fee_pct,
        "fee_tier": int(fee_pct * 10_000),
        "pair": pair,
    }


# ---------------------------------------------------------------------------
# Realistic V3 liquidity constant
# calculate_optimal_trade_size uses liquidity/1e6 as a TVL proxy.
# At 1e14, tvl_estimate=1e8, optimal=min(1e8*0.01, 50000)=$50k,
# price_impact=10000/(1e8*0.01)=0.1% — well below the 2% cap.
# ---------------------------------------------------------------------------

LIQUID_V3 = int(1e14)

# ---------------------------------------------------------------------------
# Named fixtures: individual canonical pools
# ---------------------------------------------------------------------------

@pytest.fixture
def pool_v3_weth_usdc_univ3():
    """Uniswap V3 0.05% WETH/USDC — liquid, price 2000 USDC per WETH."""
    return _make_v3_pool(
        pair="WETH/USDC",
        dex="UniV3",
        pool_address="0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640",
        token0_symbol="WETH", token0_address=WETH_ADDR, token0_decimals=18,
        token1_symbol="USDC", token1_address=USDC_ADDR, token1_decimals=6,
        price_0_in_1=2000.0,
        fee_pct=0.05,
        liquidity=LIQUID_V3,
    )


@pytest.fixture
def pool_v3_weth_usdc_expensive():
    """Uniswap V3 0.30% WETH/USDC — liquid, price 2020 USDC per WETH (1% spread)."""
    return _make_v3_pool(
        pair="WETH/USDC",
        dex="UniV3",
        pool_address="0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8",
        token0_symbol="WETH", token0_address=WETH_ADDR, token0_decimals=18,
        token1_symbol="USDC", token1_address=USDC_ADDR, token1_decimals=6,
        price_0_in_1=2020.0,
        fee_pct=0.30,
        liquidity=LIQUID_V3,
    )


@pytest.fixture
def pool_v2_weth_usdc_sushi():
    """SushiSwap V2 0.30% WETH/USDC — price 2020 USDC per WETH (sufficient spread)."""
    return _make_v2_pool(
        pair="WETH/USDC",
        dex="Sushi",
        pool_address="0x397FF1542f962076d0BFE58eA045FfA2d347ACa0",
        token0_symbol="WETH", token0_address=WETH_ADDR, token0_decimals=18,
        token0_reserve=500.0,          # 500 WETH >> 0.5 ETH threshold
        token1_symbol="USDC", token1_address=USDC_ADDR, token1_decimals=6,
        token1_reserve=1_010_000.0,    # 1 010 000 USDC → price 2020
        fee_pct=0.3,
    )


@pytest.fixture
def pool_v3_weth_usdc_illiquid():
    """Uniswap V3 WETH/USDC pool with liquidity=0 — should be filtered out."""
    return _make_v3_pool(
        pair="WETH/USDC",
        dex="UniV3",
        pool_address="0xDEAD0000000000000000000000000000000DEAD0",
        token0_symbol="WETH", token0_address=WETH_ADDR, token0_decimals=18,
        token1_symbol="USDC", token1_address=USDC_ADDR, token1_decimals=6,
        price_0_in_1=2010.0,
        fee_pct=0.05,
        liquidity=0,    # <-- illiquid
    )


@pytest.fixture
def pool_v2_weth_usdc_low_reserve():
    """V2 WETH/USDC pool with only 0.01 WETH reserve — below MIN_WETH_RESERVE (0.5)."""
    return _make_v2_pool(
        pair="WETH/USDC",
        dex="UniV2",
        pool_address="0xDEAD0000000000000000000000000000000DEAD1",
        token0_symbol="WETH", token0_address=WETH_ADDR, token0_decimals=18,
        token0_reserve=0.01,   # << 0.5 ETH threshold
        token1_symbol="USDC", token1_address=USDC_ADDR, token1_decimals=6,
        token1_reserve=20.0,
        fee_pct=0.3,
    )


# ---------------------------------------------------------------------------
# Composite fixtures: pool lists used in multi-pool scenarios
# ---------------------------------------------------------------------------

@pytest.fixture
def two_pool_arb_scenario(pool_v3_weth_usdc_univ3, pool_v3_weth_usdc_expensive):
    """
    Two liquid V3 WETH/USDC pools with a 1% gross spread (2000 vs 2020 USDC).
    Combined fees = 0.05 + 0.30 = 0.35%.
    Slippage ≈ 0.10%. Net profit ≈ 0.55% → should be detected.
    """
    return [pool_v3_weth_usdc_univ3, pool_v3_weth_usdc_expensive]


@pytest.fixture
def two_pool_tight_spread(pool_v3_weth_usdc_univ3):
    """
    Two pools with only 0.2% gross spread — below combined fees of 0.35%.
    Should produce NO opportunity.
    """
    cheap = pool_v3_weth_usdc_univ3                     # 2000 USDC
    expensive = _make_v3_pool(
        pair="WETH/USDC",
        dex="UniV3",
        pool_address="0xTIGHTSPREAD000000000000000000000000000001",
        token0_symbol="WETH", token0_address=WETH_ADDR, token0_decimals=18,
        token1_symbol="USDC", token1_address=USDC_ADDR, token1_decimals=6,
        price_0_in_1=2004.0,    # 0.2% spread vs 2000
        fee_pct=0.3,
        liquidity=LIQUID_V3,
    )
    return [cheap, expensive]


@pytest.fixture
def two_pool_outlier_spread(pool_v3_weth_usdc_univ3):
    """
    Two pools with a 60% gross spread — beyond MAX_GROSS_SPREAD=50%, a data artefact.
    Should be rejected.
    """
    cheap = pool_v3_weth_usdc_univ3                     # 2000 USDC
    outlier = _make_v3_pool(
        pair="WETH/USDC",
        dex="UniV3",
        pool_address="0xOUTLIER000000000000000000000000000OUTLIER",
        token0_symbol="WETH", token0_address=WETH_ADDR, token0_decimals=18,
        token1_symbol="USDC", token1_address=USDC_ADDR, token1_decimals=6,
        price_0_in_1=3200.0,    # 60% above 2000 → outlier
        fee_pct=0.3,
        liquidity=LIQUID_V3,
    )
    return [cheap, outlier]


# ---------------------------------------------------------------------------
# Triangular-arbitrage fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def triangular_profitable_pools():
    """
    A→B→C→A triangle with a product > 1 after fees.

    Tokens: WETH (A), USDC (B), UNI (C)
    Rates (fee-inclusive):
        WETH→USDC : 2020 * 0.997 ≈ 2013.94
        USDC→UNI  : 0.04 * 0.997 ≈ 0.03988  (UNI price ~25 USDC)
        UNI→WETH  : 0.0128 * 0.997 ≈ 0.012774 (WETH price ~78 UNI)

    Product ≈ 2013.94 * 0.03988 * 0.012774 ≈ 1.0262 → gross ≈ 2.62% > min.
    """
    # WETH/USDC pool (WETH→USDC direction: price_0_in_1 = USDC per WETH)
    weth_usdc = _make_v3_pool(
        pair="WETH/USDC",
        dex="UniV3",
        pool_address="0xWETHUSDC000000000000000000000000000000001",
        token0_symbol="WETH", token0_address=WETH_ADDR, token0_decimals=18,
        token1_symbol="USDC", token1_address=USDC_ADDR, token1_decimals=6,
        price_0_in_1=2020.0,   # USDC per WETH
        fee_pct=0.3,
        liquidity=LIQUID_V3,
    )
    # USDC/UNI pool — price_0_in_1 = UNI per USDC
    # 1 USDC = 0.04 UNI (UNI costs 25 USDC)
    usdc_uni = _make_v3_pool(
        pair="USDC/UNI",
        dex="UniV3",
        pool_address="0xUSDCUNI0000000000000000000000000000000001",
        token0_symbol="USDC", token0_address=USDC_ADDR, token0_decimals=6,
        token1_symbol="UNI",  token1_address=UNI_ADDR,  token1_decimals=18,
        price_0_in_1=0.04,    # UNI per USDC
        fee_pct=0.3,
        liquidity=LIQUID_V3,
    )
    # UNI/WETH pool — price_0_in_1 = WETH per UNI
    # 1 UNI = 0.0128 WETH (UNI ~$25.6 at $2000/ETH → 25.6/2000 = 0.0128)
    uni_weth = _make_v3_pool(
        pair="UNI/WETH",
        dex="UniV3",
        pool_address="0xUNIWETH000000000000000000000000000000001",
        token0_symbol="UNI",  token0_address=UNI_ADDR,  token0_decimals=18,
        token1_symbol="WETH", token1_address=WETH_ADDR, token1_decimals=18,
        price_0_in_1=0.0128,  # WETH per UNI
        fee_pct=0.3,
        liquidity=LIQUID_V3,
    )
    return [weth_usdc, usdc_uni, uni_weth]


@pytest.fixture
def triangular_unprofitable_pools():
    """
    Same topology as above but with rates that do NOT form a profitable cycle.
    product after fees < 1.0.

    WETH→USDC: 2000 * 0.997 = 1994
    USDC→UNI:  0.04 * 0.997 = 0.03988
    UNI→WETH:  0.0120 * 0.997 = 0.011964 (slightly lower UNI price)

    Product ≈ 1994 * 0.03988 * 0.011964 ≈ 0.9511 — unprofitable.
    """
    weth_usdc = _make_v3_pool(
        pair="WETH/USDC", dex="UniV3",
        pool_address="0xWETHUSDC000000000000000000000000000000002",
        token0_symbol="WETH", token0_address=WETH_ADDR, token0_decimals=18,
        token1_symbol="USDC", token1_address=USDC_ADDR, token1_decimals=6,
        price_0_in_1=2000.0, fee_pct=0.3, liquidity=LIQUID_V3,
    )
    usdc_uni = _make_v3_pool(
        pair="USDC/UNI", dex="UniV3",
        pool_address="0xUSDCUNI0000000000000000000000000000000002",
        token0_symbol="USDC", token0_address=USDC_ADDR, token0_decimals=6,
        token1_symbol="UNI",  token1_address=UNI_ADDR,  token1_decimals=18,
        price_0_in_1=0.04, fee_pct=0.3, liquidity=LIQUID_V3,
    )
    uni_weth = _make_v3_pool(
        pair="UNI/WETH", dex="UniV3",
        pool_address="0xUNIWETH000000000000000000000000000000002",
        token0_symbol="UNI",  token0_address=UNI_ADDR,  token0_decimals=18,
        token1_symbol="WETH", token1_address=WETH_ADDR, token1_decimals=18,
        price_0_in_1=0.0120, fee_pct=0.3, liquidity=LIQUID_V3,
    )
    return [weth_usdc, usdc_uni, uni_weth]


# ---------------------------------------------------------------------------
# Executor config fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def executor_config():
    """Minimal config dict that Executor.__init__() accepts."""
    return {
        "execution": {
            "dry_run": True,
            "max_concurrent": 3,
            "min_profit_pct": 0.05,
        }
    }


@pytest.fixture
def sample_opportunity(pool_v3_weth_usdc_univ3, pool_v3_weth_usdc_expensive):
    """A minimal valid opportunity dict as produced by find_arbitrage_opportunities()."""
    return {
        "pair": "WETH/USDC",
        "type": "two_way",
        "buy_pool":  pool_v3_weth_usdc_univ3,
        "sell_pool": pool_v3_weth_usdc_expensive,
        "gross_profit_percentage": 1.0,
        "total_fees_percentage":   0.35,
        "slippage_pct":            0.10,
        "gas_cost_pct":            0.0,
        "net_profit_percentage":   0.55,
        "optimal_trade_size_usd":  50000.0,
        "estimated_profit_usd":    275.0,
    }
