"""
simulate_bundles.py — Bundle Comparison Simulator
==================================================
Fetches live mainnet pool data across multiple blocks and runs the full
arbitrage detection pipeline (two-way + triangular) for every configured
bundle.  No transactions are sent.

Usage:
    python scripts/simulate_bundles.py              # 5 blocks, all bundles
    python scripts/simulate_bundles.py --blocks 10  # 10 blocks
    python scripts/simulate_bundles.py --bundle balanced --blocks 15

Output:
    - Per-bundle summary table
    - Per-pair breakdown
    - Best opportunities found
    - Simulated P&L if every opportunity had been executed
"""

import argparse
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

import yaml
from dotenv import load_dotenv
from web3 import Web3

# ── Path setup ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
load_dotenv(ROOT / ".env")

from onchainprice import batch_get_pool_data, find_arbitrage_opportunities, get_eth_price_usd
from triangular import find_triangular_opportunities
from pair_manager import PairManager

# ── Config ───────────────────────────────────────────────────────────────────
GAS_TWO_WAY    = 400_000
GAS_TRIANGULAR = 600_000
AAVE_FEE_PCT   = 0.09   # Flash loan fee (%)
MIN_PROFIT_PCT = -1.0   # Capture near-misses too; report filters by --threshold
SEPARATOR      = "=" * 72


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def get_gas_price_gwei(w3: Web3) -> float:
    try:
        return w3.eth.gas_price / 1e9
    except Exception:
        return 20.0


def get_eth_price(w3: Web3) -> float:
    try:
        return get_eth_price_usd() or 3500.0
    except Exception:
        return 3500.0


def compute_gas_usd(gas_units: int, gas_gwei: float, eth_price: float) -> float:
    return gas_units * gas_gwei * 1e9 / 1e18 * eth_price


def get_pool_addresses_for_bundle(config: dict, bundle_name: str) -> List[str]:
    """Return pool addresses for a specific bundle by temporarily overriding active_bundle."""
    cfg_copy = yaml.safe_load(yaml.dump(config))  # deep copy
    cfg_copy["pairs"]["active_bundle"] = bundle_name
    pm = PairManager(cfg_copy)
    pm.discover_pairs()
    return pm.get_all_pool_addresses(), pm


# ── Simulation core ───────────────────────────────────────────────────────────

def simulate_bundle(
    bundle_name: str,
    config: dict,
    w3: Web3,
    n_blocks: int,
    eth_price: float,
    gas_gwei: float,
) -> dict:
    """
    Simulate N blocks of monitoring for a given bundle.
    Returns a results dict with all collected opportunities.
    """
    print(f"\n{'─'*72}")
    print(f"  Bundle: {bundle_name.upper()}")
    print(f"{'─'*72}")

    pool_addresses, pm = get_pool_addresses_for_bundle(config, bundle_name)

    if not pool_addresses:
        print(f"  [!] No pools found for bundle '{bundle_name}' — skipping")
        return {
            "bundle": bundle_name, "blocks_scanned": 0,
            "two_way_opps": [], "tri_opps": [],
            "pool_count": 0, "pair_count": 0,
        }

    pair_count = len(pm.pairs)
    print(f"  Pairs: {pair_count} | Pools: {len(pool_addresses)}")
    print(f"  Scanning {n_blocks} blocks...\n")

    gas_2way_usd = compute_gas_usd(GAS_TWO_WAY,    gas_gwei, eth_price)
    gas_tri_usd  = compute_gas_usd(GAS_TRIANGULAR,  gas_gwei, eth_price)

    all_tw_opps:  List[dict] = []
    all_tri_opps: List[dict] = []

    last_block = None

    for b in range(n_blocks):
        # Wait for a new block
        t_wait = time.monotonic()
        while True:
            try:
                current = w3.eth.block_number
            except Exception:
                current = (last_block or 0) + 1
            if current != last_block:
                break
            if time.monotonic() - t_wait > 30:
                print(f"  [!] Block timeout on iteration {b+1}")
                break
            time.sleep(0.3)

        last_block = current
        t_fetch = time.monotonic()

        try:
            pool_data = batch_get_pool_data(pool_addresses)
        except Exception as e:
            print(f"  [!] Block {current}: fetch failed — {e}")
            continue

        if not pool_data:
            print(f"  Block {current}: no pool data returned")
            continue

        # Compute per-trade gas %
        trade_usd  = config.get("execution", {}).get("trade_size_usd", 10_000)
        g2pct = (gas_2way_usd / trade_usd) * 100
        gtpct = (gas_tri_usd  / trade_usd) * 100

        # Two-way
        tw = find_arbitrage_opportunities(
            pool_data,
            min_profit_percentage=MIN_PROFIT_PCT,
            gas_cost_pct=g2pct,
            trade_size_usd=trade_usd,
            gas_cost_usd=gas_2way_usd,
        )
        for o in tw:
            o["type"] = "two_way"
            o["block"] = current
        all_tw_opps.extend(tw)

        # Triangular
        tri = find_triangular_opportunities(pool_data, MIN_PROFIT_PCT, gtpct)
        for o in tri:
            o["block"] = current
        all_tri_opps.extend(tri)

        elapsed = time.monotonic() - t_fetch
        tw_pos  = [o for o in tw  if o["net_profit_percentage"] > 0]
        tri_pos = [o for o in tri if o["net_profit_percentage"] > 0]

        print(
            f"  Block {current} ({elapsed:.2f}s) | "
            f"pools={len(pool_data)} | "
            f"2-way={len(tw_pos):>2} positive | "
            f"tri={len(tri_pos):>2} positive"
        )

    return {
        "bundle":        bundle_name,
        "blocks_scanned": n_blocks,
        "pool_count":    len(pool_addresses),
        "pair_count":    pair_count,
        "two_way_opps":  all_tw_opps,
        "tri_opps":      all_tri_opps,
        "gas_2way_usd":  gas_2way_usd,
        "gas_tri_usd":   gas_tri_usd,
        "eth_price":     eth_price,
        "gas_gwei":      gas_gwei,
    }


# ── Report generation ─────────────────────────────────────────────────────────

def _opp_profit_usd(opp: dict, fallback_trade_usd: float = 10_000) -> float:
    """Return estimated absolute profit in USD for an opportunity."""
    if "estimated_profit_usd" in opp:
        return opp["estimated_profit_usd"]
    trade = opp.get("optimal_trade_size_usd", fallback_trade_usd)
    net   = opp.get("net_profit_percentage", 0)
    return trade * net / 100


def print_bundle_report(result: dict, threshold_pct: float = 0.0):
    bundle = result["bundle"]
    tw     = result["two_way_opps"]
    tri    = result["tri_opps"]
    blocks = result["blocks_scanned"]
    trade_usd = 10_000

    # Filter to profitable above threshold
    tw_pos  = [o for o in tw  if o["net_profit_percentage"] > threshold_pct]
    tri_pos = [o for o in tri if o["net_profit_percentage"] > threshold_pct]
    all_pos = tw_pos + tri_pos

    total_opps       = len(all_pos)
    opps_per_block   = total_opps / blocks if blocks else 0

    tw_profits  = [_opp_profit_usd(o, trade_usd) for o in tw_pos]
    tri_profits = [_opp_profit_usd(o, trade_usd) for o in tri_pos]
    all_profits = tw_profits + tri_profits

    total_sim_profit  = sum(all_profits)
    avg_profit_usd    = (total_sim_profit / len(all_profits)) if all_profits else 0
    best_opp          = max(all_pos, key=lambda o: o["net_profit_percentage"]) if all_pos else None

    # Per-pair breakdown
    pair_counts:   Dict[str, int]   = defaultdict(int)
    pair_profits:  Dict[str, float] = defaultdict(float)
    for o in all_pos:
        pair = o.get("pair", "unknown")
        pair_counts[pair]  += 1
        pair_profits[pair] += _opp_profit_usd(o, trade_usd)

    print(f"\n{SEPARATOR}")
    print(f"  REPORT: {bundle.upper()} BUNDLE")
    print(SEPARATOR)
    print(f"  Blocks scanned   : {blocks}")
    print(f"  Pools monitored  : {result['pool_count']}")
    print(f"  Pairs monitored  : {result['pair_count']}")
    print(f"  Gas price        : {result['gas_gwei']:.1f} gwei")
    print(f"  ETH price        : ${result['eth_price']:,.0f}")
    print(f"  Gas (2-way)      : ${result['gas_2way_usd']:.2f}  |  Gas (tri): ${result['gas_tri_usd']:.2f}")
    print()
    print(f"  ── Opportunities (net > {threshold_pct}%) ──────────────────────────")
    print(f"  Total profitable : {total_opps}  ({opps_per_block:.1f}/block)")
    print(f"  Two-way          : {len(tw_pos)}")
    print(f"  Triangular       : {len(tri_pos)}")
    print()
    print(f"  ── Simulated P&L (if all executed) ───────────────────────────────")
    print(f"  Total profit     : ${total_sim_profit:>10.4f}")
    print(f"  Avg per trade    : ${avg_profit_usd:>10.4f}")
    print(f"  Profit/block     : ${total_sim_profit / blocks if blocks else 0:>10.4f}")

    if best_opp:
        print()
        print(f"  ── Best opportunity ──────────────────────────────────────────────")
        print(f"  Pair             : {best_opp.get('pair', '?')}")
        print(f"  Type             : {best_opp.get('type', 'triangular')}")
        print(f"  Net profit       : {best_opp['net_profit_percentage']:.4f}%")
        print(f"  Optimal size     : ${best_opp.get('optimal_trade_size_usd', trade_usd):,.0f}")
        print(f"  Est. profit      : ${_opp_profit_usd(best_opp, trade_usd):.4f}")
        if best_opp.get("type") == "two_way":
            print(f"  Buy on           : {best_opp.get('buy_pool',  {}).get('dex', '?')}")
            print(f"  Sell on          : {best_opp.get('sell_pool', {}).get('dex', '?')}")

    if pair_counts:
        print()
        print(f"  ── Per-pair breakdown ────────────────────────────────────────────")
        print(f"  {'Pair':<22} {'Opps':>5}  {'Sim Profit':>12}")
        print(f"  {'─'*22} {'─'*5}  {'─'*12}")
        for pair, count in sorted(pair_counts.items(), key=lambda x: -pair_profits[x[0]]):
            print(f"  {pair:<22} {count:>5}  ${pair_profits[pair]:>11.4f}")

    # Near-miss section — show best below-threshold opps (closest to profitable)
    all_opps = result["two_way_opps"] + result["tri_opps"]
    near_miss = [o for o in all_opps if o["net_profit_percentage"] <= threshold_pct]
    if near_miss:
        near_miss.sort(key=lambda o: -o["net_profit_percentage"])
        print()
        print(f"  ── Near-misses (best below {threshold_pct}% threshold) ─────────────────")
        print(f"  {'Pair':<22} {'Type':<10} {'Net%':>8}  {'DEX route'}")
        print(f"  {'─'*22} {'─'*10} {'─'*8}  {'─'*20}")
        for o in near_miss[:8]:
            t    = o.get("type", "triangular")
            pair = o.get("pair", "?")
            net  = o["net_profit_percentage"]
            if t == "two_way":
                route = f"{o.get('buy_pool',{}).get('dex','?')} → {o.get('sell_pool',{}).get('dex','?')}"
            else:
                legs  = o.get("legs", [])
                route = " → ".join(l.get("dex","?") for l in legs[:3])
            print(f"  {pair:<22} {t:<10} {net:>8.4f}%  {route}")

    print(SEPARATOR)


def print_comparison_table(results: List[dict], threshold_pct: float = 0.0):
    trade_usd = 10_000

    print(f"\n{SEPARATOR}")
    print("  BUNDLE COMPARISON SUMMARY")
    print(SEPARATOR)
    print(f"  {'Bundle':<16} {'Pools':>5} {'Pairs':>5} {'Opps':>6} "
          f"{'Opps/blk':>9} {'2-way':>6} {'Tri':>5} "
          f"{'Sim Profit':>11} {'$/block':>9} {'Best%':>7}")
    print(f"  {'─'*16} {'─'*5} {'─'*5} {'─'*6} {'─'*9} {'─'*6} {'─'*5} "
          f"{'─'*11} {'─'*9} {'─'*7}")

    ranked = []
    for r in results:
        tw_pos  = [o for o in r["two_way_opps"] if o["net_profit_percentage"] > threshold_pct]
        tri_pos = [o for o in r["tri_opps"]     if o["net_profit_percentage"] > threshold_pct]
        all_pos = tw_pos + tri_pos
        total_p = sum(_opp_profit_usd(o, trade_usd) for o in all_pos)
        best_p  = max((o["net_profit_percentage"] for o in all_pos), default=0.0)
        blocks  = r["blocks_scanned"] or 1
        ranked.append((r, len(all_pos), total_p, best_p))

    ranked.sort(key=lambda x: -x[2])  # sort by total simulated profit

    for r, n_opps, total_p, best_p in ranked:
        tw_pos  = [o for o in r["two_way_opps"] if o["net_profit_percentage"] > threshold_pct]
        tri_pos = [o for o in r["tri_opps"]     if o["net_profit_percentage"] > threshold_pct]
        blocks  = r["blocks_scanned"] or 1
        print(
            f"  {r['bundle']:<16} {r['pool_count']:>5} {r['pair_count']:>5} "
            f"{n_opps:>6} {n_opps/blocks:>9.1f} "
            f"{len(tw_pos):>6} {len(tri_pos):>5} "
            f"${total_p:>10.4f} ${total_p/blocks:>8.4f} "
            f"{best_p:>6.3f}%"
        )

    print(SEPARATOR)

    # Winner analysis
    if ranked:
        best_profit_bundle = ranked[0][0]["bundle"]
        best_opp_bundle    = max(ranked, key=lambda x: x[3])[0]["bundle"]
        print(f"\n  Most profitable overall : {best_profit_bundle.upper()}")
        print(f"  Best single opportunity : {best_opp_bundle.upper()}")
        print()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Bundle comparison simulator")
    parser.add_argument("--blocks",  type=int, default=5,    help="Blocks to scan per bundle")
    parser.add_argument("--bundle",  type=str, default=None, help="Simulate one bundle only")
    parser.add_argument("--threshold", type=float, default=0.0,
                        help="Min net profit %% to count as opportunity (default 0)")
    args = parser.parse_args()

    config   = load_config()
    bundles  = config["pairs"].get("bundles", {})
    rpc_url  = os.getenv("RPC_URL", "").replace("wss://", "https://").replace("ws://", "http://")

    if not rpc_url:
        print("[ERROR] RPC_URL not set in .env")
        sys.exit(1)

    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
    if not w3.is_connected():
        print(f"[ERROR] Cannot connect to RPC: {rpc_url[:60]}...")
        sys.exit(1)

    gas_gwei  = get_gas_price_gwei(w3)
    eth_price = get_eth_price(w3)

    print(SEPARATOR)
    print("  ARBITRAGE BUNDLE SIMULATOR")
    print(SEPARATOR)
    print(f"  RPC              : {rpc_url.split('/')[2]}")
    print(f"  Current block    : {w3.eth.block_number:,}")
    print(f"  ETH price        : ${eth_price:,.0f}")
    print(f"  Gas price        : {gas_gwei:.1f} gwei")
    print(f"  Blocks per bundle: {args.blocks}")
    print(f"  Threshold        : {args.threshold}% net profit")

    target_bundles = [args.bundle] if args.bundle else list(bundles.keys())
    print(f"  Bundles          : {', '.join(target_bundles)}")
    print(SEPARATOR)

    results = []
    for bundle_name in target_bundles:
        if bundle_name not in bundles:
            print(f"\n[!] Unknown bundle '{bundle_name}'. Available: {list(bundles.keys())}")
            continue
        result = simulate_bundle(
            bundle_name, config, w3, args.blocks, eth_price, gas_gwei
        )
        results.append(result)
        print_bundle_report(result, threshold_pct=args.threshold)

    if len(results) > 1:
        print_comparison_table(results, threshold_pct=args.threshold)

    print("\nSimulation complete.\n")


if __name__ == "__main__":
    main()
