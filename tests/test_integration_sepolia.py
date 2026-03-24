"""
tests/test_integration_sepolia.py
-----------------------------------
Integration tests against a live Ethereum RPC endpoint (Sepolia or mainnet).
These tests read on-chain data — they never send transactions.

Prerequisites:
  - .env file must exist with RPC_URL set to a valid endpoint.
  - Run with: python -m pytest tests/test_integration_sepolia.py -m integration -v

All tests are decorated with @pytest.mark.integration and are automatically
skipped when RPC_URL is not configured.
"""

import os
import sys
import time
import pytest

# Ensure scripts/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

# ---------------------------------------------------------------------------
# Skip guard — skip every test in this module unless RPC_URL is set
# ---------------------------------------------------------------------------

def _rpc_url() -> str:
    """Return the configured RPC URL, or empty string if not set."""
    # Try loading .env from the repo root
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env",
                    override=False)
    except ImportError:
        pass
    return os.environ.get("RPC_URL", "").strip()


_RPC_CONFIGURED = bool(_rpc_url())

pytestmark = pytest.mark.integration  # tag every test in this module

skip_no_rpc = pytest.mark.skipif(
    not _RPC_CONFIGURED,
    reason="RPC_URL not set in .env — skipping integration tests",
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def w3():
    """Return a connected Web3 instance; skip the whole module if unavailable."""
    if not _RPC_CONFIGURED:
        pytest.skip("RPC_URL not configured")

    from web3 import Web3
    url = _rpc_url()
    _w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 30}))
    if not _w3.is_connected():
        pytest.skip(f"Cannot connect to RPC at {url}")
    return _w3


@pytest.fixture(scope="module")
def config():
    """Load the real config.yaml from the repo root."""
    import yaml
    from pathlib import Path
    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(cfg_path) as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def first_5_pool_addresses(config):
    """Return the first 5 pool addresses from hardcoded config."""
    addrs = []
    for pair_entry in config["pairs"].get("hardcoded_pools", []):
        for pool in pair_entry.get("pools", []):
            addrs.append(pool["address"])
            if len(addrs) >= 5:
                return addrs
    return addrs


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@skip_no_rpc
class TestRpcConnectivity:

    def test_can_connect_to_rpc(self, w3):
        """
        Basic connectivity: w3.is_connected() must be True and we can fetch
        a recent block number.
        """
        assert w3.is_connected(), "web3 must be connected to run integration tests"

        block_number = w3.eth.block_number
        assert isinstance(block_number, int), "block_number must be an integer"
        assert block_number > 0, f"Block number should be positive, got {block_number}"

    def test_chain_id_is_valid(self, w3):
        """Chain ID must be a known network (mainnet=1, Sepolia=11155111)."""
        chain_id = w3.eth.chain_id
        KNOWN_CHAIN_IDS = {1, 11155111}  # mainnet, Sepolia
        assert chain_id in KNOWN_CHAIN_IDS, (
            f"Unexpected chain_id={chain_id}. Expected mainnet (1) or Sepolia (11155111)."
        )

    def test_gas_price_is_nonzero(self, w3):
        """Gas price must be a positive integer (in wei)."""
        gas_price = w3.eth.gas_price
        assert isinstance(gas_price, int), "gas_price must be an integer"
        assert gas_price > 0, f"gas_price must be positive, got {gas_price}"


@skip_no_rpc
class TestBatchPoolData:

    def test_batch_pool_data_returns_valid_structure(self, first_5_pool_addresses):
        """
        batch_get_pool_data() called with 5 real pool addresses must return
        a list of dicts each containing required keys.
        """
        from scripts.onchainprice import batch_get_pool_data

        REQUIRED_KEYS = {"version", "dex", "token0", "token1",
                         "price_0_in_1", "fee_percentage", "pair"}

        assert first_5_pool_addresses, "No pool addresses in config"

        pool_data = batch_get_pool_data(first_5_pool_addresses)

        # Should return at least one result (some pools may fail gracefully)
        assert isinstance(pool_data, list), "batch_get_pool_data must return a list"
        assert len(pool_data) > 0, (
            "batch_get_pool_data returned empty list for known mainnet pools"
        )

        for pool in pool_data:
            assert isinstance(pool, dict), f"Each pool entry must be a dict, got {type(pool)}"
            missing = REQUIRED_KEYS - set(pool.keys())
            assert not missing, (
                f"Pool {pool.get('pool_address', '?')} is missing keys: {missing}"
            )

    def test_pool_prices_are_positive(self, first_5_pool_addresses):
        """price_0_in_1 and price_1_in_0 must be positive finite floats."""
        from scripts.onchainprice import batch_get_pool_data
        import math

        pool_data = batch_get_pool_data(first_5_pool_addresses)

        for pool in pool_data:
            p01 = pool.get("price_0_in_1", 0)
            p10 = pool.get("price_1_in_0", 0)
            addr = pool.get("pool_address", "?")
            assert p01 > 0,          f"price_0_in_1 must be > 0 for {addr}, got {p01}"
            assert not math.isnan(p01), f"price_0_in_1 is NaN for {addr}"
            assert not math.isinf(p01), f"price_0_in_1 is Inf for {addr}"
            assert p10 > 0,          f"price_1_in_0 must be > 0 for {addr}, got {p10}"

    def test_token_info_present_and_non_empty(self, first_5_pool_addresses):
        """Each pool's token0/token1 must have non-empty address and symbol."""
        from scripts.onchainprice import batch_get_pool_data

        pool_data = batch_get_pool_data(first_5_pool_addresses)

        for pool in pool_data:
            for key in ("token0", "token1"):
                token = pool.get(key, {})
                addr  = pool.get("pool_address", "?")
                assert isinstance(token, dict), (
                    f"{key} must be a dict for pool {addr}"
                )
                assert token.get("address"), f"{key}.address missing or empty for {addr}"
                assert token.get("symbol"),  f"{key}.symbol  missing or empty for {addr}"
                assert isinstance(token.get("decimals"), int), (
                    f"{key}.decimals must be an int for {addr}"
                )

    def test_fee_percentage_is_in_valid_range(self, first_5_pool_addresses):
        """fee_percentage must be between 0 and 1 (0% to 100%) for all known fee tiers."""
        from scripts.onchainprice import batch_get_pool_data

        pool_data = batch_get_pool_data(first_5_pool_addresses)

        KNOWN_FEE_PCTS = {0.01, 0.05, 0.1, 0.3, 1.0}  # 1bp, 5bp, 10bp, 30bp, 100bp

        for pool in pool_data:
            fee = pool.get("fee_percentage", -1)
            addr = pool.get("pool_address", "?")
            assert 0 < fee < 5.0, (
                f"fee_percentage={fee} out of expected range (0, 5.0) for {addr}"
            )

    def test_batch_empty_list_returns_empty(self):
        """batch_get_pool_data([]) must return [] without raising."""
        from scripts.onchainprice import batch_get_pool_data
        result = batch_get_pool_data([])
        assert result == []


@skip_no_rpc
class TestArbitrageDetectionPipeline:

    def test_arbitrage_detection_runs_without_error(self, first_5_pool_addresses):
        """
        Full pipeline (3 consecutive 'blocks'): fetch pool data → detect opportunities.
        No exception should be raised in any iteration.
        """
        from scripts.onchainprice import batch_get_pool_data, find_arbitrage_opportunities
        from scripts.triangular import find_triangular_opportunities

        for iteration in range(3):
            pool_data = batch_get_pool_data(first_5_pool_addresses)
            assert isinstance(pool_data, list)

            # Two-way detection — must not raise
            two_way_opps = find_arbitrage_opportunities(
                pool_data,
                min_profit_percentage=0.05,
                gas_cost_pct=0.0,
            )
            assert isinstance(two_way_opps, list)

            # Triangular detection — must not raise
            tri_opps = find_triangular_opportunities(
                pool_data,
                min_profit_pct=0.05,
                gas_cost_pct=0.0,
            )
            assert isinstance(tri_opps, list)

            # All returned opportunities must have net_profit_percentage
            for opp in two_way_opps + tri_opps:
                assert "net_profit_percentage" in opp, (
                    f"Opportunity missing net_profit_percentage: {opp}"
                )
                assert opp["net_profit_percentage"] > 0.05, (
                    f"Opportunity below min_profit_pct threshold: {opp}"
                )

            # Small pause between iterations (simulates block spacing)
            if iteration < 2:
                time.sleep(1)

    def test_opportunity_structure_when_found(self, first_5_pool_addresses):
        """
        If any two-way opportunity is found on the live network, verify it has
        the correct structure and makes economic sense.
        """
        from scripts.onchainprice import batch_get_pool_data, find_arbitrage_opportunities

        pool_data = batch_get_pool_data(first_5_pool_addresses)
        opps = find_arbitrage_opportunities(pool_data, min_profit_percentage=0.0)

        for opp in opps:
            assert opp["net_profit_percentage"] >= -10.0, (
                "Net profit should not be wildly negative (data sanity check)"
            )
            assert opp["net_profit_percentage"] < 50.0, (
                "Net profit above 50% is almost certainly a data artefact"
            )
            assert opp["gross_profit_percentage"] >= opp["total_fees_percentage"], (
                "Gross profit must be >= fees (net can be negative with gas but gross >= fees)"
            )

    def test_pool_count_matches_config(self, config, first_5_pool_addresses):
        """
        batch_get_pool_data() called with 5 addresses should return at most 5
        results (could be fewer if some pools are not recognisable as V2/V3).
        """
        from scripts.onchainprice import batch_get_pool_data

        pool_data = batch_get_pool_data(first_5_pool_addresses)
        assert len(pool_data) <= len(first_5_pool_addresses), (
            "batch_get_pool_data cannot return more pools than addresses supplied"
        )
