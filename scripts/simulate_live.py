#!/usr/bin/env python3
"""
simulate_live.py — 10-minute live opportunity detection + full analysis report
===============================================================================
Connects to mainnet via RPC_URL, runs the complete monitoring pipeline for
DURATION_S seconds, records every opportunity that would have been executed,
and writes a detailed JSON + human-readable report.

No transactions are ever sent — this is a 100% read-only simulation.

Usage
-----
    python3 scripts/simulate_live.py
    python3 scripts/simulate_live.py --duration 600   # 10 min (default)
    python3 scripts/simulate_live.py --bundle all     # override bundle
    make simulate

Output files
------------
    simulation_report.json      — machine-readable full dump
    simulation_summary.txt      — human-readable summary
"""

import argparse
import json
import os
import signal
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env", override=False)

sys.path.insert(0, str(Path(__file__).parent))

import yaml
from web3 import Web3

# ── Lazy imports from the bot itself ──────────────────────────────────────────
from onchainprice import batch_get_pool_data, find_arbitrage_opportunities
from triangular import find_triangular_opportunities
from pair_manager import PairManager

# ── Constants (mirrors bot.py) ────────────────────────────────────────────────
GAS_TWO_WAY    = 400_000   # gas units
GAS_TRIANGULAR = 600_000   # gas units
AAVE_FEE_PCT   = 0.09      # % (Aave V3 flash loan premium)
MIN_LOG_PROFIT = 0.01      # % — capture everything above this threshold
POLL_INTERVAL  = 1.0       # seconds between block-number polls

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def _c(text, colour): return f"{colour}{text}{RESET}"

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_config(path: Optional[str] = None) -> dict:
    if path is None:
        path = os.environ.get("BOT_CONFIG")
    if path is None:
        path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(path) as fh:
        return yaml.safe_load(fh)


def _connect_web3(rpc_url: str) -> Web3:
    url = rpc_url.replace("wss://", "https://").replace("ws://", "http://")
    w3  = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 15}))
    if not w3.is_connected():
        raise RuntimeError(f"Cannot connect to RPC: {url}")
    return w3


def _extract_eth_price(pool_data: list) -> float:
    """Pull ETH/USD from a WETH/stablecoin pool in the fresh pool snapshot."""
    WETH_SYMS   = {"WETH", "ETH"}
    STABLE_SYMS = {"USDC", "USDT", "DAI"}
    for pool in pool_data:
        t0, t1 = pool.get("token0", {}), pool.get("token1", {})
        s0, s1 = t0.get("symbol", ""), t1.get("symbol", "")
        p01, p10 = pool.get("price_0_in_1", 0.0) or 0.0, pool.get("price_1_in_0", 0.0) or 0.0
        if s0 in WETH_SYMS and s1 in STABLE_SYMS and p01 > 100:
            return p01
        if s1 in WETH_SYMS and s0 in STABLE_SYMS and p10 > 100:
            return p10
    return 0.0


def _gas_cost_usd(gas_units: int, gas_gwei: float, eth_usd: float) -> float:
    return gas_units * gas_gwei * 1e-9 * eth_usd


def _progress_bar(elapsed: float, total: float, width: int = 40) -> str:
    frac   = min(elapsed / total, 1.0)
    filled = int(frac * width)
    bar    = "█" * filled + "░" * (width - filled)
    pct    = frac * 100
    return f"[{bar}] {pct:5.1f}%"


# ─────────────────────────────────────────────────────────────────────────────
# Simulation runner
# ─────────────────────────────────────────────────────────────────────────────

class SimulationRunner:

    def __init__(self, w3: Web3, pool_addresses: List[str],
                 trade_size_usd: float, duration_s: int):
        self.w3              = w3
        self.pool_addresses  = pool_addresses
        self.trade_size_usd  = trade_size_usd
        self.duration_s      = duration_s

        # Accumulators
        self.blocks_seen: List[int]       = []
        self.fetch_times: List[float]     = []
        self.gas_prices:  List[float]     = []   # gwei
        self.eth_prices:  List[float]     = []   # USD
        self.all_opps:    List[dict]      = []

        self._stop = False
        signal.signal(signal.SIGINT,  lambda *_: self._set_stop())
        signal.signal(signal.SIGTERM, lambda *_: self._set_stop())

    def _set_stop(self):
        print(f"\n{_c('Interrupted — generating report…', YELLOW)}")
        self._stop = True

    # ── Main loop ──────────────────────────────────────────────────────────

    def run(self) -> dict:
        start_time  = time.time()
        last_block  = 0
        iteration   = 0
        total_pools = len(self.pool_addresses)

        print()
        print(_c("=" * 70, BOLD))
        print(_c("  ARBITRAGE BOT — 10-MINUTE LIVE SIMULATION", BOLD))
        print(_c("=" * 70, BOLD))
        print(f"  Pools monitored  : {total_pools}")
        print(f"  Duration         : {self.duration_s}s ({self.duration_s // 60}m {self.duration_s % 60}s)")
        print(f"  Min profit shown : {MIN_LOG_PROFIT}%")
        print(f"  Reference size   : ${self.trade_size_usd:,.0f}")
        print(f"  Started          : {datetime.now().strftime('%H:%M:%S')}")
        print(_c("=" * 70, BOLD))
        print()

        while not self._stop:
            elapsed = time.time() - start_time
            if elapsed >= self.duration_s:
                break

            # ── Wait for a new block ───────────────────────────────────────
            try:
                current_block = self.w3.eth.block_number
            except Exception as e:
                print(_c(f"  [RPC error] {e} — retrying…", RED))
                time.sleep(2)
                continue

            if current_block <= last_block:
                time.sleep(POLL_INTERVAL)
                continue

            last_block  = current_block
            iteration  += 1
            t_fetch     = time.monotonic()

            # ── Fetch pool data ────────────────────────────────────────────
            try:
                pool_data = batch_get_pool_data(self.pool_addresses)
            except Exception as e:
                print(_c(f"  [batch error] {e}", RED))
                continue

            fetch_elapsed = time.monotonic() - t_fetch
            self.fetch_times.append(fetch_elapsed)

            # ── Gas & ETH price ────────────────────────────────────────────
            try:
                gas_gwei = self.w3.eth.gas_price / 1e9
            except Exception:
                gas_gwei = 1.0

            eth_usd = _extract_eth_price(pool_data) or 3500.0
            self.gas_prices.append(gas_gwei)
            self.eth_prices.append(eth_usd)
            self.blocks_seen.append(current_block)

            g2_usd = _gas_cost_usd(GAS_TWO_WAY,    gas_gwei, eth_usd)
            gt_usd = _gas_cost_usd(GAS_TRIANGULAR,  gas_gwei, eth_usd)
            g2_pct = (g2_usd / self.trade_size_usd) * 100
            gt_pct = (gt_usd / self.trade_size_usd) * 100

            # ── Detection ─────────────────────────────────────────────────
            tw_opps = find_arbitrage_opportunities(
                pool_data,
                min_profit_percentage=MIN_LOG_PROFIT,
                gas_cost_pct=g2_pct,
                trade_size_usd=self.trade_size_usd,
                gas_cost_usd=g2_usd,
            )
            for o in tw_opps:
                o["type"]      = "two_way"
                o["block"]     = current_block
                o["eth_usd"]   = eth_usd
                o["gas_gwei"]  = round(gas_gwei, 4)
                o["timestamp"] = time.time()
                o["sim_profit_usd"] = (o["net_profit_percentage"] / 100.0) * \
                                       o.get("optimal_trade_size_usd", self.trade_size_usd)

            tri_opps = find_triangular_opportunities(
                pool_data,
                min_profit_pct=MIN_LOG_PROFIT,
                gas_cost_pct=gt_pct,
            )
            for o in tri_opps:
                o["block"]     = current_block
                o["eth_usd"]   = eth_usd
                o["gas_gwei"]  = round(gas_gwei, 4)
                o["timestamp"] = time.time()
                # Triangular doesn't have optimal_trade_size — use reference
                o["sim_profit_usd"] = (o["net_profit_percentage"] / 100.0) * self.trade_size_usd

            block_opps = tw_opps + tri_opps
            self.all_opps.extend(block_opps)

            # ── Live progress line ─────────────────────────────────────────
            remaining = max(0, self.duration_s - elapsed)
            bar       = _progress_bar(elapsed, self.duration_s)
            opp_color = GREEN if block_opps else RESET
            print(
                f"{_c(bar, CYAN)}  "
                f"blk={_c(str(current_block), BOLD)}  "
                f"pools={len(pool_data):2d}  "
                f"gas={_c(f'{gas_gwei:.3f}gwei', YELLOW)}  "
                f"ETH=${_c(str(int(eth_usd)), BOLD)}  "
                f"opps={_c(str(len(block_opps)), opp_color)}  "
                f"fetch={fetch_elapsed:.2f}s  "
                f"⏱ {int(remaining)}s left"
            )

            if block_opps:
                best = max(block_opps, key=lambda o: o["net_profit_percentage"])
                btype = "2-way" if best["type"] == "two_way" else "tri"
                profit_str = _c(f"{best['net_profit_percentage']:.4f}%", GREEN)
                print(f"  ★ {_c(best.get('pair','?'), BOLD)} [{btype}]"
                      f"  net={profit_str}"
                      f"  gross={best.get('gross_profit_percentage',0):.4f}%"
                      f"  size=${best.get('optimal_trade_size_usd', self.trade_size_usd):,.0f}"
                      f"  est_profit=${best['sim_profit_usd']:+.2f}")

                if len(block_opps) > 1:
                    others = sorted(block_opps, key=lambda o: o["net_profit_percentage"], reverse=True)[1:]
                    for o in others[:2]:
                        btype = "2-way" if o["type"] == "two_way" else "tri"
                        print(f"    · {o.get('pair','?'):20s} [{btype}]  "
                              f"net={o['net_profit_percentage']:.4f}%  "
                              f"est=${o['sim_profit_usd']:+.2f}")
                    if len(block_opps) > 3:
                        print(f"    … +{len(block_opps) - 3} more this block")

        print()
        print(_c("  Simulation complete — building report…", CYAN))
        print()
        return self._build_report(time.time() - start_time)

    # ── Report builder ─────────────────────────────────────────────────────

    def _build_report(self, actual_duration: float) -> dict:
        all_opps  = self.all_opps
        n_opps    = len(all_opps)
        n_blocks  = len(self.blocks_seen)
        two_way   = [o for o in all_opps if o.get("type") == "two_way"]
        tri       = [o for o in all_opps if o.get("type") == "triangular"]

        # ── Aggregate by pair ──────────────────────────────────────────────
        by_pair: Dict[str, List[dict]] = defaultdict(list)
        for o in all_opps:
            by_pair[o.get("pair", "?")].append(o)

        pair_stats = {}
        for pair, opps in by_pair.items():
            profits = [o["net_profit_percentage"] for o in opps]
            profits_usd = [o["sim_profit_usd"] for o in opps]
            pair_stats[pair] = {
                "count":          len(opps),
                "best_net_pct":   max(profits),
                "avg_net_pct":    sum(profits) / len(profits),
                "total_est_usd":  sum(profits_usd),
                "avg_est_usd":    sum(profits_usd) / len(profits),
                "types":          list({o["type"] for o in opps}),
            }

        # ── Top 10 opportunities ───────────────────────────────────────────
        top10 = sorted(all_opps, key=lambda o: o["net_profit_percentage"], reverse=True)[:10]
        top10_clean = []
        for o in top10:
            entry = {
                "pair":          o.get("pair", "?"),
                "type":          o.get("type", "?"),
                "block":         o.get("block", 0),
                "net_pct":       round(o["net_profit_percentage"], 6),
                "gross_pct":     round(o.get("gross_profit_percentage", 0), 6),
                "gas_gwei":      o.get("gas_gwei", 0),
                "sim_profit_usd": round(o["sim_profit_usd"], 2),
                "trade_size_usd": round(o.get("optimal_trade_size_usd", self.trade_size_usd), 2),
            }
            if o.get("type") == "two_way":
                entry["fees_pct"]    = round(o.get("total_fees_percentage", 0), 4)
                entry["slippage_pct"]= round(o.get("slippage_pct", 0), 4)
                entry["aave_fee_pct"]= o.get("aave_fee_pct", AAVE_FEE_PCT)
                entry["buy_dex"]     = o.get("buy_pool", {}).get("dex", "?")
                entry["sell_dex"]    = o.get("sell_pool", {}).get("dex", "?")
            else:
                entry["legs"] = [
                    f"{leg['from']}→{leg['to']} ({leg['dex']})"
                    for leg in o.get("legs", [])
                ]
            top10_clean.append(entry)

        # ── Profit distribution buckets ────────────────────────────────────
        buckets = {"0.01-0.05%": 0, "0.05-0.10%": 0,
                   "0.10-0.25%": 0, "0.25-0.50%": 0, ">0.50%": 0}
        for o in all_opps:
            p = o["net_profit_percentage"]
            if   p < 0.05:  buckets["0.01-0.05%"] += 1
            elif p < 0.10:  buckets["0.05-0.10%"] += 1
            elif p < 0.25:  buckets["0.10-0.25%"] += 1
            elif p < 0.50:  buckets["0.25-0.50%"] += 1
            else:           buckets[">0.50%"]      += 1

        # ── Extrapolated daily revenue ─────────────────────────────────────
        scale   = (86_400 / actual_duration) if actual_duration > 0 else 0
        daily_opps_est       = n_opps * scale
        total_est_usd        = sum(o["sim_profit_usd"] for o in all_opps)
        daily_rev_optimistic = total_est_usd * scale          # capture rate 100%
        daily_rev_flashbots  = daily_rev_optimistic * 0.70    # 70% capture with FB
        daily_rev_public     = daily_rev_optimistic * 0.30    # 30% capture, public mempool

        # ── Gas & timing stats ─────────────────────────────────────────────
        avg_gas_gwei  = sum(self.gas_prices) / len(self.gas_prices) if self.gas_prices else 0
        avg_eth_price = sum(self.eth_prices) / len(self.eth_prices) if self.eth_prices else 0
        avg_fetch_s   = sum(self.fetch_times) / len(self.fetch_times) if self.fetch_times else 0
        avg_cost_2way = _gas_cost_usd(GAS_TWO_WAY, avg_gas_gwei, avg_eth_price)

        report = {
            "meta": {
                "timestamp":         datetime.now(timezone.utc).isoformat(),
                "duration_s":        round(actual_duration, 1),
                "pool_count":        len(self.pool_addresses),
                "blocks_scanned":    n_blocks,
                "avg_fetch_s":       round(avg_fetch_s, 3),
                "avg_gas_gwei":      round(avg_gas_gwei, 4),
                "avg_eth_price_usd": round(avg_eth_price, 2),
                "avg_tx_cost_usd":   round(avg_cost_2way, 4),
            },
            "summary": {
                "total_opportunities": n_opps,
                "two_way":             len(two_way),
                "triangular":          len(tri),
                "pairs_with_opps":     len(by_pair),
                "opps_per_block":      round(n_opps / n_blocks, 3) if n_blocks else 0,
                "profit_distribution": buckets,
            },
            "projections": {
                "actual_duration_s":     round(actual_duration, 1),
                "scale_to_24h":          round(scale, 2),
                "daily_opps_est":        round(daily_opps_est),
                "total_sim_profit_usd":  round(total_est_usd, 2),
                "daily_rev_100pct_usd":  round(daily_rev_optimistic, 2),
                "daily_rev_flashbots_usd": round(daily_rev_flashbots, 2),
                "daily_rev_public_usd":  round(daily_rev_public, 2),
                "monthly_rev_flashbots": round(daily_rev_flashbots * 30, 2),
            },
            "top_10_opportunities": top10_clean,
            "by_pair":              {k: v for k, v in
                                     sorted(pair_stats.items(),
                                            key=lambda x: x[1]["total_est_usd"],
                                            reverse=True)},
        }
        return report


# ─────────────────────────────────────────────────────────────────────────────
# Report display
# ─────────────────────────────────────────────────────────────────────────────

def print_report(report: dict):
    m   = report["meta"]
    s   = report["summary"]
    p   = report["projections"]
    top = report["top_10_opportunities"]
    bp  = report["by_pair"]

    SEP  = _c("─" * 70, CYAN)
    DSEP = _c("═" * 70, BOLD)

    print(DSEP)
    print(_c("  SIMULATION REPORT", BOLD))
    print(DSEP)
    print(f"  Duration      : {m['duration_s']:.0f}s  "
          f"({m['duration_s']/60:.1f} min)")
    print(f"  Blocks scanned: {m['blocks_scanned']}  "
          f"(avg fetch {m['avg_fetch_s']:.2f}s/block)")
    print(f"  Pools watched : {m['pool_count']}")
    print(f"  Avg gas       : {m['avg_gas_gwei']:.4f} gwei  "
          f"→  ${m['avg_tx_cost_usd']:.4f}/tx")
    print(f"  Avg ETH price : ${m['avg_eth_price_usd']:,.2f}")

    print()
    print(SEP)
    print(_c("  OPPORTUNITIES FOUND", BOLD))
    print(SEP)
    print(f"  Total         : {_c(str(s['total_opportunities']), GREEN)}")
    print(f"  Two-way       : {s['two_way']}")
    print(f"  Triangular    : {s['triangular']}")
    print(f"  Active pairs  : {s['pairs_with_opps']}")
    print(f"  Per block     : {s['opps_per_block']:.3f}")
    print()
    print("  Profit distribution (net %):")
    for bucket, count in s["profit_distribution"].items():
        bar = "▓" * min(count, 40)
        print(f"    {bucket:12s}  {bar:<40s} {count}")

    print()
    print(SEP)
    print(_c("  TOP 10 OPPORTUNITIES", BOLD))
    print(SEP)
    fmt = "  {:3s}  {:22s}  {:7s}  {:9s}  {:9s}  {:12s}  {:10s}"
    print(fmt.format("#", "PAIR", "TYPE", "NET %", "GROSS %", "EST PROFIT", "SIZE"))
    print("  " + "─" * 68)
    for i, o in enumerate(top, 1):
        sign  = "+" if o["sim_profit_usd"] >= 0 else ""
        color = GREEN if o["sim_profit_usd"] > 0 else RED
        print(fmt.format(
            str(i),
            o["pair"][:22],
            o["type"][:7],
            f"{o['net_pct']:.4f}%",
            f"{o['gross_pct']:.4f}%",
            _c(f"${sign}{o['sim_profit_usd']:,.2f}", color),
            f"${o['trade_size_usd']:,.0f}",
        ))
        if o["type"] == "two_way":
            print(f"       └ fees={o['fees_pct']:.3f}% "
                  f"slip={o['slippage_pct']:.3f}% "
                  f"aave={o['aave_fee_pct']:.2f}% "
                  f"buy={o['buy_dex']} sell={o['sell_dex']}")
        else:
            if o.get("legs"):
                print(f"       └ {' → '.join(o['legs'][:3])}")

    print()
    print(SEP)
    print(_c("  BY-PAIR BREAKDOWN  (ranked by total estimated profit)", BOLD))
    print(SEP)
    fmt2 = "  {:22s}  {:6s}  {:9s}  {:9s}  {:12s}  {:12s}"
    print(fmt2.format("PAIR", "COUNT", "BEST NET", "AVG NET", "AVG PROFIT", "TOTAL PROFIT"))
    print("  " + "─" * 68)
    for pair, ps in list(bp.items())[:20]:
        color = GREEN if ps["total_est_usd"] > 0 else RESET
        print(fmt2.format(
            pair[:22],
            str(ps["count"]),
            f"{ps['best_net_pct']:.4f}%",
            f"{ps['avg_net_pct']:.4f}%",
            _c(f"${ps['avg_est_usd']:+.2f}", color),
            _c(f"${ps['total_est_usd']:+.2f}", color),
        ))

    print()
    print(SEP)
    print(_c("  PROJECTIONS  (extrapolated to 24h)", BOLD))
    print(SEP)
    print(f"  Scale factor         : ×{p['scale_to_24h']:.1f}  "
          f"({p['actual_duration_s']:.0f}s → 86400s)")
    print(f"  Estimated opps/day   : {_c(str(p['daily_opps_est']), YELLOW)}")
    print(f"  Sim profit in window : ${p['total_sim_profit_usd']:,.2f}")
    print()
    rev100  = f"${p['daily_rev_100pct_usd']:,.2f}"
    revfb   = f"${p['daily_rev_flashbots_usd']:,.2f}"
    revmo   = f"${p['monthly_rev_flashbots']:,.2f}"
    print(f"  Daily revenue (IF 100% capture) : {_c(rev100, BOLD)}")
    print(f"  With Flashbots (70% capture)    : {_c(revfb, GREEN)}")
    print(f"  Public mempool (30% capture)    : ${p['daily_rev_public_usd']:,.2f}")
    print(f"  Monthly  (Flashbots estimate)   : {_c(revmo, GREEN)}")
    print()
    print(DSEP)


def save_report(report: dict, base: str = "simulation"):
    json_file = f"{base}_report.json"
    txt_file  = f"{base}_summary.txt"

    with open(json_file, "w") as fh:
        json.dump(report, fh, indent=2)
    print(f"  JSON report  → {_c(json_file, CYAN)}")

    # Write plain-text summary (strip ANSI codes via a crude filter)
    import io, re
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    # Re-print without colour — just call with ANSI stripped
    print_report_plain(report)
    sys.stdout = old_stdout
    plain = buf.getvalue()

    with open(txt_file, "w") as fh:
        fh.write(plain)
    print(f"  Text summary → {_c(txt_file, CYAN)}")


def print_report_plain(report: dict):
    """Same as print_report but without ANSI colour codes."""
    m   = report["meta"]
    s   = report["summary"]
    p   = report["projections"]
    top = report["top_10_opportunities"]
    bp  = report["by_pair"]

    SEP = "─" * 70

    print("=" * 70)
    print("  SIMULATION REPORT")
    print("=" * 70)
    print(f"  Duration      : {m['duration_s']:.0f}s ({m['duration_s']/60:.1f} min)")
    print(f"  Blocks scanned: {m['blocks_scanned']}  (avg fetch {m['avg_fetch_s']:.2f}s/block)")
    print(f"  Pools watched : {m['pool_count']}")
    print(f"  Avg gas       : {m['avg_gas_gwei']:.4f} gwei  →  ${m['avg_tx_cost_usd']:.4f}/tx")
    print(f"  Avg ETH price : ${m['avg_eth_price_usd']:,.2f}")
    print()
    print(SEP)
    print("  OPPORTUNITIES FOUND")
    print(SEP)
    print(f"  Total         : {s['total_opportunities']}")
    print(f"  Two-way       : {s['two_way']}")
    print(f"  Triangular    : {s['triangular']}")
    print(f"  Active pairs  : {s['pairs_with_opps']}")
    print(f"  Per block     : {s['opps_per_block']:.3f}")
    print()
    print("  Profit distribution (net %):")
    for bucket, count in s["profit_distribution"].items():
        print(f"    {bucket:12s}  {count}")
    print()
    print(SEP)
    print("  TOP 10 OPPORTUNITIES")
    print(SEP)
    for i, o in enumerate(top, 1):
        sign = "+" if o["sim_profit_usd"] >= 0 else ""
        print(f"  {i:2d}. {o['pair']:22s}  [{o['type'][:7]}]"
              f"  net={o['net_pct']:.4f}%  gross={o['gross_pct']:.4f}%"
              f"  profit=${sign}{o['sim_profit_usd']:,.2f}  size=${o['trade_size_usd']:,.0f}")
        if o["type"] == "two_way":
            print(f"      fees={o['fees_pct']:.3f}% slip={o['slippage_pct']:.3f}% "
                  f"aave={o['aave_fee_pct']:.2f}% buy={o['buy_dex']} sell={o['sell_dex']}")
        elif o.get("legs"):
            print(f"      {' → '.join(o['legs'][:3])}")
    print()
    print(SEP)
    print("  PROJECTIONS (extrapolated to 24h)")
    print(SEP)
    print(f"  Estimated opps/day              : {p['daily_opps_est']}")
    print(f"  Sim profit in window            : ${p['total_sim_profit_usd']:,.2f}")
    print(f"  Daily revenue  (100% capture)   : ${p['daily_rev_100pct_usd']:,.2f}")
    print(f"  With Flashbots (70% capture)    : ${p['daily_rev_flashbots_usd']:,.2f}")
    print(f"  Public mempool (30% capture)    : ${p['daily_rev_public_usd']:,.2f}")
    print(f"  Monthly (Flashbots estimate)    : ${p['monthly_rev_flashbots']:,.2f}")
    print("=" * 70)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Live arbitrage simulation")
    parser.add_argument("--duration",  type=int,   default=600,
                        help="Simulation duration in seconds (default 600)")
    parser.add_argument("--bundle",    type=str,   default=None,
                        help="Override active_bundle in config.yaml")
    parser.add_argument("--trade-size", type=float, default=None,
                        help="Reference trade size USD (default from config)")
    parser.add_argument("--output",    type=str,   default="simulation",
                        help="Output file prefix (default 'simulation')")
    args = parser.parse_args()

    # ── Load config ────────────────────────────────────────────────────────
    config = _load_config()
    if args.bundle:
        config["pairs"]["active_bundle"] = args.bundle
        print(f"  Bundle overridden → {args.bundle}")

    trade_size = args.trade_size or config.get("execution", {}).get("trade_size_usd", 10_000)

    # ── Connect ────────────────────────────────────────────────────────────
    rpc_url = os.getenv("RPC_URL", "")
    if not rpc_url:
        print(_c("Error: RPC_URL not set in .env", RED))
        sys.exit(1)

    print(f"  Connecting to RPC…  ", end="", flush=True)
    w3 = _connect_web3(rpc_url)
    print(_c(f"OK  (block #{w3.eth.block_number}, "
             f"gas={w3.eth.gas_price/1e9:.3f} gwei)", GREEN))

    # ── Discover pools ─────────────────────────────────────────────────────
    print(f"  Loading pool list…  ", end="", flush=True)
    pm = PairManager(config)
    pm.discover_pairs()
    pool_addresses = pm.get_all_pool_addresses()
    print(_c(f"OK  ({len(pool_addresses)} pools, "
             f"bundle={config['pairs']['active_bundle']})", GREEN))

    # ── Run ────────────────────────────────────────────────────────────────
    runner = SimulationRunner(
        w3             = w3,
        pool_addresses = pool_addresses,
        trade_size_usd = trade_size,
        duration_s     = args.duration,
    )
    report = runner.run()

    # ── Display & save ─────────────────────────────────────────────────────
    print_report(report)
    print()
    save_report(report, base=args.output)
    print()


if __name__ == "__main__":
    main()
