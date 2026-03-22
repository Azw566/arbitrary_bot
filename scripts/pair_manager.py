"""
PairManager
-----------
Discovers tradeable pairs from all configured DEXes, deduplicates them,
applies whitelist/blacklist filters, and exposes a flat pool-address index
for the monitoring loop.
"""

import logging
import os
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(__file__))

logger = logging.getLogger(__name__)


class PairManager:
    """
    Discovers and manages trading pairs across DEXes.

    Attributes
    ----------
    pairs       : dict name → pair_info (with list of pools)
    pool_addresses  : flat list of all pool addresses to monitor
    pool_to_pair    : pool_address → pair name
    """

    def __init__(self, config: dict):
        self.config = config
        self.pairs: Dict[str, dict] = {}
        self.pool_addresses: List[str] = []
        self.pool_to_pair: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover_pairs(self) -> List[dict]:
        """
        Fetches top pairs from all DEXes, merges them, applies filters,
        and builds the internal pool index.  Runs once at startup.

        Returns a list of pair dicts sorted by total volume (descending).
        """
        from onchainprice import (
            get_top_pairs_v2,
            get_top_pairs_v3,
            get_top_pairs_sushiswap,
            get_top_pairs_curve,
            fetch_matching_pairs_for_curve,
            normalize_pair,
            UNISWAP_V2,
            UNISWAP_V3,
            SUSHISWAP,
        )

        cfg = self.config["pairs"]
        nb = cfg.get("max_pairs", 50)

        logger.info("Fetching top pairs from all DEXes (limit=%d each)…", nb)

        v2_pairs    = get_top_pairs_v2(UNISWAP_V2, limit=nb)
        v3_pairs    = get_top_pairs_v3(UNISWAP_V3, limit=nb)
        sushi_pairs = get_top_pairs_sushiswap(SUSHISWAP, limit=nb)
        curve_pairs = get_top_pairs_curve(limit=nb)

        v2_from_curve, v3_from_curve, sushi_from_curve = fetch_matching_pairs_for_curve(
            curve_pairs, UNISWAP_V2, UNISWAP_V3, SUSHISWAP
        )

        # Build normalised-key → pair maps, deduplicating
        v2_map    = {normalize_pair(p): p for p in v2_pairs}
        v3_map    = {normalize_pair(p): p for p in v3_pairs}
        sushi_map = {normalize_pair(p): p for p in sushi_pairs}
        curve_map = {normalize_pair(p): p for p in curve_pairs}

        for p in v2_from_curve:
            v2_map.setdefault(normalize_pair(p), p)
        for p in v3_from_curve:
            v3_map.setdefault(normalize_pair(p), p)
        for p in sushi_from_curve:
            sushi_map.setdefault(normalize_pair(p), p)

        all_keys = set(v2_map) | set(v3_map) | set(sushi_map) | set(curve_map)

        blacklist = {s.upper() for s in cfg.get("blacklist") or []}
        whitelist = {s.upper() for s in cfg.get("whitelist") or []}
        manual_mode = cfg.get("mode", "auto") == "manual"

        common_pairs: List[dict] = []

        for key in all_keys:
            pools: List[dict] = []
            pair_name: Optional[str] = None

            if key in v2_map:
                p = v2_map[key]
                pair_name = pair_name or f"{p['symbol0']}/{p['symbol1']}"
                pools.append(self._pool_entry("UniV2", "V2", p["pair_id"], p.get("volume_usd", 0)))

            if key in v3_map:
                p = v3_map[key]
                pair_name = pair_name or f"{p['symbol0']}/{p['symbol1']}"
                pools.append(self._pool_entry("UniV3", "V3", p["pair_id"], p.get("volume_usd", 0)))

            if key in sushi_map:
                p = sushi_map[key]
                pair_name = pair_name or f"{p['symbol0']}/{p['symbol1']}"
                pools.append(self._pool_entry("Sushi", "V2", p["pair_id"], p.get("volume_usd", 0)))

            if key in curve_map:
                p = curve_map[key]
                pools.append(self._pool_entry("Curve", "Curve", p["pair_id"], p.get("volume_usd", 0)))

            # Need ≥ 2 DEXes to have an arbitrage opportunity
            if len(pools) < 2:
                continue

            label = (pair_name or f"{str(key[0])[:6]}/{str(key[1])[:6]}").upper()

            if label in blacklist:
                continue
            if manual_mode and whitelist and label not in whitelist:
                continue

            common_pairs.append({
                "key":    key,
                "name":   pair_name or label,
                "token0": key[0],
                "token1": key[1],
                "pools":  pools,
                "total_volume_usd": sum(p["volume_usd"] for p in pools),
            })

        # Sort best-liquidity pairs first
        common_pairs.sort(key=lambda x: x["total_volume_usd"], reverse=True)
        common_pairs = common_pairs[: nb]

        logger.info("Discovered %d tradeable pairs (%d pool addresses)",
                    len(common_pairs),
                    sum(len(p["pools"]) for p in common_pairs))

        # Build internal indexes
        self.pairs = {p["name"]: p for p in common_pairs}
        self.pool_addresses = [
            pool["address"]
            for pair in common_pairs
            for pool in pair["pools"]
        ]
        self.pool_to_pair = {
            pool["address"]: pair["name"]
            for pair in common_pairs
            for pool in pair["pools"]
        }

        return common_pairs

    def get_all_pool_addresses(self) -> List[str]:
        return self.pool_addresses

    def get_pair_for_pool(self, pool_address: str) -> Optional[str]:
        return self.pool_to_pair.get(pool_address)

    def summary(self) -> str:
        lines = [f"Monitoring {len(self.pairs)} pairs / {len(self.pool_addresses)} pools"]
        for name, pair in self.pairs.items():
            dexes = ", ".join(p["dex"] for p in pair["pools"])
            lines.append(f"  {name:20s}  [{dexes}]")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pool_entry(dex: str, version: str, address: str, volume_usd: float) -> dict:
        return {"dex": dex, "version": version, "address": address, "volume_usd": volume_usd}
