// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Minimal testnet contract for deploying and verifying arbitrage workflows.
contract DeployableArb {
    address public owner;

    event Executed(address indexed executor, uint256 value);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }

    constructor() payable {
        owner = msg.sender;
    }

    /// @notice Placeholder execution entry point.
    function execute() external payable returns (bool) {
        emit Executed(msg.sender, msg.value);
        return true;
    }

    /// @notice Withdraw ETH to a specified address. Owner only.
    function withdraw(address payable to, uint256 amount) external onlyOwner {
        require(to != address(0), "Zero address");
        require(amount <= address(this).balance, "Insufficient balance");
        (bool ok,) = to.call{value: amount}("");
        require(ok, "Transfer failed");
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Zero address");
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    receive() external payable {}
}
