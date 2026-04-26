// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import {Script, console} from "forge-std/Script.sol";
import {MultiDexArbitrage} from "../contracts/MULTIDEX.sol";

/**
 * @title Deploy
 * @notice Foundry deployment script for MultiDexArbitrage.
 *
 * Supported networks
 * ------------------
 *   Mainnet (1)       Aave V3: 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2
 *   Sepolia (11155111) Aave V3: 0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951
 *   Anvil fork (31337) Aave V3: uses mainnet address (forked state)
 *
 * Usage
 * -----
 *   # Simulate only (no broadcast):
 *   forge script script/Deploy.s.sol --rpc-url $RPC_URL -vvvv
 *
 *   # Deploy to Anvil local fork (make fork must be running):
 *   forge script script/Deploy.s.sol --rpc-url http://localhost:8545 \
 *       --private-key 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 \
 *       --broadcast -vvvv
 *
 *   # Deploy to Sepolia:
 *   forge script script/Deploy.s.sol --rpc-url $SEPOLIA_RPC_URL \
 *       --private-key $PRIVATE_KEY --broadcast --verify \
 *       --etherscan-api-key $ETHERSCAN_API_KEY -vvvv
 *
 *   After deployment, copy the printed address into .env:
 *       CONTRACT_ADDRESS=0x<deployed_address>
 */
contract Deploy is Script {

    // Aave V3 Pool addresses per network
    address constant AAVE_POOL_MAINNET  = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address constant AAVE_POOL_SEPOLIA  = 0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951;
    // Anvil fork: use the mainnet address (forked state includes Aave contracts)
    address constant AAVE_POOL_ANVIL    = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;

    function run() external {
        // Resolve the correct Aave pool for the target network
        address aavePool = _aavePool();

        console.log("Chain ID  :", block.chainid);
        console.log("Aave Pool :", aavePool);
        console.log("Deployer  :", msg.sender);

        vm.startBroadcast();

        MultiDexArbitrage arb = new MultiDexArbitrage(aavePool);

        vm.stopBroadcast();

        console.log("Deployed MultiDexArbitrage at:", address(arb));
        console.log("Owner:", arb.owner());
        console.log("");
        console.log("Next step: add to .env");
        console.log("  CONTRACT_ADDRESS=", address(arb));
    }

    function _aavePool() internal view returns (address) {
        if (block.chainid == 1) {
            return AAVE_POOL_MAINNET;
        } else if (block.chainid == 11155111) {
            return AAVE_POOL_SEPOLIA;
        } else if (block.chainid == 31337) {
            // Anvil mainnet fork — Aave contracts exist at mainnet address
            return AAVE_POOL_ANVIL;
        } else {
            revert("Unsupported chain: add Aave pool address for this network");
        }
    }
}
