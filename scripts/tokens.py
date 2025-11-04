
# ============================================================================
# STABLECOINS (Les plus liquides)
# ============================================================================

STABLECOINS = {
    "USDT": {
        "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "decimals": 6,
        "name": "Tether USD",
        "coingecko_id": "tether"
    },
    "USDC": {
        "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "decimals": 6,
        "name": "USD Coin",
        "coingecko_id": "usd-coin"
    },
    "DAI": {
        "address": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        "decimals": 18,
        "name": "Dai Stablecoin",
        "coingecko_id": "dai"
    },
    "BUSD": {
        "address": "0x4Fabb145d64652a948d72533023f6E7A623C7C53",
        "decimals": 18,
        "name": "Binance USD",
        "coingecko_id": "binance-usd"
    },
    "FRAX": {
        "address": "0x853d955aCEf822Db058eb8505911ED77F175b99e",
        "decimals": 18,
        "name": "Frax",
        "coingecko_id": "frax"
    },
    "TUSD": {
        "address": "0x0000000000085d4780B73119b644AE5ecd22b376",
        "decimals": 18,
        "name": "TrueUSD",
        "coingecko_id": "true-usd"
    },
    "LUSD": {
        "address": "0x5f98805A4E8be255a32880FDeC7F6728C6568bA0",
        "decimals": 18,
        "name": "Liquity USD",
        "coingecko_id": "liquity-usd"
    }
}

# ============================================================================
# WRAPPED ASSETS
# ============================================================================

WRAPPED = {
    "WETH": {
        "address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "decimals": 18,
        "name": "Wrapped Ether",
        "coingecko_id": "weth"
    },
    "WBTC": {
        "address": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
        "decimals": 8,
        "name": "Wrapped Bitcoin",
        "coingecko_id": "wrapped-bitcoin"
    },
    "stETH": {
        "address": "0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84",
        "decimals": 18,
        "name": "Lido Staked Ether",
        "coingecko_id": "staked-ether"
    },
    "wstETH": {
        "address": "0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0",
        "decimals": 18,
        "name": "Wrapped Staked Ether",
        "coingecko_id": "wrapped-steth"
    },
    "rETH": {
        "address": "0xae78736Cd615f374D3085123A210448E74Fc6393",
        "decimals": 18,
        "name": "Rocket Pool ETH",
        "coingecko_id": "rocket-pool-eth"
    },
    "cbETH": {
        "address": "0xBe9895146f7AF43049ca1c1AE358B0541Ea49704",
        "decimals": 18,
        "name": "Coinbase Wrapped Staked ETH",
        "coingecko_id": "coinbase-wrapped-staked-eth"
    },
    "frxETH": {
        "address": "0x5E8422345238F34275888049021821E8E08CAa1f",
        "decimals": 18,
        "name": "Frax Ether",
        "coingecko_id": "frax-ether"
    }
}

# ============================================================================
# TOP DeFi TOKENS
# ============================================================================

DEFI_TOKENS = {
    "UNI": {
        "address": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
        "decimals": 18,
        "name": "Uniswap",
        "coingecko_id": "uniswap"
    },
    "LINK": {
        "address": "0x514910771AF9Ca656af840dff83E8264EcF986CA",
        "decimals": 18,
        "name": "Chainlink",
        "coingecko_id": "chainlink"
    },
    "AAVE": {
        "address": "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9",
        "decimals": 18,
        "name": "Aave",
        "coingecko_id": "aave"
    },
    "MKR": {
        "address": "0x9f8F72aA9304c8B593d555F12eF6589cC3A579A2",
        "decimals": 18,
        "name": "Maker",
        "coingecko_id": "maker"
    },
    "CRV": {
        "address": "0xD533a949740bb3306d119CC777fa900bA034cd52",
        "decimals": 18,
        "name": "Curve DAO Token",
        "coingecko_id": "curve-dao-token"
    },
    "LDO": {
        "address": "0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32",
        "decimals": 18,
        "name": "Lido DAO",
        "coingecko_id": "lido-dao"
    },
    "SNX": {
        "address": "0xC011a73ee8576Fb46F5E1c5751cA3B9Fe0af2a6F",
        "decimals": 18,
        "name": "Synthetix Network Token",
        "coingecko_id": "synthetix-network-token"
    },
    "COMP": {
        "address": "0xc00e94Cb662C3520282E6f5717214004A7f26888",
        "decimals": 18,
        "name": "Compound",
        "coingecko_id": "compound-governance-token"
    },
    "SUSHI": {
        "address": "0x6B3595068778DD592e39A122f4f5a5cF09C90fE2",
        "decimals": 18,
        "name": "SushiSwap",
        "coingecko_id": "sushi"
    },
    "BAL": {
        "address": "0xba100000625a3754423978a60c9317c58a424e3D",
        "decimals": 18,
        "name": "Balancer",
        "coingecko_id": "balancer"
    },
    "1INCH": {
        "address": "0x111111111117dC0aa78b770fA6A738034120C302",
        "decimals": 18,
        "name": "1inch",
        "coingecko_id": "1inch"
    }
}

# ============================================================================
# AUTRES TOKENS POPULAIRES
# ============================================================================

OTHER_TOKENS = {
    "SHIB": {
        "address": "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE",
        "decimals": 18,
        "name": "Shiba Inu",
        "coingecko_id": "shiba-inu"
    },
    "MATIC": {
        "address": "0x7D1AfA7B718fb893dB30A3aBc0Cfc608AaCfeBB0",
        "decimals": 18,
        "name": "Polygon",
        "coingecko_id": "matic-network"
    },
    "APE": {
        "address": "0x4d224452801ACEd8B2F0aebE155379bb5D594381",
        "decimals": 18,
        "name": "ApeCoin",
        "coingecko_id": "apecoin"
    },
    "GRT": {
        "address": "0xc944E90C64B2c07662A292be6244BDf05Cda44a7",
        "decimals": 18,
        "name": "The Graph",
        "coingecko_id": "the-graph"
    },
    "ENS": {
        "address": "0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72",
        "decimals": 18,
        "name": "Ethereum Name Service",
        "coingecko_id": "ethereum-name-service"
    }
}

# ============================================================================
# DICTIONNAIRE COMPLET
# ============================================================================

ALL_TOKENS = {
    **STABLECOINS,
    **WRAPPED,
    **DEFI_TOKENS,
    **OTHER_TOKENS
}

# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================

def get_token_address(symbol: str) -> str:
    """Récupère l'adresse d'un token par son symbole"""
    symbol = symbol.upper()
    if symbol in ALL_TOKENS:
        return ALL_TOKENS[symbol]["address"]
    raise ValueError(f"Token {symbol} not found")