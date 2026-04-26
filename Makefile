# ============================================================
#  Arbitrage Bot — task runner
# ============================================================
#  Requires: Foundry (forge + anvil), Python 3, .env filled in.
#
#  Phase 2 workflow (zero real money):
#    1. make fork          — start Anvil mainnet fork in background
#    2. make deploy-fork   — deploy contract to local fork
#    3. make bot-fork      — run bot against local fork (dry_run=false safe)
#    4. make test          — run unit tests
#
#  Phase 2b — Sepolia (test wallet + free ETH):
#    1. make deploy-sepolia
#    2. make test-integration
#
#  Phase 3 — Mainnet:
#    1. make deploy-dry    — simulate, verify gas
#    2. make deploy-mainnet
#    3. make bot
# ============================================================

-include .env
export

# Anvil default test account #0 private key (public, safe for local forks)
ANVIL_KEY := 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80

.PHONY: build build-verbose \
        test test-unit test-integration test-all \
        fork deploy-fork bot-fork \
        deploy-dry deploy-sepolia deploy-mainnet verify \
        bot bot-dry \
        clean install budget

# ── Solidity ────────────────────────────────────────────────

build:
	forge build

build-verbose:
	forge build -vvvv

# ── Python tests ────────────────────────────────────────────

test: test-unit

test-unit:
	python3 -m pytest tests/ -m unit -v --tb=short

test-integration:
	@echo "Running integration tests — requires SEPOLIA_RPC_URL in .env"
	RPC_URL=$(SEPOLIA_RPC_URL) \
	python3 -m pytest tests/test_integration_sepolia.py -m integration -v --tb=short

test-all:
	python3 -m pytest tests/ -v --tb=short \
	  --ignore=tests/test_monitoring.py \
	  --ignore=tests/test_price_changes.py

# ── Anvil mainnet fork (Phase 2 — zero real money) ─────────
#
# Forks mainnet at the latest block. The fork contains all real
# Uniswap/Aave/Curve contracts and pool state.
# Run this in a separate terminal, then use deploy-fork + bot-fork.

fork:
	@echo "Starting Anvil mainnet fork on localhost:8545 …"
	@echo "(run in a separate terminal or add & to background)"
	anvil \
	  --fork-url $(RPC_URL) \
	  --chain-id 31337 \
	  --port 8545 \
	  --block-time 12 \
	  --accounts 10 \
	  --balance 10000

# Deploy contract to local Anvil fork (uses test key — no real ETH)
deploy-fork: build
	@echo "Deploying to Anvil fork (localhost:8545) …"
	forge script script/Deploy.s.sol \
	  --rpc-url http://localhost:8545 \
	  --private-key $(ANVIL_KEY) \
	  --broadcast \
	  -vvvv
	@echo ""
	@echo "Copy the printed address into .env as CONTRACT_ADDRESS=0x..."

# Run bot against Anvil fork — dry_run=false is safe here (fake ETH)
bot-fork:
	@test -n "$(CONTRACT_ADDRESS)" || \
	  (echo "Set CONTRACT_ADDRESS in .env first (run make deploy-fork)"; exit 1)
	RPC_URL=ws://localhost:8545 \
	CHAIN_ID=31337 \
	python3 scripts/bot.py

# ── Sepolia deployment ───────────────────────────────────────

# Simulate Sepolia deployment (no broadcast)
deploy-dry:
	@test -n "$(SEPOLIA_RPC_URL)" || (echo "Set SEPOLIA_RPC_URL in .env"; exit 1)
	forge script script/Deploy.s.sol \
	  --rpc-url $(SEPOLIA_RPC_URL) \
	  -vvvv

# Deploy to Sepolia + verify on Etherscan
deploy-sepolia: build
	@test -n "$(PRIVATE_KEY)" || (echo "Set PRIVATE_KEY in .env"; exit 1)
	@test -n "$(SEPOLIA_RPC_URL)" || (echo "Set SEPOLIA_RPC_URL in .env"; exit 1)
	@echo "Deploying to Sepolia (chain 11155111) …"
	forge script script/Deploy.s.sol \
	  --rpc-url $(SEPOLIA_RPC_URL) \
	  --private-key $(PRIVATE_KEY) \
	  --broadcast \
	  --verify \
	  --etherscan-api-key $(ETHERSCAN_API_KEY) \
	  -vvvv
	@echo ""
	@echo "Copy the deployed address into .env as CONTRACT_ADDRESS=0x..."

# Verify an already-deployed contract (if --verify failed during deploy)
verify:
	@test -n "$(CONTRACT_ADDRESS)" || (echo "Set CONTRACT_ADDRESS in .env"; exit 1)
	forge verify-contract $(CONTRACT_ADDRESS) contracts/MULTIDEX.sol:MultiDexArbitrage \
	  --chain-id 11155111 \
	  --etherscan-api-key $(ETHERSCAN_API_KEY)

# ── Mainnet deployment ───────────────────────────────────────

deploy-mainnet: build
	@test -n "$(PRIVATE_KEY)" || (echo "Set PRIVATE_KEY in .env"; exit 1)
	@echo "Deploying to MAINNET — are you sure? Ctrl-C to cancel."
	@sleep 5
	forge script script/Deploy.s.sol \
	  --rpc-url $(RPC_URL) \
	  --private-key $(PRIVATE_KEY) \
	  --broadcast \
	  --verify \
	  --etherscan-api-key $(ETHERSCAN_API_KEY) \
	  -vvvv

# ── Bot ─────────────────────────────────────────────────────

# Run bot on mainnet in dry-run mode (detection only — no real txs)
bot-dry:
	DRY_RUN=true python3 scripts/bot.py

# Run bot on mainnet with real execution
bot:
	@test -n "$(CONTRACT_ADDRESS)" || (echo "Set CONTRACT_ADDRESS in .env"; exit 1)
	python3 scripts/bot.py

# ── Budget calculator ────────────────────────────────────────
# Prints the expected gas costs and break-even analysis.

budget:
	python3 scripts/budget.py

# 10-minute live simulation + analysis report
simulate:
	python3 scripts/simulate_live.py --duration 600

simulate-fast:
	python3 scripts/simulate_live.py --duration 120 --bundle balanced

simulate-all:
	python3 scripts/simulate_live.py --duration 600 --bundle all

# ── Misc ─────────────────────────────────────────────────────

clean:
	forge clean

install:
	pip3 install -r requirements.txt
