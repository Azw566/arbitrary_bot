// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

// --- Uniswap V2 Router ---
interface IUniswapV2Router02 {
    function swapExactTokensForTokens(
        uint amountIn, uint amountOutMin, address[] calldata path, address to, uint deadline
    ) external returns (uint[] memory amounts);
}

// --- Uniswap V3 Swap Router ---
interface ISwapRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params) external returns (uint amountOut);
}

// --- Curve Pool ---
interface ICurvePool {
    function exchange(int128 i, int128 j, uint256 dx, uint256 min_dy) external returns (uint256);
}

// --- Aave V3 Flash Loan ---
interface IFlashLoanSimpleReceiver {
    function executeOperation(
        address asset, uint256 amount, uint256 premium, address initiator, bytes calldata params
    ) external returns (bool);
}

interface IPool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

// -----------------------------------------------------------------------
// Params struct avoids the deep abi.decode tuple (stack-too-deep risk)
// -----------------------------------------------------------------------
struct ArbParams {
    address routerV3;
    address tokenInV3;
    address tokenOutV3;
    uint24  feeV3;
    uint256 amountOutMinV3;
    address curvePool;
    int128  curveI;
    int128  curveJ;
    uint256 amountOutMinCurve;
    address routerV2;
    address[] pathV2;
    uint256 amountOutMinV2;
}

contract AtomicArb is IFlashLoanSimpleReceiver, ReentrancyGuard {
    using SafeERC20 for IERC20;

    address public owner;
    IPool public immutable aavePool;

    event ArbExecuted(address indexed asset, uint256 borrowed, uint256 profit);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }

    constructor(address _aavePool) {
        require(_aavePool != address(0), "Zero address");
        owner = msg.sender;
        aavePool = IPool(_aavePool);
    }

    // -----------------------------------------------------------------------
    // Initiate flash loan — encodes ArbParams off-chain via abi.encode
    // -----------------------------------------------------------------------
    function startFlashLoan(address token, uint256 amount, bytes calldata params)
        external onlyOwner
    {
        aavePool.flashLoanSimple(address(this), token, amount, params, 0);
    }

    // -----------------------------------------------------------------------
    // Aave callback
    // -----------------------------------------------------------------------
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override nonReentrant returns (bool) {
        require(msg.sender == address(aavePool), "Caller must be Aave Pool");
        require(initiator == address(this), "Not self-initiated");

        ArbParams memory p = abi.decode(params, (ArbParams));

        // Step 1: Uniswap V3  (asset → tokenOutV3)
        IERC20(asset).forceApprove(p.routerV3, amount);
        uint256 receivedV3 = ISwapRouter(p.routerV3).exactInputSingle(
            ISwapRouter.ExactInputSingleParams({
                tokenIn:              asset,
                tokenOut:             p.tokenOutV3,
                fee:                  p.feeV3,
                recipient:            address(this),
                deadline:             block.timestamp + 300,
                amountIn:             amount,
                amountOutMinimum:     p.amountOutMinV3,
                sqrtPriceLimitX96:    0
            })
        );

        // Step 2: Curve  (tokenOutV3 → next token, chained from step 1 output)
        IERC20(p.tokenOutV3).forceApprove(p.curvePool, receivedV3);
        uint256 receivedCurve = ICurvePool(p.curvePool).exchange(
            p.curveI, p.curveJ, receivedV3, p.amountOutMinCurve
        );

        // Step 3: Uniswap V2  (chained from step 2 output)
        IERC20(p.pathV2[0]).forceApprove(p.routerV2, receivedCurve);
        uint[] memory amountsV2 = IUniswapV2Router02(p.routerV2).swapExactTokensForTokens(
            receivedCurve,
            p.amountOutMinV2,
            p.pathV2,
            address(this),
            block.timestamp + 300
        );

        // Repay flash loan
        uint256 totalDebt = amount + premium;
        IERC20(asset).forceApprove(address(aavePool), totalDebt);

        uint256 finalBalance = IERC20(asset).balanceOf(address(this));
        require(finalBalance >= totalDebt, "Insufficient balance to repay");

        uint256 profit = finalBalance > totalDebt ? finalBalance - totalDebt : 0;
        emit ArbExecuted(asset, amount, profit);

        // silence unused-var warning
        amountsV2;

        return true;
    }

    // -----------------------------------------------------------------------
    // Admin
    // -----------------------------------------------------------------------
    function withdrawToken(address token) external onlyOwner {
        uint256 balance = IERC20(token).balanceOf(address(this));
        require(balance > 0, "Nothing to withdraw");
        IERC20(token).safeTransfer(owner, balance);
    }

    function withdrawETH() external onlyOwner {
        uint256 balance = address(this).balance;
        require(balance > 0, "Nothing to withdraw");
        (bool ok,) = owner.call{value: balance}("");
        require(ok, "ETH transfer failed");
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Zero address");
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    receive() external payable {}
}
