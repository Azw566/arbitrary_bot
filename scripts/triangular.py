"""
Triangular Arbitrage Detector
------------------------------
Finds A → B → C → A cycles where the combined exchange rate (after fees)
exceeds 1, i.e. you end up with more of token A than you started with.

Algorithm
---------
1. Build a directed edge graph from pool_data_list:
      edge[token_in][token_out] = best (rate * (1 - fee)) across available pools

2. For every ordered 3-token combination (A, B, C):
      if edges A→B, B→C, C→A all exist:
          product = rate_AB * rate_BC * rate_CA
          net_profit_pct = (product - 1) * 100 - gas_cost_pct

3. Return all cycles with net_profit_pct > min_profit_pct, sorted best-first.

Notes
-----
- Rates already include pool fee (rate = raw_price * (1 - fee_pct/100))
- gas_cost_pct should be computed by the caller based on current gas price
- Duplicate cycles (A→B→C and A→C→B) are deduplicated by token-set key
"""

import logging
from collections import defaultdict
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


def find_triangular_opportunities(
    pool_data_list: List[Dict],
    min_profit_pct: float = 0.05,
    gas_cost_pct: float = 0.0,
) -> List[Dict]:

    # ------------------------------------------------------------------
    # Build directed edge graph
    # ------------------------------------------------------------------
    # graph[addr_in][addr_out] = {"rate": float, "fee_pct": float,
    #                              "pool": str, "dex": str,
    #                              "sym_in": str, "sym_out": str}
    graph: Dict[str, Dict[str, dict]] = defaultdict(dict)

    for pool in pool_data_list:
        t0 = pool.get("token0", {})
        t1 = pool.get("token1", {})

        if not isinstance(t0, dict) or not isinstance(t1, dict):
            continue

        addr0 = t0.get("address", "").lower()
        addr1 = t1.get("address", "").lower()
        sym0  = t0.get("symbol", addr0[:8])
        sym1  = t1.get("symbol", addr1[:8])

        p01   = pool.get("price_0_in_1", 0.0)   # units of token1 per token0
        p10   = pool.get("price_1_in_0", 0.0)   # units of token0 per token1
        fee   = pool.get("fee_percentage", 0.3)  # already in % (e.g. 0.3 = 0.3%)
        paddr = pool.get("pool_address", "?")
        dex   = pool.get("dex", "?")

        if not addr0 or not addr1 or p01 <= 0 or p10 <= 0:
            continue

        multiplier = 1 - fee / 100  # e.g. 0.9970 for 0.3% fee

        def _best(graph_slot: dict, rate: float, meta: dict):
            """Keep only the best-rate edge for each (in, out) token pair."""
            if graph_slot.get("rate", -1) < rate:
                graph_slot.update(meta)
                graph_slot["rate"] = rate

        _best(graph[addr0].setdefault(addr1, {}),
              p01 * multiplier,
              {"fee_pct": fee, "pool": paddr, "dex": dex, "sym_in": sym0, "sym_out": sym1})

        _best(graph[addr1].setdefault(addr0, {}),
              p10 * multiplier,
              {"fee_pct": fee, "pool": paddr, "dex": dex, "sym_in": sym1, "sym_out": sym0})

    # ------------------------------------------------------------------
    # Find 3-cycles
    # ------------------------------------------------------------------
    opportunities: List[Dict] = []
    seen: set = set()
    tokens = list(graph.keys())

    for a in tokens:
        for b, e_ab in graph[a].items():
            if b == a:
                continue
            for c, e_bc in graph[b].items():
                if c == a or c == b:
                    continue
                e_ca = graph[c].get(a)
                if e_ca is None:
                    continue

                # Deduplicate: {a,b,c} is the same cycle regardless of start
                key = frozenset([a, b, c])
                if key in seen:
                    continue
                seen.add(key)

                product       = e_ab["rate"] * e_bc["rate"] * e_ca["rate"]
                gross_pct     = (product - 1) * 100
                net_profit_pct = gross_pct - gas_cost_pct

                if net_profit_pct <= min_profit_pct:
                    continue

                opportunities.append({
                    "type":  "triangular",
                    "pair":  f"{e_ab['sym_in']}→{e_ab['sym_out']}→{e_bc['sym_out']}→{e_ca['sym_out']}",
                    "legs": [
                        _leg(e_ab, a, b),
                        _leg(e_bc, b, c),
                        _leg(e_ca, c, a),
                    ],
                    "product":              round(product, 8),
                    "gross_profit_percentage": round(gross_pct, 6),
                    "gas_cost_pct":         round(gas_cost_pct, 4),
                    "net_profit_percentage": round(net_profit_pct, 6),
                })

    opportunities.sort(key=lambda x: x["net_profit_percentage"], reverse=True)

    if opportunities:
        logger.info("Triangular: found %d opportunities (best: %.4f%%)",
                    len(opportunities),
                    opportunities[0]["net_profit_percentage"])

    return opportunities


def _leg(edge: dict, addr_in: str, addr_out: str) -> dict:
    return {
        "from":     edge.get("sym_in",  addr_in[:8]),
        "to":       edge.get("sym_out", addr_out[:8]),
        "rate":     round(edge["rate"], 8),
        "fee_pct":  edge["fee_pct"],
        "pool":     edge["pool"],
        "dex":      edge["dex"],
    }
