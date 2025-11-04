// Minimal deployable contract for testing arbitrage workflows
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.17;

contract DeployableArb {
    address public owner;

    event Executed(address indexed executor, uint256 value);

    constructor() payable {
        owner = msg.sender;
    }

    // simple execute function used as placeholder
    function execute() external payable returns (bool) {
        emit Executed(msg.sender, msg.value);
        return true;
    }

    // allow owner to withdraw ETH
    function withdraw(address payable to, uint256 amount) external returns (bool) {
        require(msg.sender == owner, "only owner");
        to.transfer(amount);
        return true;
    }
}
