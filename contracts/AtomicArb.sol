// SPDX-License-Identifier: MIT
pragma solidity ^0.8.17;

import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";

//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
// Définition des différents DEX et leurs ABI
//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////

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
    function exchange(int128 i, int128 j, uint256 dx, uint256 min_dy) external;
}

// --- Aave Flash Loan (Simple Receiver) ---
interface IFlashLoanSimpleReceiver {
    function executeOperation(
        address asset, uint256 amount, uint256 premium, address initiator, bytes calldata params
    ) external returns (bool);
}

interface ILendingPool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}


//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
// Définition du contrat
//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////

contract AtomicArb is IFlashLoanSimpleReceiver, ReentrancyGuard {
    using SafeERC20 for IERC20;

    address public owner;
    ILendingPool public lendingPool;

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }

    constructor(address _lendingPool) {
        owner = msg.sender;
        lendingPool = ILendingPool(_lendingPool);
    }

    // --- Initiate flash loan ---
    function startFlashLoan(address token, uint256 amount, bytes calldata params) external onlyOwner {
        lendingPool.flashLoanSimple(address(this), token, amount, params, 0);
    }

    // --- Callback from Aave ---
    function executeOperation(
        address asset, uint256 amount, uint256 premium, address initiator, bytes calldata params
    ) external override nonReentrant returns (bool) {
        require(msg.sender == address(lendingPool), "Not lending pool");
        require(initiator == address(this), "Not initiated by contract");

        // Decode params to get all needed info for swaps
        (
            address routerV3,
            address tokenInV3,
            address tokenOutV3,
            uint24 feeV3,
            uint amountOutMinV3,
            address curvePool,
            int128 curveI,
            int128 curveJ,
            uint amountInCurve,
            uint amountOutMinCurve,
            address routerV2,
            address[] memory pathV2,
            uint amountInV2,
            uint amountOutMinV2
        ) = abi.decode(params, (
            address,address,address,uint24,uint,address,int128,int128,uint,uint,address,address[],uint,uint
        ));

        // -------- Step 1: Uniswap V3 ----------
        IERC20(tokenInV3).safeApprove(routerV3, amount);
        ISwapRouter.ExactInputSingleParams memory paramsV3 = ISwapRouter.ExactInputSingleParams({
            tokenIn: tokenInV3,
            tokenOut: tokenOutV3,
            fee: feeV3,
            recipient: address(this),
            deadline: block.timestamp + 300,
            amountIn: amount,
            amountOutMinimum: amountOutMinV3,
            sqrtPriceLimitX96: 0
        });
        uint receivedV3 = ISwapRouter(routerV3).exactInputSingle(paramsV3);
        require(receivedV3 >= amountOutMinV3, "Uniswap V3 swap failed");

        // -------- Step 2: Curve ----------
        IERC20(tokenOutV3).safeApprove(curvePool, amountInCurve);
        ICurvePool(curvePool).exchange(curveI, curveJ, amountInCurve, amountOutMinCurve);

        // -------- Step 3: Uniswap V2 ----------
        IERC20(pathV2[0]).safeApprove(routerV2, amountInV2);
        IUniswapV2Router02(routerV2).swapExactTokensForTokens(
            amountInV2,
            amountOutMinV2,
            pathV2,
            address(this),
            block.timestamp + 300
        );

        // -------- Repay flash loan ----------
        uint totalDebt = amount + premium;
        IERC20(asset).safeApprove(address(lendingPool), totalDebt);

        // Profit left in contract
        return true;
    }

    // Emergency withdraw
    function withdraw(address token) external onlyOwner {
        uint balance = IERC20(token).balanceOf(address(this));
        IERC20(token).safeTransfer(owner, balance);
    }
}
