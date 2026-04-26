#!/usr/bin/env python3
"""
Budget & break-even calculator for the arbitrage bot.
Run: python3 scripts/budget.py
  or: make budget
"""

ETH_PRICE  = 3500     # USD/ETH  — update to current spot price
GAS_GWEI   = 20       # gwei     — typical mainnet base fee (check etherscan.io/gastracker)
GAS_2WAY   = 400_000  # gas units for 2-swap flash-loan arbitrage
GAS_TRI    = 600_000  # gas units for 3-swap triangular flash-loan arbitrage
AAVE_FEE   = 0.09     # % Aave V3 flash loan premium
MIN_FEES   = 0.35     # % combined pool fees (0.05% buy + 0.30% sell — common case)
SLIPPAGE   = 0.10     # % dynamic slippage at typical optimal trade size


def tx_cost_usd(gas_units: int) -> float:
    return gas_units * GAS_GWEI * 1e-9 * ETH_PRICE


cost_2way = tx_cost_usd(GAS_2WAY)
cost_tri  = tx_cost_usd(GAS_TRI)
fixed_pct = MIN_FEES + AAVE_FEE + SLIPPAGE   # always paid, independent of trade size

SEP = "=" * 62

print(SEP)
print("  ARBITRAGE BOT — BUDGET & BREAK-EVEN ANALYSIS")
print(SEP)
print(f"  Assumptions: ETH=${ETH_PRICE:,}  gas={GAS_GWEI} gwei  date=2026-04")
print()
print("  TRANSACTION COSTS")
print(f"    2-way arb  : {cost_2way / ETH_PRICE:.4f} ETH  =  ${cost_2way:.2f}")
print(f"    Triangular : {cost_tri  / ETH_PRICE:.4f} ETH  =  ${cost_tri:.2f}")
print()
print("  FIXED COSTS (% of notional, independent of trade size)")
print(f"    Pool fees (buy+sell) : {MIN_FEES:.2f}%")
print(f"    Aave flash loan fee  : {AAVE_FEE:.2f}%")
print(f"    Dynamic slippage est : {SLIPPAGE:.2f}%")
print(f"    Fixed subtotal       : {fixed_pct:.2f}%")
print()
print("  MINIMUM GROSS SPREAD TO BREAK EVEN — by trade size")
print(f"  {'Trade size':>12}  {'Gas cost %':>10}  {'Min spread':>11}  {'Min profit':>11}")
print(f"  {'-'*12}  {'-'*10}  {'-'*11}  {'-'*11}")
for size in [5_000, 10_000, 25_000, 50_000, 100_000]:
    gas_pct    = (cost_2way / size) * 100
    min_spread = fixed_pct + gas_pct
    min_profit = (min_spread / 100) * size
    print(f"  ${size:>11,}  {gas_pct:>9.3f}%  {min_spread:>10.3f}%  ${min_profit:>10.2f}")
print()
print("  STARTING BUDGET (mainnet)")
reserve     = 0.05    # ETH gas reserve (configured in config.yaml)
safety      = 0.15    # ETH safety buffer covers ~18 reverted txs at $28 each
recommended = reserve + safety
print(f"    Gas reserve (config) : {reserve:.2f} ETH  =  ${reserve * ETH_PRICE:,.0f}")
print(f"    Safety (~18 reverts) : {safety:.2f} ETH  =  ${safety * ETH_PRICE:,.0f}")
print(f"    RECOMMENDED MINIMUM  : {recommended:.2f} ETH  =  ${recommended * ETH_PRICE:,.0f}")
print()
print("  PREDICTED DAILY REVENUE (mainnet, balanced bundle, calm market)")
print(f"  {'Scenario':20}  {'Trades/day':>10}  {'Avg profit':>11}  {'Daily rev':>10}")
print(f"  {'-'*20}  {'-'*10}  {'-'*11}  {'-'*10}")
trade_size  = 25_000
net_pct_map = {
    "Conservative":  (5,  0.15),
    "Moderate":      (15, 0.20),
    "Volatile mkt":  (35, 0.30),
}
for label, (n, net_pct) in net_pct_map.items():
    per_trade  = (net_pct / 100) * trade_size
    daily      = n * per_trade
    print(f"  {label:20}  {n:>10}  ${per_trade:>10.0f}  ${daily:>9,.0f}")
print()
print("  MEV CAPTURE RATE (% of detected opps that execute before MEV bots)")
print("    Without Flashbots : ~20-40%  (high sandwich risk)")
print("    With Flashbots    : ~60-80%  (private mempool, protected)")
print()
print("  PHASE 2 — SEPOLIA TEST BUDGET (free, from faucet)")
print("    Deploy contract  : ~0.003 Sepolia ETH")
print("    20 test txs      : ~0.040 Sepolia ETH")
print("    Recommended      :  0.10  Sepolia ETH")
print("    Source           : sepoliafaucet.com  (0.5 ETH/day via Alchemy)")
print()
print("  PHASE 2 — ANVIL LOCAL FORK (zero cost)")
print("    Uses: anvil --fork-url $RPC_URL")
print("    Test key funded with 10,000 ETH (fake money)")
print("    Command: make fork  →  make deploy-fork  →  make bot-fork")
print(SEP)
