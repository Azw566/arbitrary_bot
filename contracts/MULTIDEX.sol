// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/**
 * @title MultiDexArbitrage
 * @notice Contrat d'arbitrage supportant Uniswap V2, V3 et Curve avec Flash Loans
 * @dev Utilise Aave V3 pour les flash loans
 */

// ============================================================================
// INTERFACES
// ============================================================================

interface IERC20 {
    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address recipient, uint256 amount) external returns (bool);
    function allowance(address owner, address spender) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
    function transferFrom(address sender, address recipient, uint256 amount) external returns (bool);
}

// Interface Uniswap V2 Router
interface IUniswapV2Router02 {
    function swapExactTokensForTokens(
        uint amountIn,
        uint amountOutMin,
        address[] calldata path,
        address to,
        uint deadline
    ) external returns (uint[] memory amounts);
    
    function getAmountsOut(uint amountIn, address[] calldata path) 
        external view returns (uint[] memory amounts);
}

// Interface Uniswap V3 Router
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

    function exactInputSingle(ExactInputSingleParams calldata params)
        external payable returns (uint256 amountOut);
}

// Interface Curve Pool
interface ICurvePool {
    function exchange(
        int128 i,
        int128 j,
        uint256 dx,
        uint256 min_dy
    ) external returns (uint256);
    
    function get_dy(
        int128 i,
        int128 j,
        uint256 dx
    ) external view returns (uint256);
}

// Interface Aave V3 Pool pour Flash Loans
interface IPoolV3 {
    function flashLoan(
        address receiverAddress,
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata modes,
        address onBehalfOf,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

// Interface pour recevoir les flash loans
interface IFlashLoanReceiverV3 {
    function executeOperation(
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata premiums,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

// ============================================================================
// CONTRAT PRINCIPAL
// ============================================================================

contract MultiDexArbitrage is IFlashLoanReceiverV3 {
    
    // ========================================================================
    // VARIABLES D'ÉTAT
    // ========================================================================
    
    address public owner;
    address public immutable AAVE_POOL;  // Aave V3 Pool
    
    // Adresses des routers/pools (Mainnet Ethereum)
    address public constant UNISWAP_V2_ROUTER = 0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D;
    address public constant UNISWAP_V3_ROUTER = 0xE592427A0AEce92De3Edee1F18E0157C05861564;
    
    // Compteurs pour statistiques
    uint256 public totalArbitrages;
    uint256 public totalProfit;
    
    // Protection contre la réentrance
    bool private locked;
    
    // ========================================================================
    // ENUMS ET STRUCTS
    // ========================================================================
    
    enum DexType {
        UNISWAP_V2,
        UNISWAP_V3,
        CURVE
    }
    
    struct SwapParams {
        DexType dexType;
        address router;      // Adresse du router/pool
        address tokenIn;
        address tokenOut;
        uint256 amountIn;
        uint256 minAmountOut;
        bytes extraData;     // Données supplémentaires (fee V3, indices Curve)
    }
    
    struct ArbitrageParams {
        SwapParams[] swaps;  // Liste des swaps à effectuer
        uint256 expectedProfit;
        uint256 deadline;
    }
    
    // ========================================================================
    // EVENTS
    // ========================================================================
    
    event ArbitrageExecuted(
        address indexed tokenBorrowed,
        uint256 amountBorrowed,
        uint256 profit,
        uint256 timestamp
    );
    
    event SwapExecuted(
        DexType indexed dexType,
        address indexed tokenIn,
        address indexed tokenOut,
        uint256 amountIn,
        uint256 amountOut
    );
    
    event ProfitWithdrawn(
        address indexed token,
        uint256 amount,
        address indexed to
    );
    
    // ========================================================================
    // MODIFIERS
    // ========================================================================
    
    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }
    
    modifier noReentrant() {
        require(!locked, "No reentrancy");
        locked = true;
        _;
        locked = false;
    }
    
    // ========================================================================
    // CONSTRUCTOR
    // ========================================================================
    
    constructor(address _aavePool) {
        owner = msg.sender;
        AAVE_POOL = _aavePool;
    }
    
    // ========================================================================
    // FONCTIONS PRINCIPALES
    // ========================================================================
    
    /**
     * @notice Démarre un arbitrage avec flash loan
     * @param token Adresse du token à emprunter
     * @param amount Montant à emprunter
     * @param params Paramètres de l'arbitrage (encodés)
     */
    function startFlashLoanArbitrage(
        address token,
        uint256 amount,
        bytes calldata params
    ) external onlyOwner noReentrant {
        address[] memory assets = new address[](1);
        assets[0] = token;
        
        uint256[] memory amounts = new uint256[](1);
        amounts[0] = amount;
        
        uint256[] memory modes = new uint256[](1);
        modes[0] = 0;  // 0 = pas de dette, remboursement immédiat
        
        IPoolV3(AAVE_POOL).flashLoan(
            address(this),
            assets,
            amounts,
            modes,
            address(this),
            params,
            0  // referralCode
        );
    }
    
    /**
     * @notice Callback appelé par Aave lors du flash loan
     * @dev Cette fonction exécute la stratégie d'arbitrage
     */
    function executeOperation(
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata premiums,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        require(msg.sender == AAVE_POOL, "Caller must be Aave Pool");
        require(initiator == address(this), "Initiator must be this contract");
        
        // Décoder les paramètres
        ArbitrageParams memory arbParams = abi.decode(params, (ArbitrageParams));
        
        // Vérifier le deadline
        require(block.timestamp <= arbParams.deadline, "Transaction expired");
        
        // Solde initial
        uint256 balanceInitial = IERC20(assets[0]).balanceOf(address(this));
        
        // Exécuter les swaps
        for (uint256 i = 0; i < arbParams.swaps.length; i++) {
            _executeSwap(arbParams.swaps[i]);
        }
        
        // Solde final
        uint256 balanceFinal = IERC20(assets[0]).balanceOf(address(this));
        
        // Calculer le profit (après remboursement du flash loan)
        uint256 amountOwed = amounts[0] + premiums[0];
        require(balanceFinal >= amountOwed, "Insufficient funds to repay");
        
        uint256 profit = balanceFinal - amountOwed;
        require(profit >= arbParams.expectedProfit, "Profit too low");
        
        // Approuver le remboursement
        IERC20(assets[0]).approve(AAVE_POOL, amountOwed);
        
        // Mettre à jour les statistiques
        totalArbitrages++;
        totalProfit += profit;
        
        emit ArbitrageExecuted(assets[0], amounts[0], profit, block.timestamp);
        
        return true;
    }
    
    /**
     * @notice Exécute un swap sur le DEX spécifié
     * @param params Paramètres du swap
     */
    function _executeSwap(SwapParams memory params) internal {
        uint256 balanceBefore = IERC20(params.tokenOut).balanceOf(address(this));
        
        if (params.dexType == DexType.UNISWAP_V2) {
            _swapUniswapV2(params);
        } else if (params.dexType == DexType.UNISWAP_V3) {
            _swapUniswapV3(params);
        } else if (params.dexType == DexType.CURVE) {
            _swapCurve(params);
        } else {
            revert("Unknown DEX type");
        }
        
        uint256 balanceAfter = IERC20(params.tokenOut).balanceOf(address(this));
        uint256 amountOut = balanceAfter - balanceBefore;
        
        require(amountOut >= params.minAmountOut, "Insufficient output amount");
        
        emit SwapExecuted(
            params.dexType,
            params.tokenIn,
            params.tokenOut,
            params.amountIn,
            amountOut
        );
    }
    
    /**
     * @notice Swap sur Uniswap V2
     */
    function _swapUniswapV2(SwapParams memory params) internal {
        // Approuver le router
        IERC20(params.tokenIn).approve(params.router, params.amountIn);
        
        // Construire le path
        address[] memory path = new address[](2);
        path[0] = params.tokenIn;
        path[1] = params.tokenOut;
        
        // Exécuter le swap
        IUniswapV2Router02(params.router).swapExactTokensForTokens(
            params.amountIn,
            params.minAmountOut,
            path,
            address(this),
            block.timestamp
        );
    }
    
    /**
     * @notice Swap sur Uniswap V3
     */
    function _swapUniswapV3(SwapParams memory params) internal {
        // Décoder le fee tier (500, 3000, 10000)
        uint24 fee = abi.decode(params.extraData, (uint24));
        
        // Approuver le router
        IERC20(params.tokenIn).approve(params.router, params.amountIn);
        
        // Construire les paramètres du swap
        ISwapRouter.ExactInputSingleParams memory swapParams = ISwapRouter.ExactInputSingleParams({
            tokenIn: params.tokenIn,
            tokenOut: params.tokenOut,
            fee: fee,
            recipient: address(this),
            deadline: block.timestamp,
            amountIn: params.amountIn,
            amountOutMinimum: params.minAmountOut,
            sqrtPriceLimitX96: 0
        });
        
        // Exécuter le swap
        ISwapRouter(params.router).exactInputSingle(swapParams);
    }
    
    /**
     * @notice Swap sur Curve
     */
    function _swapCurve(SwapParams memory params) internal {
        // Décoder les indices (i, j)
        (int128 i, int128 j) = abi.decode(params.extraData, (int128, int128));
        
        // Approuver le pool
        IERC20(params.tokenIn).approve(params.router, params.amountIn);
        
        // Exécuter le swap
        ICurvePool(params.router).exchange(
            i,
            j,
            params.amountIn,
            params.minAmountOut
        );
    }
    
    // ========================================================================
    // FONCTIONS D'ADMINISTRATION
    // ========================================================================
    
    /**
     * @notice Retirer les profits
     */
    function withdrawToken(address token, uint256 amount) external onlyOwner {
        require(amount > 0, "Amount must be > 0");
        uint256 balance = IERC20(token).balanceOf(address(this));
        require(balance >= amount, "Insufficient balance");
        
        IERC20(token).transfer(owner, amount);
        
        emit ProfitWithdrawn(token, amount, owner);
    }
    
    /**
     * @notice Retirer l'ETH
     */
    function withdrawETH() external onlyOwner {
        uint256 balance = address(this).balance;
        require(balance > 0, "No ETH to withdraw");
        
        payable(owner).transfer(balance);
    }
    
    /**
     * @notice Transférer la propriété
     */
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Invalid address");
        owner = newOwner;
    }
    
    /**
     * @notice Approuver un token pour un spender (utile pour debugging)
     */
    function approveToken(address token, address spender, uint256 amount) external onlyOwner {
        IERC20(token).approve(spender, amount);
    }
    
    // ========================================================================
    // FONCTIONS DE LECTURE
    // ========================================================================
    
    /**
     * @notice Simuler un swap Uniswap V2 (lecture seule)
     */
    function quoteUniswapV2(
        address router,
        address tokenIn,
        address tokenOut,
        uint256 amountIn
    ) external view returns (uint256 amountOut) {
        address[] memory path = new address[](2);
        path[0] = tokenIn;
        path[1] = tokenOut;
        
        uint[] memory amounts = IUniswapV2Router02(router).getAmountsOut(amountIn, path);
        return amounts[1];
    }
    
    /**
     * @notice Simuler un swap Curve (lecture seule)
     */
    function quoteCurve(
        address pool,
        int128 i,
        int128 j,
        uint256 amountIn
    ) external view returns (uint256 amountOut) {
        return ICurvePool(pool).get_dy(i, j, amountIn);
    }
    
    /**
     * @notice Obtenir le solde d'un token
     */
    function getTokenBalance(address token) external view returns (uint256) {
        return IERC20(token).balanceOf(address(this));
    }
    
    // ========================================================================
    // FALLBACK
    // ========================================================================
    
    receive() external payable {}
    fallback() external payable {}
}