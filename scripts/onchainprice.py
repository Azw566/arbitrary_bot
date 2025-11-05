import web3
from web3 import Web3
import psycopg2
import psycopg2.extras
import time
import concurrent.futures
from typing import List, Dict
import pandas as pd
import requests
from solcx import compile_standard, install_solc
import json
import base64
from decimal import Decimal
from datetime import datetime
import sqlite3
import os
from dotenv import load_dotenv
from tokens import ALL_TOKENS, get_token_address
import asyncio
import eth_abi

load_dotenv(r"C:\Users\telem\Desktop\Rabby\.env", encoding='latin-1')

##############################################################################################################################################################
# CONFIGURATION GLOBALE ET CONSTANTES
##############################################################################################################################################################

MEMPOOL_SURVEILLANCE_ENABLED = False

ACTUAL_PRICES = {}

WALLET_BALANCE = 0.0  # Balance totale du wallet en USD pour l'analyse de capacité d'investissement

AMOUNT_TOKEN = os.getenv('AMOUNT_TOKEN', '1000')

INTERVAL_PAUSE = 0

NB_PAIR=20

CHUNK_SIZE = 100

DRY_RUN=os.getenv('DRY_RUN', 'true')

AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2" 

PRIVATE_KEY = os.getenv("PRIVATE_KEY")

DRY_RUN=os.getenv("DRY_RUN")

PROFIT_NET = float(os.getenv('PROFIT_NET', '0.005')) 

CHAIN_ID = int(os.getenv("CHAIN_ID", 11155111))  # ID de la chaîne (11155111 pour Sepolia par défaut)

RPC_PROVIDERS = [
    "https://mainnet.infura.io/v3/b23f45e3a93e470e8728a7f61baa0295",
    "https://mainnet.infura.io/v3/2ba89ba9e1414a768b9b95bb133b06fe",
    "https://rpc.ankr.com/eth",
    "https://eth.api.onfinality.io/public",
    "https://1rpc.io/eth"
]

# Buffer pour récup les infos des tokens
TOKEN_INFO_CACHE = {}

# Flash loan fee Aave V3 = 0.09%
AAVE_FLASH_LOAN_FEE = 0.0009

# Slippage tolérance par défaut (0.5%)
DEFAULT_SLIPPAGE_TOLERANCE = 0.005

# Gas estimations plus réalistes
GAS_ESTIMATES = {
    'V2_SWAP': 150000,
    'V3_SWAP': 180000,
    'FLASH_LOAN': 200000,
    'TOTAL_ARBITRAGE': 600000  # Estimation totale
}

# Limite de perte maximale (5% du capital)
MAX_LOSS_THRESHOLD = 0.05

# Adresses des DEX et contrats importants
UNISWAP_V2_ROUTER_ADDRESS = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
SUSHISWAP_ROUTER_ADDRESS = "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F"
UNISWAP_V3_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"  
UNISWAP_V3_ROUTER02 = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"
UNISWAP_V3_QUOTER_ADDRESS = "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6"
CURVE_REGISTRY_ADDRESS = "0x0000000022D53366457F9d5E68Ec105046FC4383"
BALANCER_VAULT_ADDRESS = "0xBA12222222228d8Ba445958a75a0704d566BF2C8" # à implémenter

UNISWAP_V2 = os.getenv('UNISWAP_V2')
UNISWAP_V3 = os.getenv('UNISWAP_V3')
SUSHISWAP = os.getenv('SUSHISWAP')
CURVE = os.getenv('CURVE')

# Contracts Curve principaux (adresse => nom)
CURVE_POOLS = {
    '0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7': '3pool (USDT/USDC/DAI)',
    '0xDC24316b9AE028F1497c275EB9192a3Ea0f67022': 'stETH/ETH',
    '0xD51a44d3FaE010294C616388b506AcdA1bfAAE46': 'Tricrypto2',
    '0xDcEF968d416a41Cdac0ED8702fAC8128A64241A2': 'FRAX/USDC',
    '0xA5407eAE9Ba41422680e2e00537571bcC53efBfD': 'sUSD',
    '0x06364f10B501e868329afBc005b3492902d6C763': 'PAX',
}

WATCH_TOKENS = []

# Adresse du contrat Multicall3 (déployé sur Ethereum mainnet) - env override
MULTICALL3_ADDRESS = os.getenv('MULTICALL3_ADDRESS', "0xcA11bde05977b3631167028862bE2a173976CA11")

##############################################################################################################################################################
# ABIs 
##############################################################################################################################################################

# Primary web3 instance selected at startup to avoid reconnecting on every call
PRIMARY_W3 = None

# ABI pour les contrats Uniswap V2 Pair
UNISWAP_V2_PAIR_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"internalType": "uint112", "name": "_reserve0", "type": "uint112"},
            {"internalType": "uint112", "name": "_reserve1", "type": "uint112"},
            {"internalType": "uint32", "name": "_blockTimestampLast", "type": "uint32"}
        ],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token1",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    }
]

# ABI pour les contrats Uniswap V3 Pool
UNISWAP_V3_POOL_ABI = [
    {
        "inputs": [],
        "name": "liquidity",
        "outputs": [{"internalType": "uint128", "name": "", "type": "uint128"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "fee",
        "outputs": [{"internalType": "uint24", "name": "", "type": "uint24"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "tickSpacing",
        "outputs": [{"internalType": "int24", "name": "", "type": "int24"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# ABI pour le Router Uniswap V2 (getAmountsOut)
UNISWAP_V2_ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"}
        ],
        "name": "getAmountsOut",
        "outputs": [
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

# ABI pour le Quoter Uniswap V3 (quoteExactInputSingle)
UNISWAP_V3_QUOTER_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenIn", "type": "address"},
            {"internalType": "address", "name": "tokenOut", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"}
        ],
        "name": "quoteExactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

# ABI pour les contrats ERC20 (pour obtenir les décimales)
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    }
]

CURVE_POOL_ABI = [
    {
        "name": "get_dy",
        "inputs": [
            {"name": "i", "type": "int128"},
            {"name": "j", "type": "int128"},
            {"name": "dx", "type": "uint256"}
        ],
        "outputs": [{"name": "dy", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "name": "coins",
        "inputs": [{"name": "arg0", "type": "uint256"}],
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "name": "balances",
        "inputs": [{"name": "arg0", "type": "uint256"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# ABI pour Multicall3 - permet de grouper plusieurs appels en un seul
MULTICALL3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "target", "type": "address"},
                    {"internalType": "bytes", "name": "callData", "type": "bytes"}
                ],
                "internalType": "struct Multicall3.Call[]",
                "name": "calls",
                "type": "tuple[]"
            }
        ],
        "name": "aggregate",
        "outputs": [
            {"internalType": "uint256", "name": "blockNumber", "type": "uint256"},
            {"internalType": "bytes[]", "name": "returnData", "type": "bytes[]"}
        ],
        "stateMutability": "payable",
        "type": "function"
    },
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "target", "type": "address"},
                    {"internalType": "bool", "name": "allowFailure", "type": "bool"},
                    {"internalType": "bytes", "name": "callData", "type": "bytes"}
                ],
                "internalType": "struct Multicall3.Call3[]",
                "name": "calls",
                "type": "tuple[]"
            }
        ],
        "name": "aggregate3",
        "outputs": [
            {
                "components": [
                    {"internalType": "bool", "name": "success", "type": "bool"},
                    {"internalType": "bytes", "name": "returnData", "type": "bytes"}
                ],
                "internalType": "struct Multicall3.Result[]",
                "name": "returnData",
                "type": "tuple[]"
            }
        ],
        "stateMutability": "payable",
        "type": "function"
    }
]

##############################################################################################################################################################
# FONCTIONS UTILITAIRES
##############################################################################################################################################################

def get_curve_price_direct(w3, pool_addr, i, j, amount_in):
    """
    Calcule le prix Curve via get_dy() on-chain
    Gère les adresses checksummées et non-checksummées
    """
    pool_addr = Web3.to_checksum_address(pool_addr)
    pool = w3.eth.contract(address=pool_addr, abi=CURVE_POOL_ABI)
    dy = pool.functions.get_dy(i, j, amount_in).call()
    return dy

def get_web3():   
    """Retourne un fournisseur Web3 disponible."""
    global PRIMARY_W3
    if PRIMARY_W3 is not None:
        return PRIMARY_W3

    last_exc = None
    for rpc in RPC_PROVIDERS:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
            # web3.py uses isConnected() in some versions and is_connected() in others
            connected = False
            try:
                connected = w3.is_connected()
            except Exception:
                try:
                    connected = w3.isConnected()
                except Exception:
                    connected = False

            if connected:
                PRIMARY_W3 = w3
                print(f"Connected to RPC provider: {rpc}")
                return PRIMARY_W3
        except Exception as e:
            last_exc = e
            print(f"Erreur avec {rpc}: {e}")
            continue

    raise Exception("Aucun fournisseur RPC disponible") from last_exc

def safe_serialize(obj):
    """
    Recursively prepare an object for JSON/DB insertion.
    - bytes -> {'__base64': '...'}
    - Decimal -> float
    - strings: ensure valid LATIN-1 by replacing invalid sequences
    - dict/list/tuple -> recursively processed
    """
    if obj is None:
        return None

    if isinstance(obj, (bytes, bytearray)):
        try:
            b64 = base64.b64encode(bytes(obj)).decode('ascii')
            return {'__base64': b64}
        except Exception:
            return {'__base64': ''}

    if isinstance(obj, Decimal):
        try:
            return float(obj)
        except Exception:
            return None

    if isinstance(obj, (int, float, bool)):
        return obj

    # CORRECTION ICI : Utiliser latin-1 au lieu de utf-8
    if isinstance(obj, str):
        try:
            # Essayer d'encoder/décoder en latin-1
            return obj.encode('latin-1', errors='replace').decode('latin-1')
        except Exception:
            # Fallback : nettoyer les caractères problématiques
            return ''.join(c if ord(c) < 256 else '?' for c in obj)

    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            try:
                key = k if isinstance(k, str) else str(k)
                # S'assurer que la clé est compatible latin-1
                key = key.encode('latin-1', errors='replace').decode('latin-1')
            except Exception:
                key = repr(k)
            out[key] = safe_serialize(v)
        return out

    if isinstance(obj, (list, tuple, set)):
        return [safe_serialize(v) for v in obj]

    try:
        return str(obj).encode('latin-1', errors='replace').decode('latin-1')
    except Exception:
        return None

def safe_psycopg2_connect(db_config: Dict):
    """Robust psycopg2.connect wrapper with LATIN-1 encoding support.
    
    Gère correctement les mots de passe et chemins avec caractères accentués.
    """
    if db_config is None:
        db_config = {}

    # Nettoyer et convertir toutes les valeurs en strings sûres
    clean = {}
    for k, v in db_config.items():
        if v is None:
            continue
            
        if isinstance(v, (bytes, bytearray)):
            try:
                clean[k] = v.decode('latin-1', errors='replace')
            except Exception:
                clean[k] = str(v)
        elif isinstance(v, str):
            # S'assurer que la string est compatible
            try:
                # Tester l'encodage
                v.encode('latin-1')
                clean[k] = v
            except:
                clean[k] = v.encode('latin-1', errors='replace').decode('latin-1')
        else:
            clean[k] = v

    # PostgreSQL utilise 'dbname' pas 'database' dans les DSN strings
    if 'database' in clean:
        clean['dbname'] = clean.pop('database')
    
    # Retirer 'options' du dict car on va le gérer séparément
    client_encoding_option = clean.pop('options', None)

    # PREMIÈRE TENTATIVE : Connexion directe avec paramètres
    try:
        conn = psycopg2.connect(**clean)
        # Forcer l'encodage client après connexion
        try:
            conn.set_client_encoding('LATIN1')
        except:
            pass
        return conn
    except Exception as e:
        print(f"Tentative 1 échouée: {e}")

    # DEUXIÈME TENTATIVE : Construction manuelle du DSN
    try:
        dsn_parts = []
        
        # Ordre important pour PostgreSQL DSN
        dsn_keys = ['host', 'port', 'dbname', 'user', 'password']
        
        for key in dsn_keys:
            if key in clean and clean[key] is not None:
                val = str(clean[key])
                # Échapper les apostrophes et backslashes
                val = val.replace('\\', '\\\\').replace("'", "\\'")
                dsn_parts.append(f"{key}='{val}'")
        
        # Ajouter les options d'encodage
        dsn_parts.append("client_encoding='LATIN1'")
        
        dsn = ' '.join(dsn_parts)
        print(f"DSN construit: {dsn.replace(clean.get('password', 'XXX'), '***')}")
        
        conn = psycopg2.connect(dsn)
        return conn
        
    except Exception as e:
        print(f"Tentative 2 échouée: {e}")
        
    # TROISIÈME TENTATIVE : DSN ultra-simple
    try:
        simple_dsn = f"host={clean.get('host', 'localhost')} "
        simple_dsn += f"port={clean.get('port', 5432)} "
        simple_dsn += f"dbname={clean.get('dbname', 'arbitrage')} "
        simple_dsn += f"user={clean.get('user', 'postgres')} "
        
        # Password séparé pour éviter problèmes d'échappement
        password = clean.get('password', '')
        
        conn = psycopg2.connect(simple_dsn, password=password)
        try:
            conn.set_client_encoding('LATIN1')
        except:
            pass
        return conn
        
    except Exception as e:
        print(f"Tentative 3 échouée: {e}")
        raise Exception(f"Impossible de se connecter à PostgreSQL: {e}")

def detect_pool_version(pool_address):
    """
    Détecte automatiquement si un pool est V2 ou V3
    """
    try:
        pool_address = Web3.to_checksum_address(pool_address)
        # Essayer d'appeler une fonction spécifique à V3
        w3 = get_web3()
        pool_contract = w3.eth.contract(address=pool_address, abi=UNISWAP_V3_POOL_ABI)
        pool_contract.functions.slot0().call()
        return 'V3'
    except:
        try:
            # Essayer d'appeler une fonction spécifique à V2
            pool_contract = w3.eth.contract(address=pool_address, abi=UNISWAP_V2_PAIR_ABI)
            pool_contract.functions.getReserves().call()
            return 'V2'
        except:
            return 'UNKNOWN'

#############################################################################################################################################################
# GESTION DU WALLET 
#############################################################################################################################################################

def get_token_balance(w3, wallet_address: str, token_address: str) -> dict:
    """
    Récupère la balance d'un token ERC20 spécifique
    
    Args:
        w3: Instance Web3
        wallet_address: Adresse du wallet
        token_address: Adresse du contrat du token
    
    Returns:
        dict: Informations sur la balance du token
    """
    try:
        # Créer le contrat ERC20
        token_contract = w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_ABI  # Déjà défini dans votre code ligne 222
        )
        
        # Récupérer les informations du token
        symbol = token_contract.functions.symbol().call()
        decimals = token_contract.functions.decimals().call()
        balance_raw = token_contract.functions.balanceOf(
            Web3.to_checksum_address(wallet_address)
        ).call()
        
        # Convertir en unités lisibles
        balance = balance_raw / (10 ** decimals)
        
        return {
            'token_address': token_address,
            'symbol': symbol,
            'decimals': decimals,
            'balance_raw': balance_raw,
            'balance': balance,
            'success': True
        }
        
    except Exception as e:
        return {
            'token_address': token_address,
            'error': str(e),
            'success': False
        }

def get_all_token_balances(w3, wallet_address: str, token_list: list) -> list:
    balances = []
    
    for token_address in token_list:
        balance_info = get_token_balance(w3, wallet_address, token_address)
        if balance_info['success'] and balance_info['balance'] > 0:
            balances.append(balance_info)
    
    return balances

def analyze_investment_capacity(w3, wallet_info: dict) -> dict:
    print("\n" + "="*80)
    print(" ANALYSE DE LA CAPACITÉ D'INVESTISSEMENT")
    print("="*80)
    
    print("\n 1. BALANCE ETH")
    balance_check = check_wallet_balance(w3, wallet_info['address'])
    
    if 'error' in balance_check:
        print(f" Erreur: {balance_check['error']}")
        return {'error': balance_check['error']}
    
    eth_balance = balance_check['balance_eth']
    print(f"   Balance actuelle: {eth_balance:.6f} ETH")
    
    eth_price = get_eth_price_usd()
    gas_reserve_eth = balance_check['required_eth'] * 5  # Réserve pour 5 transactions
    available_eth = max(0, eth_balance - gas_reserve_eth)
    
    print(f"   Réserve gas (5 tx): {gas_reserve_eth:.6f} ETH")
    print(f"   ETH disponible: {available_eth:.6f} ETH (${available_eth * eth_price:.2f})")
    
    print("\n 2. BALANCES TOKENS ERC20")
    token_balances = get_all_token_balances(w3, wallet_info['address'], wallet_info['tokens'])

    global WALLET_BALANCE # Variable globale pour la balance totale à investir en plus des flash loans
    
    total_value_usd = available_eth * eth_price
    WALLET_BALANCE = total_value_usd
    token_details = []
    
    for token in token_balances:
        token_price_usd = get_token_price_usd(token['symbol']) 
        token_value_usd = token['balance'] * token_price_usd
        total_value_usd += token_value_usd
        
        token_details.append({
            'symbol': token['symbol'],
            'balance': token['balance'],
            'price_usd': token_price_usd,
            'value_usd': token_value_usd
        })
        
        print(f"   {token['symbol']}: {token['balance']:.4f} (${token_value_usd:.2f})")
    
    print("\n 3. CAPACITÉ D'INVESTISSEMENT")
    
    max_single_trade_pct = 0.60  # 60% max du capital total
    max_single_trade_usd = total_value_usd * max_single_trade_pct
    
    optimal_trade_pct = 0.30  # 30% du capital
    optimal_trade_usd = total_value_usd * optimal_trade_pct
    
    print("\n 4. RECOMMANDATIONS")
    
    recommendations = []
    
    if eth_balance < gas_reserve_eth:
        recommendations.append("  CRITIQUE: Balance ETH insuffisante pour le gas. Rechargez au moins {:.6f} ETH".format(
            gas_reserve_eth - eth_balance
        ))
    
    if total_value_usd < 500:
        recommendations.append("  Capital faible (<$500). Risque de rentabilité limitée à cause des frais de gas.")
    
    if len(token_balances) == 0:
        recommendations.append(" Aucun token détecté. Pour l'arbitrage, ayez au moins WETH ou stablecoins.")
    
    if total_value_usd > 10000:
        recommendations.append(" Capital suffisant pour des arbitrages rentables.")
    
    for rec in recommendations:
        print(f"   {rec}")
    
    print("\n" + "="*80)
    
    return {
        'eth_balance': eth_balance,
        'eth_available': available_eth,
        'token_balances': token_details,
        'total_value_usd': total_value_usd,
        'optimal_trade_usd': optimal_trade_usd,
        'max_trade_usd': max_single_trade_usd,
        'gas_reserve_eth': gas_reserve_eth,
        'recommendations': recommendations,
        'is_ready_for_trading': eth_balance >= gas_reserve_eth and total_value_usd >= 500
    }

def check_wallet_balance(w3, wallet_address: str, 
                        gas_units: int = 600000,
                        gas_price_multiplier: float = 1,
                        safety_multiplier: float = 1.2) -> dict:
    """
    Vérifie si le wallet a suffisamment d'ETH pour couvrir les frais de gas
    """
    try:
        balance_wei = w3.eth.get_balance(wallet_address)
        balance_eth = balance_wei / (10**18)
        
        gas_price = w3.eth.gas_price
        adjusted_gas_price = int(gas_price * gas_price_multiplier)
        estimated_cost_wei = gas_units * adjusted_gas_price
        required_eth = (estimated_cost_wei / (10**18)) * safety_multiplier
        
        has_enough = balance_eth >= required_eth
        
        return {
            'balance_wei': balance_wei,
            'balance_eth': balance_eth,
            'estimated_gas_units': gas_units,
            'gas_price_wei': adjusted_gas_price,
            'gas_price_gwei': adjusted_gas_price / (10**9),
            'required_eth': required_eth,
            'has_enough': has_enough,
            'shortage_eth': max(0, required_eth - balance_eth),
            'safety_multiplier': safety_multiplier
        }
    except Exception as e:
        print(f" Erreur lors de la vérification de balance: {e}")
        return {'has_enough': False, 'error': str(e)}

def get_wallet_contents(w3=None, private_key=PRIVATE_KEY, check_tokens=True, custom_tokens=None) -> Dict:
    if w3 is None:
        RPC_PROVIDERS = [
            "https://mainnet.infura.io/v3/b23f45e3a93e470e8728a7f61baa0295",
            "https://rpc.ankr.com/eth",
            "https://eth.api.onfinality.io/public"
        ]
        for rpc in RPC_PROVIDERS:
            try:
                w3 = Web3(Web3.HTTPProvider(rpc))
                if w3.is_connected():
                    print(f" Connecté à {rpc}")
                    break
            except:
                continue
        if not w3 or not w3.is_connected():
            return {"error": "Impossible de se connecter à un RPC Ethereum"}

    try:
        account = w3.eth.account.from_key(private_key)
        address = account.address
        
        balance_wei = w3.eth.get_balance(address)
        balance_eth = balance_wei / (10**18)
        
        eth_price_usd = get_eth_price_usd()

        balance_usd = balance_eth * eth_price_usd if eth_price_usd else None
        nonce = w3.eth.get_transaction_count(address)
        
        wallet_info = {
            "address": address,
            "eth_balance": {
                "wei": balance_wei,
                "eth": balance_eth,
                "usd": balance_usd
            },
            "transaction_count": nonce,
            "tokens": [],
            "total_value_usd": balance_usd if balance_usd else 0
        }
        
        if check_tokens:
            tokens_to_check = ALL_TOKENS.copy()  
            for symbol, token_address in tokens_to_check.items():
                try:
                    token_info = get_token_balance(w3, address, token_address)
                    
                    if token_info and token_info.get('balance_raw', 0) > 0:
                        wallet_info["tokens"].append(token_info)
                        
                        if token_info.get('value_usd'):
                            wallet_info["total_value_usd"] += token_info['value_usd']
                
                except Exception as e:
                    print(f" Erreur pour {symbol}: {e}")
                    continue
            wallet_info["tokens"].sort(key=lambda x: x.get('value_usd', 0), reverse=True)
        return wallet_info
    except Exception as e:
        return {"error": f"Erreur lors de la récupération du wallet: {str(e)}"}

def get_eth_price_usd() -> float:
    return (get_token_price_usd("WETH") or 3500.0) 

def get_token_price_usd(symbol: str) -> float:
    adr = get_token_address(symbol)
    return (ACTUAL_PRICES[adr]['USDC'] if adr in ACTUAL_PRICES else None) 

def inspect_my_wallet():
    wallet_info = get_wallet_contents()
    format_wallet_report(wallet_info)
    return wallet_info

def format_wallet_report(wallet_info: Dict) -> str:
    if "error" in wallet_info:
        return f"\n ERREUR: {wallet_info['error']}\n"
    print("\n" + "="*80)
    print("CONTENU DU WALLET")
    print("="*80)
    print(f"\n Balance ETH:")
    print(f"   {wallet_info['eth_balance']['eth']:.6f} ETH")
    print(f"   {wallet_info['eth_balance']['wei']:,} wei")
    if wallet_info['eth_balance']['usd']:
        print(f"   ~${wallet_info['eth_balance']['usd']:,.2f} USD")
    
    print(f"\n Transactions:")
    print(f"   {wallet_info['transaction_count']} transactions envoyées")
    
    if wallet_info['tokens']:
        print(f"\n Tokens ERC20 ({len(wallet_info['tokens'])} tokens détectés):")
        print("")
        print(f"   {'Symbol':<12} {'Balance':<20} {'Valeur USD':<15}")
        print(f"   {'-'*12} {'-'*20} {'-'*15}")
        
        for token in wallet_info['tokens']:
            balance_str = f"{token['balance']:,.4f}".rstrip('0').rstrip('.')
            value_str = f"${token['value_usd']:,.2f}" if token.get('value_usd') else "N/A"
            print(f"   {token['symbol']:<12} {balance_str:<20} {value_str:<15}")
    else:
        print(f"\n Pas de tokens ERC20:")
    
    # Valeur totale
    print(f"\n VALEUR TOTALE ESTIMÉE:")
    if wallet_info['total_value_usd'] > 0:
        print(f"   ${wallet_info['total_value_usd']:,.2f} USD")
    else:
        print(f"   Non disponible")
    print("\n" + "="*80 + "\n") 
    
    analyze_investment_capacity(get_web3(), wallet_info)
    return None

#############################################################################################################################################################
# SIMULATION DES SWAPS ON-CHAIN
#############################################################################################################################################################

def simulate_swap_v2_router(amount_in, token_in, token_out, router_address=UNISWAP_V2_ROUTER_ADDRESS):
    """
    Simule un swap sur Uniswap V2 via getAmountsOut() du router
    Méthode recommandée pour obtenir des prix précis
    """
    try:
        w3 = get_web3()
        router_contract = w3.eth.contract(
            address=Web3.to_checksum_address(router_address),
            abi=UNISWAP_V2_ROUTER_ABI
        )
        
        # Définir le chemin de swap [token_in, token_out]
        path = [
            Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out)
        ]
        
        # Appeler getAmountsOut
        amounts = router_contract.functions.getAmountsOut(amount_in, path).call()
        amount_out = amounts[-1]  # Dernière valeur = quantité reçue
        
        return {
            'amount_in': amount_in,
            'amount_out': amount_out,
            'path': path,
            'router': router_address
        }
        
    except Exception as e:
        print(f"Erreur simulation V2 router: {e}")
        return None

def simulate_swap_v3_quoter(token_in, token_out, fee, amount_in, quoter_address=UNISWAP_V3_QUOTER_ADDRESS):
    """
    Simule un swap sur Uniswap V3 via quoteExactInputSingle() du quoter
    Méthode recommandée pour obtenir des prix précis V3
    """
    try:
        w3 = get_web3()
        quoter_contract = w3.eth.contract(
            address=Web3.to_checksum_address(quoter_address),
            abi=UNISWAP_V3_QUOTER_ABI
        )
        
        token0_info = get_token_info(token_in)
        token1_info = get_token_info(token_out)
        
        if not token0_info or not token1_info:
            print(f"Erreur: Impossible de récupérer les informations des tokens")
            return None
            
        decimals_in = token0_info['decimals']
        decimals_out = token1_info['decimals']
        
        if amount_in < 1e6:
            amount_in_wei = int(amount_in * (10 ** decimals_in))
        else: 
            amount_in_wei = int(amount_in)
        
        try:
            amount_out = quoter_contract.functions.quoteExactInputSingle(
                Web3.to_checksum_address(token_in),
                Web3.to_checksum_address(token_out),
                fee,
                amount_in_wei,
                0  
            ).call()
        except Exception as quote_error:
            print(f"Erreur quote V3: {quote_error}")
            return None
        amount_out_decimal = amount_out / (10 ** decimals_out)
        
        amount_in_decimal = amount_in if amount_in < 1e6 else amount_in / (10 ** decimals_in)
        
        if amount_in_decimal > 0:
            price = amount_out_decimal / amount_in_decimal
            if not (1e-12 <= price <= 1e6):
                print(f"Warning: Prix V3 suspect ({price}), probablement erroné")
                return None
        else:
            price = 0
        return {
            'amount_in': amount_in_decimal,
            'amount_out': amount_out_decimal,
            'amount_out_wei': amount_out,
            'price': price,
            'fee': fee,
            'quoter': quoter_address,
            'decimals_in': decimals_in,
            'decimals_out': decimals_out
        }
        
    except Exception as e:
        print(f"Erreur simulation V3 quoter: {e}")
        return None

def get_token_info(token_address): 
    """
    Récupère les informations d'un token avec gestion LATIN-1
    
    VERSION CORRIGÉE: Normalise l'adresse en checksum pour correspondre au cache
    """
    #  CORRECTION: Normaliser en checksum (comme dans batch_get_tokens_info)
    token_address = Web3.to_checksum_address(token_address)
    
    # Cas spécial ETH natif
    if token_address.lower() == "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee":
        WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        info = {
            'decimals': 18,
            'symbol': 'ETH',
            'is_native': True,
            'weth_address': WETH_ADDRESS
        }
        TOKEN_INFO_CACHE[token_address] = info
        return info
    
    #  CACHE HIT - Le token est en cache
    if token_address in TOKEN_INFO_CACHE:
        return TOKEN_INFO_CACHE[token_address]
    
    # ⚠️ CACHE MISS - Le token n'est PAS en cache, faire un appel RPC
    print(f"  CACHE MISS: Appel RPC pour {token_address[:10]}...")
    
    try:
        w3 = get_web3()
        token_contract = w3.eth.contract(address=token_address, abi=ERC20_ABI)
        decimals = token_contract.functions.decimals().call()
        
        try:
            symbol = token_contract.functions.symbol().call()
        except:
            try:
                bytes32_abi = [{
                    "constant": True,
                    "inputs": [],
                    "name": "symbol",
                    "outputs": [{"name": "", "type": "bytes32"}],
                    "payable": False,
                    "stateMutability": "view",
                    "type": "function"
                }]
                token_contract_bytes32 = w3.eth.contract(address=token_address, abi=bytes32_abi)
                symbol_bytes = token_contract_bytes32.functions.symbol().call()
                
                # Utiliser latin-1 au lieu de utf-8
                try:
                    symbol = symbol_bytes.decode('latin-1', errors='replace').rstrip('\x00')
                except:
                    # Fallback: nettoyer les bytes invalides
                    symbol = symbol_bytes.hex()[:10]
            except Exception as e2:
                symbol = f"TOKEN_{token_address[:6]}"
                print(f"Impossible de récupérer le symbole pour {token_address}, utilisation de {symbol}")
        
        info = {
            'decimals': decimals,
            'symbol': symbol
        }
        #  Ajouter au cache avec l'adresse checksum
        TOKEN_INFO_CACHE[token_address] = info
        return info
        
    except Exception as e:
        print(f" Erreur récupération info token {token_address}: {e}")
        return None

def batch_get_tokens_info(token_addresses: List[str]) -> Dict:
    """
    Récupère les informations (decimals, symbol) de plusieurs tokens en un seul multicall
    Remplit directement le cache TOKEN_INFO_CACHE
    
    VERSION CORRIGÉE: Normalise toutes les adresses en checksum pour éviter les cache miss
    
    Args:
        token_addresses: Liste des adresses de tokens
    
    Returns:
        Dict: Dictionnaire {token_address: {decimals, symbol}}
    """
    if not token_addresses:
        return {}
    
    w3 = get_web3()
    multicall = w3.eth.contract(
        address=Web3.to_checksum_address(MULTICALL3_ADDRESS),
        abi=MULTICALL3_ABI
    )
    
    # Construire les appels pour tous les tokens
    calls = []
    call_mapping = []
    
    for token_address in token_addresses:
        #  CORRECTION #1: Normaliser l'adresse en checksum DÈS LE DÉBUT
        token_checksum = Web3.to_checksum_address(token_address)
        
        # Cas spécial ETH natif
        if token_checksum.lower() == "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee":
            WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
            #  CORRECTION #2: Utiliser l'adresse checksum comme clé du cache
            TOKEN_INFO_CACHE[token_checksum] = {
                'decimals': 18,
                'symbol': 'ETH',
                'is_native': True,
                'weth_address': WETH_ADDRESS
            }
            continue
        
        #  CORRECTION #3: Vérifier le cache avec l'adresse checksum
        if token_checksum in TOKEN_INFO_CACHE:
            continue
        
        # Contract pour encoder les appels
        token_contract = w3.eth.contract(address=token_checksum, abi=ERC20_ABI)
        
        # Call 1: decimals()
        try:
            decimals_data = token_contract.functions.decimals()._encode_transaction_data()
            calls.append({"target": token_checksum, "allowFailure": True, "callData": decimals_data})
            #  CORRECTION #4: Stocker l'adresse checksum dans le mapping
            call_mapping.append(("decimals", token_checksum))
        except Exception as e:
            print(f"Erreur encodage decimals pour {token_checksum}: {e}")
            continue
        
        # Call 2: symbol() - format standard
        try:
            symbol_data = token_contract.functions.symbol()._encode_transaction_data()
            calls.append({"target": token_checksum, "allowFailure": True, "callData": symbol_data})
            #  CORRECTION #5: Stocker l'adresse checksum dans le mapping
            call_mapping.append(("symbol", token_checksum))
        except Exception as e:
            print(f"Erreur encodage symbol pour {token_checksum}: {e}")
    
    if not calls:
        return {}
    
    try:
        start_time = time.perf_counter()
        results = multicall.functions.aggregate3(calls).call()
        elapsed = time.perf_counter() - start_time
        print(f" Multicall tokens terminé en {elapsed:.3f}s")
        
        # Organiser les résultats par token
        token_data = {}
        for idx, res in enumerate(results):
            try:
                success, return_data = res
            except Exception:
                if isinstance(res, (list, tuple)) and len(res) >= 2:
                    success = res[0]
                    return_data = res[1]
                else:
                    success = False
                    return_data = None
            
            method, token_address = call_mapping[idx]
            # token_address est déjà en checksum grâce à la correction #4 et #5
            
            if token_address not in token_data:
                token_data[token_address] = {}
            
            if success and return_data:
                token_data[token_address][method] = return_data
        
        # Décoder les résultats et remplir le cache
        tokens_added = 0
        for token_address, data in token_data.items():
            # token_address est déjà en checksum
            try:
                decimals = None
                symbol = None
                
                # Décoder decimals
                if 'decimals' in data and data['decimals']:
                    try:
                        decimals = w3.codec.decode(['uint8'], data['decimals'])[0]
                    except Exception as e:
                        print(f"  Erreur décodage decimals pour {token_address}: {e}")
                
                # Décoder symbol (peut être string ou bytes32)
                if 'symbol' in data and data['symbol']:
                    try:
                        # Essayer format string
                        symbol = w3.codec.decode(['string'], data['symbol'])[0]
                    except:
                        try:
                            # Essayer format bytes32
                            symbol_bytes = w3.codec.decode(['bytes32'], data['symbol'])[0]
                            symbol = symbol_bytes.decode('latin-1', errors='replace').rstrip('\x00')
                        except Exception as e:
                            symbol = f"TOKEN_{token_address[:8]}"
                            print(f"  Impossible de décoder le symbole pour {token_address}: {e}")
                
                #  CORRECTION #6: Ajouter au cache avec l'adresse checksum comme clé
                if decimals is not None:
                    TOKEN_INFO_CACHE[token_address] = {
                        'decimals': decimals,
                        'symbol': symbol if symbol else f"TOKEN_{token_address[:8]}"
                    }
                    tokens_added += 1
                    
            except Exception as e:
                print(f" Erreur traitement token {token_address}: {e}")
                continue
        
        # 🔍 DEBUG: Afficher quelques exemples pour vérifier le format
        if tokens_added > 0:
            print(f" Exemples d'adresses en cache:")
            for addr in list(TOKEN_INFO_CACHE.keys())[:3]:
                print(f"   {addr} → {TOKEN_INFO_CACHE[addr]['symbol']}")
        
        return TOKEN_INFO_CACHE
        
    except Exception as e:
        print(f" Erreur multicall tokens: {e}")
        return {}
    
#############################################################################################################################################################
# RECUPERATION DES DONNÉES DE POOLS EN BATCH
#############################################################################################################################################################

def batch_get_pool_data(pool_addresses: List[str]) -> List[Dict]:
    """
    VERSION ULTRA-OPTIMISÉE qui batche TOUT, y compris les appels Curve get_dy()
    """
    if not pool_addresses:
        return []
    
    try:
        w3 = get_web3()
        multicall = w3.eth.contract(
            address=Web3.to_checksum_address(MULTICALL3_ADDRESS),
            abi=MULTICALL3_ABI
        )
        
        all_pool_data = {}
        
        total_start = time.perf_counter()
        
        def _build_calls_for_pools(pools_subset):
            calls = []
            call_mapping = []
            for pool_address in pools_subset:
                pool_checksum = Web3.to_checksum_address(pool_address)
                v3_contract = w3.eth.contract(address=pool_checksum, abi=UNISWAP_V3_POOL_ABI)
                v2_contract = w3.eth.contract(address=pool_checksum, abi=UNISWAP_V2_PAIR_ABI)
                curve_contract = w3.eth.contract(address=pool_checksum, abi=CURVE_POOL_ABI)

                # Calls 1-10: Identiques à avant
                # Call 1: slot0() for V3
                slot0_data = v3_contract.functions.slot0()._encode_transaction_data()
                calls.append({"target": pool_checksum, "allowFailure": True, "callData": slot0_data})
                call_mapping.append(("slot0", pool_address))

                # Call 2: getReserves() for V2
                reserves_data = v2_contract.functions.getReserves()._encode_transaction_data()
                calls.append({"target": pool_checksum, "allowFailure": True, "callData": reserves_data})
                call_mapping.append(("getReserves", pool_address))

                # Call 3: token0
                token0_data = v2_contract.functions.token0()._encode_transaction_data()
                calls.append({"target": pool_checksum, "allowFailure": True, "callData": token0_data})
                call_mapping.append(("token0", pool_address))

                # Call 4: token1
                token1_data = v2_contract.functions.token1()._encode_transaction_data()
                calls.append({"target": pool_checksum, "allowFailure": True, "callData": token1_data})
                call_mapping.append(("token1", pool_address))

                # Call 5: fee (V3)
                fee_data = v3_contract.functions.fee()._encode_transaction_data()
                calls.append({"target": pool_checksum, "allowFailure": True, "callData": fee_data})
                call_mapping.append(("fee", pool_address))

                # Call 6: liquidity (V3)
                liquidity_data = v3_contract.functions.liquidity()._encode_transaction_data()
                calls.append({"target": pool_checksum, "allowFailure": True, "callData": liquidity_data})
                call_mapping.append(("liquidity", pool_address))

                # Call 7: coins(0) for Curve
                coins0_data = curve_contract.functions.coins(0)._encode_transaction_data()
                calls.append({"target": pool_checksum, "allowFailure": True, "callData": coins0_data})
                call_mapping.append(("coins0", pool_address))

                # Call 8: coins(1) for Curve
                coins1_data = curve_contract.functions.coins(1)._encode_transaction_data()
                calls.append({"target": pool_checksum, "allowFailure": True, "callData": coins1_data})
                call_mapping.append(("coins1", pool_address))

                # Call 9: balances(0) for Curve
                balances0_data = curve_contract.functions.balances(0)._encode_transaction_data()
                calls.append({"target": pool_checksum, "allowFailure": True, "callData": balances0_data})
                call_mapping.append(("balances0", pool_address))

                # Call 10: balances(1) for Curve
                balances1_data = curve_contract.functions.balances(1)._encode_transaction_data()
                calls.append({"target": pool_checksum, "allowFailure": True, "callData": balances1_data})
                call_mapping.append(("balances1", pool_address))
                
                #  NOUVEAU Call 11: get_dy() for Curve - BATCHÉ!
                # On calcule pour un montant standard (1 token avec 18 decimals)
                try:
                    standard_amount = 10**18  # 1 token standard
                    get_dy_data = curve_contract.functions.get_dy(0, 1, standard_amount)._encode_transaction_data()
                    calls.append({"target": pool_checksum, "allowFailure": True, "callData": get_dy_data})
                    call_mapping.append(("get_dy", pool_address))
                except:
                    # Si get_dy n'existe pas sur ce contrat, on skip
                    pass

            return calls, call_mapping

        # ÉTAPE 1: Récupérer TOUTES les données des pools (y compris get_dy)
        for i in range(0, len(pool_addresses), CHUNK_SIZE):
            subset = pool_addresses[i:i+CHUNK_SIZE]
            calls, call_mapping = _build_calls_for_pools(subset)
            chunk_start = time.perf_counter()
            try:
                results = multicall.functions.aggregate3(calls).call()
            except Exception as e:
                print(f" Erreur multicall chunk {i//CHUNK_SIZE+1}: {e}")
                results = []
            chunk_time = time.perf_counter() - chunk_start

            # Organiser les résultats
            pool_data = {}
            for idx, res in enumerate(results):
                try:
                    success, return_data = res
                except Exception:
                    if isinstance(res, (list, tuple)) and len(res) >= 2:
                        success = res[0]
                        return_data = res[1]
                    else:
                        success = False
                        return_data = None

                method, pool_address = call_mapping[idx]
                if pool_address not in pool_data:
                    pool_data[pool_address] = {}
                if success and return_data:
                    pool_data[pool_address][method] = return_data

            for k, v in pool_data.items():
                if k not in all_pool_data:
                    all_pool_data[k] = v
                else:
                    all_pool_data[k].update(v)

        pool_fetch_time = time.perf_counter() - total_start
        print(f" Données pools récupérées en {pool_fetch_time:.3f}s")

        # ÉTAPE 2: Extraire tokens uniques
        unique_tokens = set()
        for pool_address, data in all_pool_data.items():
            if 'token0' in data and data['token0']:
                try:
                    token0_addr = w3.codec.decode(['address'], data['token0'])[0]
                    unique_tokens.add(token0_addr)
                except:
                    pass
            if 'token1' in data and data['token1']:
                try:
                    token1_addr = w3.codec.decode(['address'], data['token1'])[0]
                    unique_tokens.add(token1_addr)
                except:
                    pass
            if 'coins0' in data and data['coins0']:
                try:
                    token0_addr = w3.codec.decode(['address'], data['coins0'])[0]
                    unique_tokens.add(token0_addr)
                except:
                    pass
            if 'coins1' in data and data['coins1']:
                try:
                    token1_addr = w3.codec.decode(['address'], data['coins1'])[0]
                    unique_tokens.add(token1_addr)
                except:
                    pass
        
        # ÉTAPE 3: Batch load tokens info
        if unique_tokens:
            batch_get_tokens_info(list(unique_tokens))
        
        # ÉTAPE 4: Traitement ultra-rapide (TOUT est en cache maintenant!)
        processing_start = time.perf_counter()
        final_results = []
        
        for pool_address, data in all_pool_data.items():
            try:
                def _is_valid_v3_slot0(raw_data):
                    return bool(raw_data) and len(raw_data) >= 224

                def _is_valid_v2_reserves(raw_data):
                    return bool(raw_data) and len(raw_data) in (96, 128) 

                is_v3 = 'slot0' in data and _is_valid_v3_slot0(data['slot0'])
                is_v2 = 'getReserves' in data and _is_valid_v2_reserves(data['getReserves'])
                is_curve = 'coins0' in data and data['coins0'] and 'coins1' in data and data['coins1']

                                
                if not (is_v3 or is_v2 or is_curve):
                    continue
                
                # Décoder tokens
                if is_curve and not is_v2 and not is_v3:
                    token0_address = w3.codec.decode(['address'], data['coins0'])[0]
                    token1_address = w3.codec.decode(['address'], data['coins1'])[0]
                else:
                    token0_address = w3.codec.decode(['address'], data['token0'])[0]
                    token1_address = w3.codec.decode(['address'], data['token1'])[0]
                
                token0_info = get_token_info(token0_address)
                token1_info = get_token_info(token1_address)
                
                if not token0_info or not token1_info:
                    continue
                
                if is_curve and not is_v2 and not is_v3:
                    balance0 = 0
                    balance1 = 0
                    if 'balances0' in data and data['balances0']:
                        balance0 = w3.codec.decode(['uint256'], data['balances0'])[0]
                    if 'balances1' in data and data['balances1']:
                        balance1 = w3.codec.decode(['uint256'], data['balances1'])[0]
                    
                    #  Utiliser get_dy depuis le cache au lieu d'un appel RPC!
                    if 'get_dy' in data and data['get_dy']:
                        try:
                            amount_out = w3.codec.decode(['uint256'], data['get_dy'])[0]
                            standard_amount = 10**18
                            
                            # Ajuster pour les décimales réelles
                            decimals_adj = token0_info['decimals'] - 18
                            if decimals_adj != 0:
                                standard_amount = 10**token0_info['decimals']
                            
                            price_0_in_1 = amount_out / (10 ** token1_info['decimals'])
                            price_1_in_0 = 1 / price_0_in_1 if price_0_in_1 > 0 else 0
                            
                            balance0_formatted = balance0 / (10 ** token0_info['decimals'])
                            balance1_formatted = balance1 / (10 ** token1_info['decimals'])
                            
                            final_results.append({
                                'pool_address': pool_address,
                                'version': 'Curve',
                                'dex': 'Curve',
                                'token0': {
                                    'address': token0_address,
                                    'symbol': token0_info['symbol'],
                                    'decimals': token0_info['decimals'],
                                    'reserve': balance0_formatted
                                },
                                'token1': {
                                    'address': token1_address,
                                    'symbol': token1_info['symbol'],
                                    'decimals': token1_info['decimals'],
                                    'reserve': balance1_formatted
                                },
                                'price_0_in_1': price_0_in_1,
                                'price_1_in_0': price_1_in_0,
                                'pair': f"{token0_info['symbol']}/{token1_info['symbol']}"
                            })
                        except Exception as e:
                            # Fallback: utiliser une approximation depuis les balances
                            if balance0 > 0 and balance1 > 0:
                                balance0_formatted = balance0 / (10 ** token0_info['decimals'])
                                balance1_formatted = balance1 / (10 ** token1_info['decimals'])
                                price_0_in_1 = balance1_formatted / balance0_formatted
                                price_1_in_0 = balance0_formatted / balance1_formatted
                                
                                final_results.append({
                                    'pool_address': pool_address,
                                    'version': 'Curve',
                                    'dex': 'Curve',
                                    'token0': {
                                        'address': token0_address,
                                        'symbol': token0_info['symbol'],
                                        'decimals': token0_info['decimals'],
                                        'reserve': balance0_formatted
                                    },
                                    'token1': {
                                        'address': token1_address,
                                        'symbol': token1_info['symbol'],
                                        'decimals': token1_info['decimals'],
                                        'reserve': balance1_formatted
                                    },
                                    'price_0_in_1': price_0_in_1,
                                    'price_1_in_0': price_1_in_0,
                                    'pair': f"{token0_info['symbol']}/{token1_info['symbol']}"
                                })
                
                elif is_v3:
                    slot0_decoded = w3.codec.decode(
                        ['uint160', 'int24', 'uint16', 'uint16', 'uint16', 'uint8', 'bool'],
                        data['slot0']
                    )
                    sqrt_price_x96 = slot0_decoded[0]
                    tick = slot0_decoded[1]
                    
                    fee = w3.codec.decode(['uint24'], data['fee'])[0] if 'fee' in data and data['fee'] else 3000
                    liquidity = w3.codec.decode(['uint128'], data['liquidity'])[0] if 'liquidity' in data and data['liquidity'] else 0

                    sqrt_ratio = sqrt_price_x96 / (2 ** 96)
                    
                    price_raw = (sqrt_ratio ** 2)
                    
                    decimals_adjustment = 10 ** (token0_info['decimals'] - token1_info['decimals'])
                    price_0_in_1 = price_raw * decimals_adjustment
                    
                    if price_0_in_1 <= 0 or price_0_in_1 > 1e12:
                        print(f"  Prix V3 aberrant pour {token0_info['symbol']}/{token1_info['symbol']}: {price_0_in_1}")
                        continue
                    
                    price_1_in_0 = 1 / price_0_in_1 if price_0_in_1 > 0 else 0

                    if price_0_in_1 <= 0 or price_1_in_0 <= 0:
                        print(f"   Prix nul ignoré (V3): {token0_info['symbol']}/{token1_info['symbol']}")
                        continue

                    final_results.append({
                        'pool_address': pool_address,
                        'version': 'V3',
                        'dex': 'Unknown',
                        'token0': {
                            'address': token0_address,
                            'symbol': token0_info['symbol'],
                            'decimals': token0_info['decimals']
                        },
                        'token1': {
                            'address': token1_address,
                            'symbol': token1_info['symbol'],
                            'decimals': token1_info['decimals']
                        },
                        'fee_tier': fee,
                        'fee_percentage': fee / 10000,
                        'liquidity': liquidity,
                        'tick': tick,
                        'sqrt_price_x96': sqrt_price_x96,
                        'price_0_in_1': price_0_in_1,
                        'price_1_in_0': price_1_in_0,
                        'pair': f"{token0_info['symbol']}/{token1_info['symbol']}"
                    })
                
                # TRAITEMENT V2
                elif is_v2:
                    reserves_decoded = w3.codec.decode(['uint112', 'uint112', 'uint32'], data['getReserves'])
                    reserve0 = reserves_decoded[0]
                    reserve1 = reserves_decoded[1]
                    
                    reserve0_formatted = reserve0 / (10 ** token0_info['decimals'])
                    reserve1_formatted = reserve1 / (10 ** token1_info['decimals'])
                    
                    price_0_in_1 = reserve1_formatted / reserve0_formatted if reserve0_formatted > 0 else 0
                    price_1_in_0 = reserve0_formatted / reserve1_formatted if reserve1_formatted > 0 else 0
                    
                    final_results.append({
                        'pool_address': pool_address,
                        'version': 'V2',
                        'dex': 'Unknown',
                        'token0': {
                            'address': token0_address,
                            'symbol': token0_info['symbol'],
                            'decimals': token0_info['decimals'],
                            'reserve': reserve0_formatted
                        },
                        'token1': {
                            'address': token1_address,
                            'symbol': token1_info['symbol'],
                            'decimals': token1_info['decimals'],
                            'reserve': reserve1_formatted
                        },
                        'price_0_in_1': price_0_in_1,
                        'price_1_in_0': price_1_in_0,
                        'pair': f"{token0_info['symbol']}/{token1_info['symbol']}",
                        'fee_tier': 3000,
                        'fee_percentage': 0.3
                    })
            
            except Exception as e:
                print(f"  Erreur traitement pool {pool_address}: {e}")
                continue
        
        processing_time = time.perf_counter() - processing_start
        total_time = time.perf_counter() - total_start
        
        print(f" Traitement terminé en {processing_time:.3f}s")
        print(f" TEMPS TOTAL ULTRA-OPTIMISÉ: {total_time:.3f}s pour {len(final_results)} pools")
        print(f"   └─ Multicall pools + Curve: {pool_fetch_time:.3f}s")
        print(f"   └─ Traitement: {processing_time:.3f}s")
        
        return final_results
        
    except Exception as e:
        print(f" Erreur lors de l'appel batch: {e}")
        import traceback
        traceback.print_exc()
        return []

#############################################################################################################################################################
# RECUPERATION DES PAIRES POPULAIRES VIA THE GRAPH  
#############################################################################################################################################################

# Récupération des pools les plus utilisées pour analyse de prix et arbitrage
def get_top_pairs_v2(subgraph_url: str, limit: int = NB_PAIR) -> List[Dict]:
    """
    Récupère les paires Uniswap V2 depuis The Graph
    V2 utilise le terme "pairs"
    """
    try:
        query = f"""
        {{
          pairs(first: {limit}, orderBy: volumeUSD, orderDirection: desc) {{
            id
            token0 {{ id symbol decimals }}
            token1 {{ id symbol decimals }}
            volumeUSD
          }}
        }}
        """
        print(f"Envoi requête V2 à: {subgraph_url[:80]}...")
        response = requests.post(subgraph_url, json={'query': query}, timeout=10)
        
        if response.status_code != 200:
            print(f"Erreur HTTP {response.status_code}")
            return []
        
        json_response = response.json()
        
        if "errors" in json_response:
            print(f"Erreurs GraphQL V2: {json_response['errors']}")
            return []
        
        if "data" not in json_response or not json_response["data"]:
            return []
        
        data = json_response["data"]["pairs"]
        print(f"{len(data)} paires V2 récupérées")

        pairs = []
        for p in data:
            pairs.append({
                "pair_id": p["id"].lower(),
                "token0": p["token0"]["id"].lower(),
                "token1": p["token1"]["id"].lower(),
                "symbol0": p["token0"]["symbol"],
                "symbol1": p["token1"]["symbol"],
                "volume_usd": float(p["volumeUSD"])
            })
        return pairs
    except Exception as e:
        print(f"Erreur V2: {e}")
        return []

def get_top_pairs_v3(subgraph_url: str, limit: int = NB_PAIR) -> List[Dict]:
    """
    Récupère les pools Uniswap V3 depuis The Graph
    V3 utilise le terme "pools" (pas "pairs")
    """
    try:
        query = f"""
        {{
          pools(first: {limit}, orderBy: volumeUSD, orderDirection: desc) {{
            id
            token0 {{ id symbol decimals }}
            token1 {{ id symbol decimals }}
            volumeUSD
          }}
        }}
        """
        print(f"Envoi requête V3 à: {subgraph_url[:80]}...")
        response = requests.post(subgraph_url, json={'query': query}, timeout=10)
        
        if response.status_code != 200:
            print(f"Erreur HTTP {response.status_code}")
            return []
        
        json_response = response.json()
        
        if "errors" in json_response:
            print(f"Erreurs GraphQL V3: {json_response['errors']}")
            return []
        
        if "data" not in json_response or not json_response["data"]:
            return []
        
        data = json_response["data"]["pools"]
        print(f"{len(data)} pools V3 récupérés")

        pools = []
        for p in data:
            pools.append({
                "pair_id": p["id"].lower(),
                "token0": p["token0"]["id"].lower(),
                "token1": p["token1"]["id"].lower(),
                "symbol0": p["token0"]["symbol"],
                "symbol1": p["token1"]["symbol"],
                "volume_usd": float(p["volumeUSD"])
            })
        return pools
    except Exception as e:
        print(f"Erreur V3: {e}")
        return []

def get_top_pairs_sushiswap(subgraph_url: str, limit: int = NB_PAIR) -> List[Dict]:
    """
    Récupère les paires Sushiswap depuis The Graph
    Sushiswap utilise le même schéma qu'Uniswap V2 (terme "pairs")
    """
    try:
        query = f"""
        {{
          pairs(first: {limit}, orderBy: volumeUSD, orderDirection: desc) {{
            id
            token0 {{ id symbol decimals }}
            token1 {{ id symbol decimals }}
            volumeUSD
          }}
        }}
        """
        print(f"Envoi requête Sushiswap à: {subgraph_url[:80]}...")
        response = requests.post(subgraph_url, json={'query': query}, timeout=10)
        
        if response.status_code != 200:
            print(f"Erreur HTTP {response.status_code}")
            return []
        
        json_response = response.json()
        
        if "errors" in json_response:
            print(f"Erreurs GraphQL Sushiswap: {json_response['errors']}")
            return []
        
        if "data" not in json_response or not json_response["data"]:
            return []
        
        data = json_response["data"]["pairs"]
        print(f"{len(data)} paires Sushiswap récupérées")

        pairs = []
        for p in data:
            pairs.append({
                "pair_id": p["id"].lower(),
                "token0": p["token0"]["id"].lower(),
                "token1": p["token1"]["id"].lower(),
                "symbol0": p["token0"]["symbol"],
                "symbol1": p["token1"]["symbol"],
                "volume_usd": float(p["volumeUSD"])
            })
        return pairs
    except Exception as e:
        print(f"Erreur Sushiswap: {e}")
        return []

def get_top_pairs_curve(subgraph_url: str = None, limit: int = NB_PAIR) -> List[Dict]:
    """
    Récupère les pools Curve depuis une liste prédéfinie
    Les subgraphs Curve sont souvent obsolètes ou ont des schémas non standard
    Cette approche utilise les pools Curve les plus populaires
    """
    try:
        # Liste des pools Curve populaires (adresse, token0, token1)
        # Ces pools sont vérifiés et actifs sur Ethereum mainnet
        # Note: Seuls les pools avec interface standard get_dy(int128, int128, uint256) sont inclus
        CURVE_POPULAR_POOLS = [
            # stETH/ETH pool
            {
                "pool_address": "0xDC24316b9AE028F1497c275EB9192a3Ea0f67022",
                "token0": "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE",  # ETH
                "token1": "0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84",  # stETH
            },
            # frxETH/ETH pool 
            {
                "pool_address": "0xa1F8A6807c402E4A15ef4EBa36528A3FED24E577",
                "token0": "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE",  # ETH
                "token1": "0x5E8422345238F34275888049021821E8E08CAa1f",  # frxETH
            },
            # Tricrypto pool (USDT/WBTC/ETH)
            {
                "pool_address": "0x7F86Bf177Dd4F3494b841a37e810A34dD56c829B",
                "token0": "0xdAC17F958D2ee523a2206206994597C13D831ec7",  # USDT
                "token1": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",  # WBTC
            },
            # 3Pool (DAI/USDC/USDT) 
            {
                "pool_address": "0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7",
                "token0": "0x6B175474E89094C44Da98b954EedeAC495271d0F",  # DAI
                "token1": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
            },
            # LUSD/3Crv pool 
            {
                "pool_address": "0xEd279fDD11cA84bEef15AF5D39BB4d4bEE23F0cA",
                "token0": "0x5f98805A4E8be255a32880FDeC7F6728C6568bA0",  # LUSD
                "token1": "0x6c3F90f043a72FA612cbac8115EE7e52BDe6E490",  # 3Crv
            },
            # sUSD/3Crv pool 
            {
                "pool_address": "0xA5407eAE9Ba41422680e2e00537571bcC53efBfD",
                "token0": "0x57Ab1ec28D129707052df4dF418D58a2D46d5f51",  # sUSD
                "token1": "0x6c3F90f043a72FA612cbac8115EE7e52BDe6E490",  # 3Crv
            }
        ]
        
        print(f"Utilisation de {len(CURVE_POPULAR_POOLS)} pools Curve prédéfinis...")
        
        pools = []
        for pool_data in CURVE_POPULAR_POOLS[:limit]:
            # Normaliser les adresses ETH en WETH pour la comparaison
            token0 = pool_data["token0"]
            token1 = pool_data["token1"]
            
            # ETH (0xEee...) → WETH pour compatibilité avec Uniswap
            WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
            if token0.lower() == "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee":
                token0 = WETH
            if token1.lower() == "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee":
                token1 = WETH
            
            pools.append({
                "pair_id": pool_data["pool_address"].lower(),
                "token0": token0.lower(),
                "token1": token1.lower(),
                "volume_usd": 1000000  # Valeur fictive pour le tri
            })
        
        print(f"{len(pools)} pools Curve chargés depuis la liste prédéfinie")
        return pools
        
    except Exception as e:
        print(f"Erreur Curve: {e}")
        import traceback
        traceback.print_exc()
        return []

def fetch_matching_pairs_for_curve(curve_pairs: List[Dict], v2_subgraph: str, v3_subgraph: str, sushi_subgraph: str) -> tuple:
    """
    Pour chaque paire Curve, interroge les subgraphs Uniswap V2, V3 et Sushiswap
    pour trouver les pools avec les mêmes tokens
    
    Args:
        curve_pairs: Liste des paires Curve à matcher
        v2_subgraph: URL du subgraph Uniswap V2
        v3_subgraph: URL du subgraph Uniswap V3
        sushi_subgraph: URL du subgraph Sushiswap
    
    Returns:
        tuple: (v2_matches, v3_matches, sushi_matches) - listes de pools trouvés
    """
    
    v2_matches = []
    v3_matches = []
    sushi_matches = []
    
    for curve_pair in curve_pairs:
        token0 = curve_pair["token0"]
        token1 = curve_pair["token1"]
        
        # Normaliser les tokens (ordre alphabétique pour la recherche)
        tokens_sorted = sorted([token0, token1])
        
        # Rechercher sur Uniswap V2
        try:
            query_v2 = f"""
            {{
              pairs(
                where: {{
                  token0_in: ["{tokens_sorted[0]}", "{tokens_sorted[1]}"],
                  token1_in: ["{tokens_sorted[0]}", "{tokens_sorted[1]}"]
                }},
                first: 10,
                orderBy: volumeUSD,
                orderDirection: desc
              ) {{
                id
                token0 {{ id symbol decimals }}
                token1 {{ id symbol decimals }}
                volumeUSD
              }}
            }}
            """
            response = requests.post(v2_subgraph, json={'query': query_v2}, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if "data" in data and data["data"]["pairs"]:
                    for pair in data["data"]["pairs"]:
                        # Vérifier que c'est bien la même paire (dans n'importe quel ordre)
                        pair_tokens = sorted([pair["token0"]["id"].lower(), pair["token1"]["id"].lower()])
                        if pair_tokens == tokens_sorted:
                            v2_matches.append({
                                "pair_id": pair["id"].lower(),
                                "token0": pair["token0"]["id"].lower(),
                                "token1": pair["token1"]["id"].lower(),
                                "symbol0": pair["token0"]["symbol"],
                                "symbol1": pair["token1"]["symbol"],
                                "volume_usd": float(pair["volumeUSD"])
                            })
                            break  # On prend le premier (plus gros volume)
        except Exception as e:
            print(f"    ✗ Erreur V2: {e}")
        
        # Rechercher sur Uniswap V3
        try:
            query_v3 = f"""
            {{
              pools(
                where: {{
                  token0_in: ["{tokens_sorted[0]}", "{tokens_sorted[1]}"],
                  token1_in: ["{tokens_sorted[0]}", "{tokens_sorted[1]}"]
                }},
                first: 10,
                orderBy: volumeUSD,
                orderDirection: desc
              ) {{
                id
                token0 {{ id symbol decimals }}
                token1 {{ id symbol decimals }}
                volumeUSD
              }}
            }}
            """
            response = requests.post(v3_subgraph, json={'query': query_v3}, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if "data" in data and data["data"]["pools"]:
                    for pool in data["data"]["pools"]:
                        # Vérifier que c'est bien la même paire
                        pool_tokens = sorted([pool["token0"]["id"].lower(), pool["token1"]["id"].lower()])
                        if pool_tokens == tokens_sorted:
                            v3_matches.append({
                                "pair_id": pool["id"].lower(),
                                "token0": pool["token0"]["id"].lower(),
                                "token1": pool["token1"]["id"].lower(),
                                "symbol0": pool["token0"]["symbol"],
                                "symbol1": pool["token1"]["symbol"],
                                "volume_usd": float(pool["volumeUSD"])
                            })
                            break  # On prend le premier (plus gros volume)
        except Exception as e:
            print(f"    ✗ Erreur V3: {e}")
        
        # Rechercher sur Sushiswap
        try:
            query_sushi = f"""
            {{
              pairs(
                where: {{
                  token0_in: ["{tokens_sorted[0]}", "{tokens_sorted[1]}"],
                  token1_in: ["{tokens_sorted[0]}", "{tokens_sorted[1]}"]
                }},
                first: 10,
                orderBy: volumeUSD,
                orderDirection: desc
              ) {{
                id
                token0 {{ id symbol decimals }}
                token1 {{ id symbol decimals }}
                volumeUSD
              }}
            }}
            """
            response = requests.post(sushi_subgraph, json={'query': query_sushi}, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if "data" in data and data["data"]["pairs"]:
                    for pair in data["data"]["pairs"]:
                        # Vérifier que c'est bien la même paire
                        pair_tokens = sorted([pair["token0"]["id"].lower(), pair["token1"]["id"].lower()])
                        if pair_tokens == tokens_sorted:
                            sushi_matches.append({
                                "pair_id": pair["id"].lower(),
                                "token0": pair["token0"]["id"].lower(),
                                "token1": pair["token1"]["id"].lower(),
                                "symbol0": pair["token0"]["symbol"],
                                "symbol1": pair["token1"]["symbol"],
                                "volume_usd": float(pair["volumeUSD"])
                            })
                            break  # On prend le premier (plus gros volume)
        except Exception as e:
            print(f"    ✗ Erreur Sushiswap: {e}")
    
    print(f"\n Résultats de la recherche:")
    print(f"   Uniswap V2: {len(v2_matches)} pools trouvés")
    print(f"   Uniswap V3: {len(v3_matches)} pools trouvés")
    print(f"   Sushiswap: {len(sushi_matches)} pools trouvés")
    
    return v2_matches, v3_matches, sushi_matches

#############################################################################################################################################################
# SAUVEGARDE DES DONNÉES EN BASE POSTGRESQL
#############################################################################################################################################################

def safe_db_string(s):
    """Convertit une string en format safe pour Postgres avec encoding LATIN-1"""
    if s is None:
        return None
    if not isinstance(s, str):
        s = str(s)
    try:
        # Tester si la string est déjà compatible latin-1
        s.encode('latin-1')
        return s
    except (UnicodeDecodeError, UnicodeEncodeError):
        # Remplacer les caractères incompatibles
        return s.encode('latin-1', errors='replace').decode('latin-1')

def save_to_database(pool_data_list: List[Dict], opportunities: List[Dict] = None, 
                     block_number: int = None, db_config: Dict = None) -> bool:
    """Simple save avec protection encodage LATIN-1"""
    if db_config is None:
        db_config = {
            'host': os.getenv("DB_HOST", "localhost"),
            'dbname': os.getenv("DB_NAME", "arbitrage"),  
            'user': os.getenv("DB_USER", "postgres"),
            'password': os.getenv("DB_PASSWORD"),
            'port': int(os.getenv("DB_PORT", "5432"))
        }

    if (not pool_data_list) and (not opportunities):
        print("Aucune donnée à sauvegarder.")
        return True

    conn = None
    try:
        conn = safe_psycopg2_connect(db_config)
        try:
            conn.set_client_encoding('LATIN1')
        except Exception:
            try:
                with conn.cursor() as ctmp:
                    ctmp.execute("SET client_encoding = 'LATIN1';")
                conn.commit()
            except Exception:
                pass

        with conn.cursor() as cur:
            inserted = 0

            # Insert pools avec protection encodage
            for pool in (pool_data_list or []):
                token0 = pool.get('token0', {}) or {}
                token1 = pool.get('token1', {}) or {}

                try:
                    cur.execute(
                        """
                        INSERT INTO pool_prices (
                            block_number, pool_address, version, pair,
                            token0_address, token0_symbol, token0_decimals,
                            token1_address, token1_symbol, token1_decimals,
                            price_0_in_1, price_1_in_0,
                            fee_tier, fee_percentage,
                            liquidity, tick, sqrt_price_x96,
                            reserve0, reserve1
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            block_number,
                            safe_db_string(pool.get('pool_address')),
                            safe_db_string(pool.get('version')),
                            safe_db_string(pool.get('pair')),
                            safe_db_string(token0.get('address')),
                            safe_db_string(token0.get('symbol')),
                            token0.get('decimals'),
                            safe_db_string(token1.get('address')),
                            safe_db_string(token1.get('symbol')),
                            token1.get('decimals'),
                            pool.get('price_0_in_1'),
                            pool.get('price_1_in_0'),
                            pool.get('fee_tier'),
                            pool.get('fee_percentage'),
                            pool.get('liquidity'),
                            pool.get('tick'),
                            pool.get('sqrt_price_x96'),
                            token0.get('reserve'),
                            token1.get('reserve')
                        )
                    )
                    inserted += 1
                except Exception as e:
                    print(f"Erreur insertion pool {pool.get('pool_address','unknown')}: {e}")
                    continue

            # Insert opportunities avec protection encodage
            for opp in (opportunities or []):
                buy = opp.get('buy_pool', {}) or {}
                sell = opp.get('sell_pool', {}) or {}
                try:
                    cur.execute(
                        """
                        INSERT INTO arbitrage_opportunities (
                            block_number, pair,
                            buy_pool_address, buy_pool_version, buy_price,
                            sell_pool_address, sell_pool_version, sell_price,
                            gross_profit_percentage, total_fees_percentage, net_profit_percentage
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            block_number,
                            safe_db_string(opp.get('pair')),
                            safe_db_string(buy.get('pool_address')),
                            safe_db_string(buy.get('version')),
                            buy.get('price_0_in_1'),
                            safe_db_string(sell.get('pool_address')),
                            safe_db_string(sell.get('version')),
                            sell.get('price_0_in_1'),
                            opp.get('gross_profit_percentage'),
                            opp.get('total_fees_percentage'),
                            opp.get('net_profit_percentage')
                        )
                    )
                    inserted += 1
                except Exception as e:
                    print(f"Erreur insertion opportunité {opp.get('pair','unknown')}: {e}")
                    continue

            conn.commit()
            print(f"Sauvegarde BD: {inserted} lignes insérées")
            return inserted > 0

    except Exception as e:
        print(f"Erreur sauvegarde base de données: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

#############################################################################################################################################################
# ANALISE DES DONNÉES ENREGISTRÉES
#############################################################################################################################################################

def get_latest_prices(pair: str = None, limit: int = 100, db_config: Dict = None):
    """
    Récupère les derniers prix enregistrés
    
    Args:
        pair: Filtrer par paire spécifique (ex: "USDC/WETH")
        limit: Nombre maximum de résultats
        db_config: Configuration de connexion à la base de données
    
    Returns:
        DataFrame pandas avec les données
    """

    try:
        conn = safe_psycopg2_connect(db_config)
        
        if pair:
            query = """
                SELECT * FROM pool_prices 
                WHERE pair = %s 
                ORDER BY timestamp DESC 
                LIMIT %s
            """
            df = pd.read_sql_query(query, conn, params=(pair, limit))
        else:
            query = """
                SELECT * FROM pool_prices 
                ORDER BY timestamp DESC 
                LIMIT %s
            """
            df = pd.read_sql_query(query, conn, params=(limit,))
        
        conn.close()
        return df
        
    except Exception as e:
        print(f"Erreur récupération prix: {e}")
        return None

def get_best_arbitrage_opportunities(min_profit: float = 0.01, limit: int = 50, db_config: Dict = None):
    """
    Récupère les meilleures opportunités d'arbitrage enregistrées
    
    Args:
        min_profit: Profit net minimum (en %)
        limit: Nombre maximum de résultats
        db_config: Configuration de connexion à la base de données
    
    Returns:
        DataFrame pandas avec les opportunités
    """

    try:
        conn = safe_psycopg2_connect(db_config)
        
        query = """
            SELECT * FROM arbitrage_opportunities 
            WHERE net_profit_percentage >= %s 
            ORDER BY net_profit_percentage DESC, timestamp DESC 
            LIMIT %s
        """
        df = pd.read_sql_query(query, conn, params=(min_profit, limit))
        
        conn.close()
        return df
        
    except Exception as e:
        print(f"Erreur récupération opportunités: {e}")
        return None

def get_price_history(pool_address: str, hours: int = 24, db_config: Dict = None):
    """
    Récupère l'historique des prix pour un pool spécifique
    
    Args:
        pool_address: Adresse du pool
        hours: Nombre d'heures d'historique
        db_config: Configuration de connexion à la base de données
    
    Returns:
        DataFrame pandas avec l'historique
    """

    try:
        conn = safe_psycopg2_connect(db_config)
        
        query = """
            SELECT timestamp, price_0_in_1, price_1_in_0, liquidity, reserve0, reserve1
            FROM pool_prices 
            WHERE pool_address = %s 
            AND timestamp >= NOW() - INTERVAL '%s hours'
            ORDER BY timestamp ASC
        """
        df = pd.read_sql_query(query, conn, params=(pool_address.lower(), hours))
        
        conn.close()
        return df
        
    except Exception as e:
        print(f"Erreur récupération historique: {e}")
        return None

def get_statistics(pair: str = None, db_config: Dict = None):
    """
    Calcule des statistiques sur les données enregistrées
    
    Args:
        pair: Filtrer par paire spécifique
        db_config: Configuration de connexion à la base de données
    
    Returns:
        Dict avec les statistiques
    """

    try:
        conn = safe_psycopg2_connect(db_config)
        cur = conn.cursor()
        
        stats = {}
        
        # Statistiques générales sur les pools
        if pair:
            cur.execute("""
                SELECT 
                    COUNT(*) as total_records,
                    COUNT(DISTINCT pool_address) as unique_pools,
                    MIN(timestamp) as first_record,
                    MAX(timestamp) as last_record,
                    AVG(price_0_in_1) as avg_price,
                    MIN(price_0_in_1) as min_price,
                    MAX(price_0_in_1) as max_price,
                    STDDEV(price_0_in_1) as price_volatility
                FROM pool_prices
                WHERE pair = %s
            """, (pair,))
        else:
            cur.execute("""
                SELECT 
                    COUNT(*) as total_records,
                    COUNT(DISTINCT pool_address) as unique_pools,
                    COUNT(DISTINCT pair) as unique_pairs,
                    MIN(timestamp) as first_record,
                    MAX(timestamp) as last_record
                FROM pool_prices
            """)
        
        row = cur.fetchone()
        if row:
            columns = [desc[0] for desc in cur.description]
            stats['pools'] = dict(zip(columns, row))
        
        # Statistiques sur les opportunités d'arbitrage
        if pair:
            cur.execute("""
                SELECT 
                    COUNT(*) as total_opportunities,
                    AVG(net_profit_percentage) as avg_profit,
                    MAX(net_profit_percentage) as max_profit,
                    MIN(net_profit_percentage) as min_profit
                FROM arbitrage_opportunities
                WHERE pair = %s
            """, (pair,))
        else:
            cur.execute("""
                SELECT 
                    COUNT(*) as total_opportunities,
                    COUNT(DISTINCT pair) as unique_pairs_with_arb,
                    AVG(net_profit_percentage) as avg_profit,
                    MAX(net_profit_percentage) as max_profit,
                    MIN(net_profit_percentage) as min_profit
                FROM arbitrage_opportunities
            """)
        
        row = cur.fetchone()
        if row:
            columns = [desc[0] for desc in cur.description]
            stats['arbitrage'] = dict(zip(columns, row))
        
        cur.close()
        conn.close()
        
        return stats
        
    except Exception as e:
        print(f"Erreur calcul statistiques: {e}")
        return None

def compare_pricing_methods(pool_data_list: List[Dict]):
    """
    Compare les différentes méthodes de calcul de prix
    """
    print(f"\n=== COMPARAISON DES MÉTHODES DE CALCUL ===")
    
    for pool in pool_data_list:
        if pool['version'] == 'V2':
            reserves_price = pool.get('price_reserves_0_in_1')
            swap_price = pool.get('price_swap_0_in_1')
            
            if reserves_price and swap_price:
                diff_percentage = abs(swap_price - reserves_price) / reserves_price * 100
                print(f"{pool['pair']} V2:")
                print(f"  Réserves (x*y=k): {reserves_price:.6f}")
                print(f"  getAmountsOut():   {swap_price:.6f}")
                print(f"  Différence:        {diff_percentage:.4f}%")
                
        else:  # V3
            slot0_price = pool.get('price_slot0_0_in_1')
            quote_price = pool.get('price_quote_0_in_1')
            
            if slot0_price and quote_price:
                diff_percentage = abs(quote_price - slot0_price) / slot0_price * 100
                print(f"{pool['pair']} V3 (fee: {pool['fee_tier']/10000:.2f}%):")
                print(f"  slot0 (sqrtPriceX96):        {slot0_price:.6f}")
                print(f"  quoteExactInputSingle():     {quote_price:.6f}")
                print(f"  Différence:                  {diff_percentage:.4f}%")


#############################################################################################################################################################
# DETECTION D'OPPORTUNITÉS D'ARBITRAGE
#############################################################################################################################################################

def analyze_arbitrage_opportunities(pool_data_list: List[Dict], AMOUNT_TOKEN: float = AMOUNT_TOKEN):
    """
    Analyse simple des opportunités d'arbitrage entre les pools
    """
    print(f"\n=== ANALYSE D'ARBITRAGE ===")
    
    # D'abord, comparer les méthodes de calcul
    compare_pricing_methods(pool_data_list)
    
    print(f"\n--- Opportunités d'arbitrage ---")
    
    # Grouper par paire de tokens
    pairs = {}
    for pool in pool_data_list:
        pair = pool['pair']
        if pair not in pairs:
            pairs[pair] = []
        pairs[pair].append(pool)
    
    opportunities = []
    
    for pair, pools in pairs.items():
        if len(pools) < 2:
            continue
        
        # Trouver les prix min/max pour cette paire
        pools_sorted = sorted(pools, key=lambda x: x['price_0_in_1'])
        min_price_pool = pools_sorted[0]
        max_price_pool = pools_sorted[-1]

        buy_price = min_price_pool['price_0_in_1']
        sell_price = max_price_pool['price_0_in_1']

        # Frais en décimal (ex: 0.3% = 0.003)
        buy_fee = min_price_pool.get('fee_percentage', 0) / 100
        sell_fee = max_price_pool.get('fee_percentage', 0) / 100

        capital_token0 = (WALLET_BALANCE / buy_price) * (1 - buy_fee)

        final_token1 = capital_token0 * sell_price * (1 - sell_fee)

        net_profit_amount = final_token1 - WALLET_BALANCE
        net_profit_percentage = (net_profit_amount / WALLET_BALANCE) * 100

        if net_profit_percentage > PROFIT_NET: 
            opportunities.append({
                'pair': pair,
                'buy_pool': min_price_pool,
                'sell_pool': max_price_pool,
                'net_profit_amount': net_profit_amount
            })
    
    if opportunities:
        print("Opportunités rentables détectées:")
        for opp in sorted(opportunities, key=lambda x: x['net_profit_percentage'], reverse=True):
            print(f"  {opp['pair']}: {opp['net_profit_percentage']:.3f}% net "
                  f"(brut: {opp['gross_profit_percentage']:.3f}%, fees: {opp['total_fees_percentage']:.3f}%) "
                  f"({opp['buy_pool']['version']} → {opp['sell_pool']['version']})")
    else:
        print("Aucune opportunité d'arbitrage rentable détectée.")

def normalize_pair(p):
    """Retourne un tuple trié d’adresses de tokens (pour détecter les paires équivalentes)."""
    t0, t1 = sorted([p["token0"], p["token1"]])
    return (t0, t1)

def monitor_pools_continuously(interval_seconds: int = INTERVAL_PAUSE, max_iterations: int = None):
    """
    Surveille les pools en continu avec batch calling
    Inclut toujours Uniswap V2, Uniswap V3, Sushiswap et Curve
    
    Args:
        interval_seconds: Délai entre chaque itération (en secondes)
        max_iterations: Nombre maximum d'itérations (None = infini)
    """
    print("=" * 80)
    print("SURVEILLANCE EN CONTINU DES POOLS (BATCH CALLING)")
    print("=" * 80)
    print(f"Intervalle: {interval_seconds}s entre chaque scan")
    print(f"Itérations: {'Infini' if max_iterations is None else max_iterations}")
    print(f"DEX surveillés: Uniswap V2, Uniswap V3, Sushiswap, Curve")
    print("Appuyez sur Ctrl+C pour arrêter")
    print("=" * 80)
    
    # ==========================================
    # PHASE D'INITIALISATION (UNE SEULE FOIS)
    # ==========================================
    print("\n")
    print("=" * 80)
    print(" PHASE D'INITIALISATION ")
    print("=" * 80)
    print("Chargement des paires les plus populaires")
    v2_pairs = get_top_pairs_v2(UNISWAP_V2, limit=NB_PAIR)
    v3_pairs = get_top_pairs_v3(UNISWAP_V3, limit=NB_PAIR)
    sushi_pairs = get_top_pairs_sushiswap(SUSHISWAP, limit=NB_PAIR)
    curve_pairs = get_top_pairs_curve(limit=NB_PAIR)
    
    v2_from_curve, v3_from_curve, sushi_from_curve = fetch_matching_pairs_for_curve(
        curve_pairs, UNISWAP_V2, UNISWAP_V3, SUSHISWAP
    )
    
    v2_pairs_dict = {p["pair_id"]: p for p in v2_pairs}
    v3_pairs_dict = {p["pair_id"]: p for p in v3_pairs}
    sushi_pairs_dict = {p["pair_id"]: p for p in sushi_pairs}
    
    for p in v2_from_curve:
        if p["pair_id"] not in v2_pairs_dict:
            v2_pairs_dict[p["pair_id"]] = p
    for p in v3_from_curve:
        if p["pair_id"] not in v3_pairs_dict:
            v3_pairs_dict[p["pair_id"]] = p
    for p in sushi_from_curve:
        if p["pair_id"] not in sushi_pairs_dict:
            sushi_pairs_dict[p["pair_id"]] = p
    
    v2_pairs = list(v2_pairs_dict.values())
    v3_pairs = list(v3_pairs_dict.values())
    sushi_pairs = list(sushi_pairs_dict.values())
    
    v2_map = {normalize_pair(p): p for p in v2_pairs}
    v3_map = {normalize_pair(p): p for p in v3_pairs}
    sushi_map = {normalize_pair(p): p for p in sushi_pairs}
    curve_map = {normalize_pair(p): p for p in curve_pairs}


    all_keys = set(v2_map.keys()) | set(v3_map.keys()) | set(sushi_map.keys()) | set(curve_map.keys())
    common_pairs = []
    pool_addresses = [] 
    pool_to_dex = {}  
    
    for key in all_keys:
        available_dexes = []
        pair_info = {
            "token0_addr": key[0],
            "token1_addr": key[1]
        }
        
        # Collecter les infos de chaque DEX
        temp_pools = []  # Pools temporaires pour cette paire
        
        # Vérifier chaque DEX
        if key in v2_map:
            v2 = v2_map[key]
            pair_info["token0"] = v2["symbol0"]
            pair_info["token1"] = v2["symbol1"]
            pair_info["v2_address"] = v2["pair_id"]
            pair_info["v2_volume_usd"] = v2["volume_usd"]
            temp_pools.append(("v2", v2["pair_id"], "Uniswap V2"))
            available_dexes.append("UniV2")
        
        if key in v3_map:
            v3 = v3_map[key]
            if "token0" not in pair_info:
                pair_info["token0"] = v3["symbol0"]
                pair_info["token1"] = v3["symbol1"]
            pair_info["v3_address"] = v3["pair_id"]
            pair_info["v3_volume_usd"] = v3["volume_usd"]
            temp_pools.append(("v3", v3["pair_id"], "Uniswap V3"))
            available_dexes.append("UniV3")
        
        if key in sushi_map:
            sushi = sushi_map[key]
            if "token0" not in pair_info:
                pair_info["token0"] = sushi["symbol0"]
                pair_info["token1"] = sushi["symbol1"]
            pair_info["sushi_address"] = sushi["pair_id"]
            pair_info["sushi_volume_usd"] = sushi["volume_usd"]
            temp_pools.append(("sushi", sushi["pair_id"], "Sushiswap"))
            available_dexes.append("Sushi")
        
        if key in curve_map:
            curve = curve_map[key]
            # Curve ne fournit pas toujours les symboles, on utilisera get_token_info plus tard
            if "token0" not in pair_info:
                # On aura besoin de récupérer les symboles via get_token_info
                pair_info["token0"] = "?"
                pair_info["token1"] = "?"
            pair_info["curve_address"] = curve["pair_id"]
            pair_info["curve_volume_usd"] = curve["volume_usd"]
            temp_pools.append(("curve", curve["pair_id"], "Curve"))
            available_dexes.append("Curve")
        
        keep_pair = len(available_dexes) >= 2
        
        if keep_pair:
            pair_info["available_dexes"] = available_dexes
            common_pairs.append(pair_info)
            
            for dex_type, pool_id, dex_name in temp_pools:
                pool_addresses.append(pool_id)
                pool_to_dex[pool_id] = dex_name
    
    # Trier par volume total
    common_pairs.sort(key=lambda x: x.get("v2_volume_usd", 0) + x.get("v3_volume_usd", 0) + x.get("sushi_volume_usd", 0) + x.get("curve_volume_usd", 0), reverse=True)
    
    for pair in common_pairs:
        if pair["token0"] not in WATCH_TOKENS:
            WATCH_TOKENS.append(pair["token0"])
        elif pair["token1"] not in WATCH_TOKENS:
            WATCH_TOKENS.append(pair["token1"])
        else:
            continue

    print(f"\nPaires comparables identifiées: {len(common_pairs)} ({len(pool_addresses)} pools)")
    print()
    print("=" * 80)
    print(" DÉBUT DU MONITORING")
    print("=" * 80)
    # ==========================================
    # BOUCLE DE MONITORING (UTILISE LA LISTE FIXE)
    # ==========================================
    iteration = 0
    total_time = 0
    
    try:
        while True:
            iteration += 1
            
            if max_iterations and iteration > max_iterations:
                print(f"\n{max_iterations} itérations complétées. Arrêt du monitoring.")
                break
            
            print(f"\n--- ITÉRATION #{iteration} ---")
            
            # Afficher le numéro de bloc actuel pour vérifier que la blockchain avance
            try:
                w3 = get_web3()
                current_block = w3.eth.block_number
                print(f"Bloc Ethereum: #{current_block}")
            except:
                current_block = None
            
            start_time = time.time()

            # Récupérer toutes les données en un seul appel batch (LISTE FIXE)
            results = batch_get_pool_data(pool_addresses)
            
            # Ajouter l'information du DEX à chaque résultat
            for result in results:
                if result['pool_address'] in pool_to_dex:
                    if result['version'] == 'V2':
                        result['dex'] = pool_to_dex[result['pool_address']]
                    # V3 est toujours Uniswap pour l'instant
                    elif result['version'] == 'V3':
                        result['dex'] = 'Uniswap V3'
                    # Curve est déjà marqué comme 'Curve' dans batch_get_pool_data
                    elif result['version'] == 'Curve':
                        result['dex'] = 'Curve'

            end_time = time.time()
            iteration_time = end_time - start_time
            total_time += iteration_time
            avg_time = total_time / iteration
            
            # Afficher les résultats
            print(f"\nRésultats: {len(results)}/{len(pool_addresses)} pools récupérés en {iteration_time:.2f}s")
            print(f"Temps moyen: {avg_time:.2f}s/itération")
            
            if results:
                # Trier les résultats par paire pour faciliter la lecture
                results_sorted = sorted(results, key=lambda x: (x['pair'], x.get('dex', 'Unknown'), x['version']))
                
                # Afficher les prix actuels avec haute précision
                print(f"\n{'Paire':<18} {'DEX':<14} {'Version':<10} {'Prix (haute précision)':<25} {'Variation':<12} {'Liquidité/Réserves'}")
                print("-" * 110)
                
                # Stocker les prix précédents pour calculer les variations
                if not hasattr(monitor_pools_continuously, 'previous_prices'):
                    monitor_pools_continuously.previous_prices = {}
                
                for pool in results_sorted:
                    dex_name = pool.get('dex', 'Unknown')
                    version_info = f"{pool['version']}"
                    if pool['version'] == 'V3':
                        version_info += f" ({pool['fee_tier']/10000:.2f}%)"
                    elif pool['version'] == 'V2':
                        fee_pct = pool.get('fee_percentage', 0.3)
                        version_info += f" ({fee_pct:.2f}%)"
                    elif pool['version'] == 'Curve':
                        fee_pct = pool.get('fee_percentage', 0.04)
                        version_info += f" ({fee_pct:.2f}%)"
                    
                    # Afficher avec 8 décimales au lieu de 6
                    price_str = f"{pool['price_0_in_1']:.8f}"
                    
                    # Calculer la variation par rapport à l'itération précédente
                    pool_key = f"{pool['pool_address']}"
                    variation_str = ""
                    if pool_key in monitor_pools_continuously.previous_prices:
                        prev_price = monitor_pools_continuously.previous_prices[pool_key]
                        if prev_price > 0:
                            variation = ((pool['price_0_in_1'] - prev_price) / prev_price) * 100
                            if abs(variation) > 0.0001:  # Afficher si variation > 0.0001%
                                arrow = "↑" if variation > 0 else "↓"
                                variation_str = f"{arrow}{abs(variation):.4f}%"
                            else:
                                variation_str = "stable"
                        else:
                            variation_str = "N/A"
                    else:
                        variation_str = "initial"
                    
                    # Stocker le prix actuel
                    monitor_pools_continuously.previous_prices[pool_key] = pool['price_0_in_1']
                    
                    if pool['version'] == 'V2' or pool['version'] == 'Curve':
                        liquidity_str = f"R: {pool['token0']['reserve']:.2f}/{pool['token1']['reserve']:.2f}"
                    else:
                        liquidity_str = f"L: {pool['liquidity']:,}"
                    
                    print(f"{pool['pair']:<18} {dex_name:<14} {version_info:<10} {price_str:<25} {variation_str:<12} {liquidity_str}")
                
                # Analyser les opportunités d'arbitrage
                opportunities = find_arbitrage_opportunities(results)
                ACTUAL_PRICES = { (p['pool_address']): p['price_0_in_1'] for p in results }
            
                if opportunities:
                    print(f"\n OPPORTUNITÉS D'ARBITRAGE DÉTECTÉES ({len(opportunities)})")
                    print("-" * 80)
                    
                    # Afficher les opportunités
                    for opp in opportunities:
                        buy_dex = opp['buy_pool'].get('dex', 'Unknown')
                        sell_dex = opp['sell_pool'].get('dex', 'Unknown')
                        print(f"  {opp['pair']}: {opp['net_profit_percentage']:.4f}% net")
                        print(f"    Acheter: {buy_dex} {opp['buy_pool']['version']} @ {opp['buy_pool']['price_0_in_1']:.6f}")
                        print(f"    Vendre:  {sell_dex} {opp['sell_pool']['version']} @ {opp['sell_pool']['price_0_in_1']:.6f}")
                        print(f"    Profit brut: {opp['gross_profit_percentage']:.4f}% | Fees: {opp['total_fees_percentage']:.4f}%")
                    
                    # Vérification rapide de balance avant sauvegarde/exécution
                    if not DRY_RUN:
                        try:
                            w3 = get_web3()
                            account = w3.eth.account.from_key(PRIVATE_KEY)
                            quick_check = check_eth_balance(w3, account.address, safety_multiplier=1.5)
                            
                            if not quick_check['has_enough']:
                                print(f"\n AVERTISSEMENT: Balance ETH insuffisante!")
                                print(f"   Disponible: {quick_check['balance_eth']:.6f} ETH")
                                print(f"   Requis: {quick_check['required_eth']:.6f} ETH")
                                print(f"   Les transactions seront simulées uniquement jusqu'à recharge du wallet")
                                # Ne pas exécuter en mode réel
                                continue
                            else:
                                print(f"\n Balance ETH OK: {quick_check['balance_eth']:.6f} ETH disponible")
                                print( "lancement du contrat solidity")
                                execute_arbitrage(opportunities, contract_address=CONTRACT_ADDRESS)
                        except Exception as e:
                            print(f"\n Impossible de vérifier la balance: {e}")
                    
                    # Sauvegarder en base
                    print("\n Sauvegarde des résultats en base de données...")
                    save_to_database(results, opportunities, current_block)
                    
                    # Exécuter l'arbitrage avec vérification de balance intégrée
                    if opportunities:
                        print("\n Envoi du contrat d'arbitrage pour exécution")
                        execute_arbitrage(opportunities, contract_address=CONTRACT_ADDRESS)

                else:
                    print(f"\n Aucune opportunité d'arbitrage rentable pour le moment.")          
            else:
                print(" ERREUR: Aucune donnée récupérée")
            
            # Attendre avant la prochaine itération
            if max_iterations is None or iteration < max_iterations:
                print(f"\nProchaine itération dans {interval_seconds}s...")
                time.sleep(interval_seconds)
    
    except KeyboardInterrupt:
        print(f"\n\nArrêt du monitoring après {iteration} itérations")
        print(f"Temps total: {total_time:.2f}s | Temps moyen: {avg_time:.2f}s/itération")
    
    except Exception as e:
        print(f"\n ERREUR: {e}")
        import traceback
        traceback.print_exc()

##############################################################################################################################################################################
# FONCTION DÉTECTION D'OPPORTUNITÉS D'ARBITRAGE (RÉUTILISABLE)
##############################################################################################################################################################################

def find_arbitrage_opportunities(pool_data_list: List[Dict], min_profit_percentage: float = PROFIT_NET) -> List[Dict]:
    """
    Trouve les opportunités d'arbitrage entre les pools
    
    Args:
        pool_data_list: Liste des données de pools
        min_profit_percentage: Profit net minimum requis (en %)
    
    Returns:
        Liste des opportunités d'arbitrage détectées
    """
    # Grouper par paire de tokens
    pairs = {}
    for pool in pool_data_list:
        pair = pool['pair']
        if pair not in pairs:
            pairs[pair] = []
        pairs[pair].append(pool)
    
    opportunities = []
    
    for pair, pools in pairs.items():
        if len(pools) < 2:
            continue
        
        # Trouver les prix min/max pour cette paire
        pools_sorted = sorted(pools, key=lambda x: x['price_0_in_1'])
        min_price_pool = pools_sorted[0]
        max_price_pool = pools_sorted[-1]
        
        price_diff = max_price_pool['price_0_in_1'] - min_price_pool['price_0_in_1']
        percentage_diff = (price_diff / min_price_pool['price_0_in_1']) * 100
        
        # Calculer les fees combinés
        fee1 = min_price_pool.get('fee_tier', 3000) / 10000
        fee2 = max_price_pool.get('fee_tier', 3000) / 10000
        total_fees = (fee1 + fee2) * 100
        net_profit = percentage_diff - total_fees
        
        if net_profit > min_profit_percentage:
            opportunities.append({
                'pair': pair,
                'buy_pool': min_price_pool,
                'sell_pool': max_price_pool,
                'gross_profit_percentage': percentage_diff,
                'total_fees_percentage': total_fees,
                'net_profit_percentage': net_profit
            })
    
    # Trier par profit net décroissant
    opportunities.sort(key=lambda x: x['net_profit_percentage'], reverse=True)
    
    return opportunities

def calculate_min_amount_out(amount_out_expected: int, slippage_tolerance: float = DEFAULT_SLIPPAGE_TOLERANCE) -> int:
    """
    Calcule le montant minimum acceptable avec protection slippage
    
    Args:
        amount_out_expected: Montant attendu en sortie
        slippage_tolerance: Tolérance de slippage (0.005 = 0.5%)
    
    Returns:
        Montant minimum accepté (avec protection)
    """
    if amount_out_expected <= 0:
        return 0
    
    # Appliquer la tolérance de slippage
    min_amount = int(amount_out_expected * (1 - slippage_tolerance))
    
    return max(min_amount, 1)  # Jamais 0


def calculate_price_impact(amount_in: int, reserve_in: float, reserve_out: float, decimals_in: int, decimals_out: int) -> float:
    """
    Calcule l'impact de prix d'un swap (pour V2)
    
    Returns:
        Impact de prix en pourcentage (0.05 = 5%)
    """
    if reserve_in <= 0 or reserve_out <= 0:
        return float('inf')
    
    # Convertir en format lisible
    amount_in_formatted = amount_in / (10 ** decimals_in)
    
    # Calculer le nouveau ratio après le swap
    new_reserve_in = reserve_in + amount_in_formatted
    ratio_change = amount_in_formatted / reserve_in
    
    return ratio_change

#############################################################################################################################################################
# GESTION DU SLIPPAGE ET CALCULS DE SÉCURITÉ
#############################################################################################################################################################

def calculate_expected_output_v2(
    amount_in: int,
    reserve_in: float,
    reserve_out: float,
    fee_percentage: float = 0.3
) -> int:
    """
    Calcule l'output attendu pour un swap V2 selon la formule x*y=k
    
    Formula: amountOut = (amountIn * 997 * reserveOut) / (reserveIn * 1000 + amountIn * 997)
    (pour 0.3% de fees)
    
    Args:
        amount_in: Montant en entrée (en wei/unités brutes)
        reserve_in: Réserve du token en entrée
        reserve_out: Réserve du token en sortie
        fee_percentage: Pourcentage de fees (0.3 pour Uniswap V2)
    
    Returns:
        Montant attendu en sortie (en wei/unités brutes)
    """
    if reserve_in <= 0 or reserve_out <= 0:
        return 0
    
    # Convertir le fee en basis points (0.3% = 9970/10000)
    fee_multiplier = 10000 - int(fee_percentage * 100)
    fee_denominator = 10000
    
    # Formule AMM : amountOut = (amountIn * fee * reserveOut) / (reserveIn * 10000 + amountIn * fee)
    amount_in_with_fee = amount_in * fee_multiplier
    numerator = amount_in_with_fee * reserve_out
    denominator = (reserve_in * fee_denominator) + amount_in_with_fee
    
    amount_out = int(numerator / denominator)
    
    return amount_out


def calculate_expected_output_v3(
    w3,
    token_in: str,
    token_out: str,
    fee: int,
    amount_in: int
) -> int:
    """
    Calcule l'output attendu pour un swap V3 via le Quoter
    
    Args:
        w3: Instance Web3
        token_in: Adresse du token d'entrée
        token_out: Adresse du token de sortie
        fee: Fee tier (500, 3000, 10000)
        amount_in: Montant en entrée
    
    Returns:
        Montant attendu en sortie
    """
    try:
        quoter_contract = w3.eth.contract(
            address=Web3.to_checksum_address(UNISWAP_V3_QUOTER_ADDRESS),
            abi=UNISWAP_V3_QUOTER_ABI
        )
        
        amount_out = quoter_contract.functions.quoteExactInputSingle(
            Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out),
            fee,
            amount_in,
            0  # sqrtPriceLimitX96 = 0 (pas de limite)
        ).call()
        
        return amount_out
        
    except Exception as e:
        print(f"  Erreur calcul output V3: {e}")
        return 0


def calculate_expected_output_curve(
    w3,
    pool_address: str,
    i: int,
    j: int,
    amount_in: int
) -> int:
    """
    Calcule l'output attendu pour un swap Curve via get_dy()
    
    Args:
        w3: Instance Web3
        pool_address: Adresse du pool Curve
        i: Index du token d'entrée
        j: Index du token de sortie
        amount_in: Montant en entrée
    
    Returns:
        Montant attendu en sortie
    """
    try:
        pool_contract = w3.eth.contract(
            address=Web3.to_checksum_address(pool_address),
            abi=CURVE_POOL_ABI
        )
        
        amount_out = pool_contract.functions.get_dy(i, j, amount_in).call()
        return amount_out
        
    except Exception as e:
        print(f"  Erreur calcul output Curve: {e}")
        return 0


def calculate_min_amount_out_with_slippage(
    expected_amount_out: int,
    slippage_tolerance: float = 0.005  # 0.5% par défaut
) -> int:
    """
    Applique la tolérance de slippage sur le montant attendu
    
    Args:
        expected_amount_out: Montant attendu en sortie
        slippage_tolerance: Tolérance (0.005 = 0.5%, 0.01 = 1%)
    
    Returns:
        Montant minimum acceptable
    """
    if expected_amount_out <= 0:
        return 0
    
    # Appliquer le slippage
    min_amount = int(expected_amount_out * (1 - slippage_tolerance))
    
    # S'assurer que ce n'est jamais 0
    return max(min_amount, 1)


def get_expected_output_for_pool(
    w3,
    pool: Dict,
    amount_in: int,
    direction: str = 'forward'  # 'forward' = token0→token1, 'reverse' = token1→token0
) -> int:
    """
    Calcule l'output attendu selon le type de pool
    
    Args:
        w3: Instance Web3
        pool: Données du pool
        amount_in: Montant en entrée
        direction: Direction du swap
    
    Returns:
        Montant attendu en sortie
    """
    try:
        if direction == 'forward':
            token_in = pool['token0']['address']
            token_out = pool['token1']['address']
        else:
            token_in = pool['token1']['address']
            token_out = pool['token0']['address']
        
        # V2 ou Sushiswap
        if pool['version'] == 'V2':
            if direction == 'forward':
                reserve_in = pool['token0']['reserve']
                reserve_out = pool['token1']['reserve']
            else:
                reserve_in = pool['token1']['reserve']
                reserve_out = pool['token0']['reserve']
            
            # Convertir en unités brutes
            decimals_in = pool['token0']['decimals'] if direction == 'forward' else pool['token1']['decimals']
            reserve_in_raw = int(reserve_in * (10 ** decimals_in))
            reserve_out_raw = int(reserve_out * (10 ** decimals_in))
            
            fee = pool.get('fee_percentage', 0.3)
            
            return calculate_expected_output_v2(
                amount_in,
                reserve_in_raw,
                reserve_out_raw,
                fee
            )
        
        # V3
        elif pool['version'] == 'V3':
            fee_tier = pool.get('fee_tier', 3000)
            
            return calculate_expected_output_v3(
                w3,
                token_in,
                token_out,
                fee_tier,
                amount_in
            )
        
        # Curve
        elif pool['version'] == 'Curve':
            pool_address = pool['pool_address']
            i = 0 if direction == 'forward' else 1
            j = 1 if direction == 'forward' else 0
            
            return calculate_expected_output_curve(
                w3,
                pool_address,
                i,
                j,
                amount_in
            )
        
        else:
            print(f"  Type de pool inconnu: {pool['version']}")
            return 0
            
    except Exception as e:
        print(f"  Erreur calcul expected output: {e}")
        return 0


def calculate_slippage_for_arbitrage(
    w3,
    buy_pool: Dict,
    sell_pool: Dict,
    amount_in: int,
    slippage_tolerance: float = 0.005
) -> Dict:
    """
    Calcule les protections slippage pour un arbitrage complet
    
    Returns:
        Dict avec tous les montants calculés
    """
    print(f"\n  Calcul des protections slippage...")
    
    # Swap 1 : Acheter (token0 → token1)
    expected_mid = get_expected_output_for_pool(w3, buy_pool, amount_in, 'forward')
    min_mid = calculate_min_amount_out_with_slippage(expected_mid, slippage_tolerance)
    
    decimals_mid = buy_pool['token1']['decimals']
    
    print(f"    Swap 1 ({buy_pool['token0']['symbol']} → {buy_pool['token1']['symbol']}):")
    print(f"      Attendu: {expected_mid / (10**decimals_mid):.6f}")
    print(f"      Minimum: {min_mid / (10**decimals_mid):.6f} (slippage {slippage_tolerance*100}%)")
    
    # Swap 2 : Vendre (token1 → token0)
    expected_final = get_expected_output_for_pool(w3, sell_pool, expected_mid, 'reverse')
    min_final = calculate_min_amount_out_with_slippage(expected_final, slippage_tolerance)
    
    decimals_final = buy_pool['token0']['decimals']
    
    print(f"    Swap 2 ({sell_pool['token1']['symbol']} → {sell_pool['token0']['symbol']}):")
    print(f"      Attendu: {expected_final / (10**decimals_final):.6f}")
    print(f"      Minimum: {min_final / (10**decimals_final):.6f} (slippage {slippage_tolerance*100}%)")
    
    # Vérifier la rentabilité
    profit = expected_final - amount_in
    profit_pct = (profit / amount_in) * 100 if amount_in > 0 else 0
    
    is_profitable = min_final > amount_in  # Même avec slippage max
    
    print(f"\n    Profit attendu: {profit / (10**decimals_final):.6f} ({profit_pct:.4f}%)")
    print(f"    Rentable après slippage: {'✅ OUI' if is_profitable else '❌ NON'}")
    
    return {
        'swap1': {
            'expected': expected_mid,
            'minimum': min_mid
        },
        'swap2': {
            'expected': expected_final,
            'minimum': min_final
        },
        'is_profitable': is_profitable,
        'expected_profit': profit,
        'expected_profit_pct': profit_pct
    }

#############################################################################################################################################
# FONCTION D'EXECUTION D'ARBITRAGE ATOMIQUE
#############################################################################################################################################

def load_contract(contract_address=None, abi_file='MultiDexArbitrage_ABI.json'):
    """Charge un contrat déjà déployé"""
    
    # Si pas d'adresse fournie, lire depuis le fichier
    if contract_address is None:
        try:
            with open('contract_address.txt', 'r') as f:
                contract_address = f.read().strip()
        except FileNotFoundError:
            raise Exception("Pas d'adresse de contrat. Déployez d'abord le contrat.")
    
    with open(abi_file, 'r') as f:
        abi = json.load(f)
    
    w3 = get_web3()
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(contract_address),
        abi=abi
    )
    return contract
    
def encode_swap_params(dex_type, router, token_in, token_out, amount_in, min_amount_out, extra_data=b''):
    from eth_abi import encode
    
    # Encoder extra_data selon le type de DEX
    if dex_type == 1:  # Uniswap V3
        if isinstance(extra_data, int):
            extra_data = encode(['uint24'], [extra_data])
    elif dex_type == 2:  # Curve
        if isinstance(extra_data, tuple) and len(extra_data) == 2:
            # extra_data = (i, j) pour Curve
            extra_data = encode(['int128', 'int128'], extra_data)
        else:
            # Par défaut: (0, 1) pour token0 → token1
            extra_data = encode(['int128', 'int128'], [0, 1])
    
    return {
        'dexType': dex_type,
        'router': Web3.to_checksum_address(router),
        'tokenIn': Web3.to_checksum_address(token_in),
        'tokenOut': Web3.to_checksum_address(token_out),
        'amountIn': amount_in,
        'minAmountOut': min_amount_out,
        'extraData': extra_data
    }

def execute_total_arbitrage(opportunities: List[Dict], contract_address: str = None, borrow_amount: int = WALLET_BALANCE):
    receipts =[]
    for operation in opportunities:
        receipt, used = execute_arbitrage(operation, contract_address, borrow_amount)
        borrow_amount -= used
        receipts.append(receipt)
    return receipts

def execute_arbitrage(best_opp: Dict, contract_address: str = None, borrow_amount: int = None):

    if not best_opp:
        print("  Aucune opportunité à exécuter")
        return None
    
    buy_pool = best_opp['buy_pool']
    sell_pool = best_opp['sell_pool']
    
    token_buy = buy_pool['token0']['address']
    token_sell = buy_pool['token1']['address']
    decimals_buy = buy_pool['token0']['decimals']
    decimals_sell = buy_pool['token1']['decimals']
    
    if borrow_amount is None:
        borrow_amount = 1000 * (10 ** decimals_buy)
    
    print(f"\n{' [SIMULATION]' if DRY_RUN == 'true' else ' [RÉEL]'} Exécution arbitrage:")
    print(f"   Paire: {best_opp['pair']}")
    print(f"   Achat: {buy_pool['dex']} {buy_pool['version']}")
    print(f"   Vente: {sell_pool['dex']} {sell_pool['version']}")
    print(f"   Profit net estimé: {best_opp['net_profit_percentage']:.4f}%")
    print(f"   Montant: {borrow_amount / (10 ** decimals_buy):.2f} {buy_pool['token0']['symbol']}")
    
    print("\n" + "=" * 80)
    print(" SIMULATION PRÉALABLE DES SWAPS")
    print("=" * 80)
    
    print(f"\n  Simulation SWAP 1 (Achat sur {buy_pool['dex']}):")
    print(f"    IN:  {borrow_amount / (10 ** decimals_buy):.2f} {buy_pool['token0']['symbol']}")
    
    try:
        w3 = get_web3()
        
        if buy_pool['version'] == 'V2':
            router = SUSHISWAP_ROUTER_ADDRESS if 'Sushi' in buy_pool['dex'] else UNISWAP_V2_ROUTER_ADDRESS
            result1 = simulate_swap_v2_router(borrow_amount, token_buy, token_sell, router)
            amount_mid = result1['amount_out'] if result1 else 0
        elif buy_pool['version'] == 'V3':
            result1 = simulate_swap_v3_quoter(token_buy, token_sell, buy_pool['fee_tier'], borrow_amount)
            amount_mid = result1['amount_out'] if result1 else 0
        elif buy_pool['version'] == 'Curve':
            amount_mid = get_curve_price_direct(w3, buy_pool['pool_address'], 0, 1, borrow_amount)
        else:
            print(f"     Type de DEX non supporté: {buy_pool['version']}")
            return None
        
        if amount_mid == 0:
            print(f"     SWAP 1 échouerait - ARRÊT")
            return {'success': False, 'reason': 'swap1_failed_simulation'}
        
        print(f"     OUT: {amount_mid / (10 ** decimals_sell):.6f} {buy_pool['token1']['symbol']}")
        
    except Exception as e:
        print(f"     Erreur simulation swap 1: {e}")
        return None
    
    print(f"\n  Simulation SWAP 2 (Vente sur {sell_pool['dex']}):")
    print(f"    IN:  {amount_mid / (10 ** decimals_sell):.6f} {sell_pool['token1']['symbol']}")
    
    try:
        if sell_pool['version'] == 'V2':
            router = SUSHISWAP_ROUTER_ADDRESS if 'Sushi' in sell_pool['dex'] else UNISWAP_V2_ROUTER_ADDRESS
            result2 = simulate_swap_v2_router(amount_mid, token_sell, token_buy, router)
            amount_final = result2['amount_out'] if result2 else 0
        elif sell_pool['version'] == 'V3':
            result2 = simulate_swap_v3_quoter(token_sell, token_buy, sell_pool['fee_tier'], amount_mid)
            amount_final = result2['amount_out'] if result2 else 0
        elif sell_pool['version'] == 'Curve':
            amount_final = get_curve_price_direct(w3, sell_pool['pool_address'], 1, 0, amount_mid)
        else:
            print(f"     Type de DEX non supporté: {sell_pool['version']}")
            return None
        
        if amount_final == 0:
            print(f"     SWAP 2 échouerait - ARRÊT")
            return {'success': False, 'reason': 'swap2_failed_simulation'}
        
        print(f"     OUT: {amount_final / (10 ** decimals_buy):.6f} {sell_pool['token0']['symbol']}")
        
    except Exception as e:
        print(f"     Erreur simulation swap 2: {e}")
        return None
    
    # Calcul du profit simulé
    profit_amount = amount_final - borrow_amount
    profit_formatted = profit_amount / (10 ** decimals_buy)
    profit_pct = (profit_amount / borrow_amount) * 100
    
    print(f"\n   RÉSULTAT SIMULATION:")
    print(f"     Investi:  {borrow_amount / (10 ** decimals_buy):.6f}")
    print(f"     Récupéré: {amount_final / (10 ** decimals_buy):.6f}")
    print(f"     Profit:   {profit_formatted:.6f} ({profit_pct:+.4f}%)")
    
    # Vérifier rentabilité
    if amount_final <= borrow_amount:
        print(f"\n     NON RENTABLE après simulation - ARRÊT")
        return {'success': False, 'reason': 'not_profitable_after_simulation'}
    
    print(f"     Arbitrage RENTABLE après simulation")
    
    SLIPPAGE_TOLERANCE = 0.005  # 0.5%
    
    min_amount_mid = int(amount_mid * (1 - SLIPPAGE_TOLERANCE))
    min_amount_final = int(amount_final * (1 - SLIPPAGE_TOLERANCE))
    
    print(f"\n    PROTECTION SLIPPAGE ({SLIPPAGE_TOLERANCE*100}%):")
    print(f"     Swap 1 minimum: {min_amount_mid / (10**decimals_sell):.6f}")
    print(f"     Swap 2 minimum: {min_amount_final / (10**decimals_buy):.6f}")
    
    if min_amount_final <= borrow_amount:
        print(f"\n      Non rentable avec slippage max - ARRÊT")
        return {'success': False, 'reason': 'not_profitable_with_slippage'}
    
    if DRY_RUN == 'true':
        estimated_gas_cost_eth = 0.01
        estimated_gas_cost_usd = estimated_gas_cost_eth * 1800
        
        print("\n" + "=" * 80)
        print(" MODE DRY_RUN - Aucune transaction envoyée")
        print("=" * 80)
        print(f"   Coût gas estimé:  ~{estimated_gas_cost_eth:.4f} ETH (~${estimated_gas_cost_usd:.2f})")
        print(f"   Pour exécuter réellement, définissez DRY_RUN=false dans .env")
        print("=" * 80)
        
        return {
            'simulated': True,
            'success': True,
            'profit': profit_formatted,
            'profit_percentage': profit_pct,
            'amount_in': borrow_amount,
            'amount_mid': amount_mid,
            'amount_out': amount_final,
            'min_amount_mid': min_amount_mid,
            'min_amount_final': min_amount_final,
            'estimated_gas_usd': estimated_gas_cost_usd
        }
    
    print("\n" + "=" * 80)
    print(" MODE RÉEL - Vérifications avant envoi")
    print("=" * 80)
    
    account = w3.eth.account.from_key(PRIVATE_KEY)
    
    # Vérification balance ETH
    print("\n  Vérification balance ETH...")
    
    try:
        cost_estimate = estimate_transaction_cost(w3, 'TOTAL_ARBITRAGE', gas_price_multiplier=1.3)
        
        if cost_estimate:
            print(f"    Gas estimé: {cost_estimate['gas_units']:,} unités")
            print(f"    Gas price: {cost_estimate['gas_price_gwei']:.2f} gwei")
            print(f"    Coût ETH: {cost_estimate['cost_eth']:.6f} ETH")
            if cost_estimate['cost_usd']:
                print(f"    Coût USD: ~${cost_estimate['cost_usd']:.2f}")
        
        balance_check = check_eth_balance(
            w3, 
            account.address,
            required_gas_units=GAS_ESTIMATES['TOTAL_ARBITRAGE'],
            safety_multiplier=1.5
        )
        
        if not balance_check['has_enough']:
            print(f"\n     BALANCE ETH INSUFFISANTE")
            print(f"       Disponible: {balance_check['balance_eth']:.6f} ETH")
            print(f"       Requis: {balance_check['required_eth']:.6f} ETH")
            print(f"       Manque: {balance_check['shortage_eth']:.6f} ETH")
            return {
                'success': False,
                'reason': 'insufficient_balance',
                'shortage_eth': balance_check['shortage_eth']
            }
        
        print(f"     Balance OK: {balance_check['balance_eth']:.6f} ETH")
        
        if balance_check['balance_eth'] < balance_check['required_eth'] * 2:
            print(f"      Balance faible - Il restera {balance_check['balance_eth'] - balance_check['required_eth']:.6f} ETH après")
        
    except Exception as e:
        print(f"     Erreur vérification balance: {e}")
        return {
            'success': False,
            'reason': 'balance_check_failed',
            'error': str(e)
        }
    
    # Charger le contrat
    print("\n  Chargement du contrat...")
    
    if contract_address is None:
        try:
            with open('contract_address.txt', 'r') as f:
                contract_address = f.read().strip()
        except:
            print("     Impossible de trouver l'adresse du contrat")
            return {'success': False, 'reason': 'contract_address_not_found'}
    
    try:
        contract = load_contract(contract_address)
        print(f"     Contrat chargé: {contract_address[:10]}...")
    except Exception as e:
        print(f"     Erreur chargement contrat: {e}")
        return {'success': False, 'reason': 'contract_load_failed', 'error': str(e)}
    
    # Fonctions helper
    def get_dex_type(pool):
        if pool['version'] == 'V2':
            return 0
        elif pool['version'] == 'V3':
            return 1
        elif pool['version'] == 'Curve':
            return 2
        else:
            raise ValueError(f"Version inconnue: {pool['version']}")
    
    def get_router(pool):
        if pool['version'] == 'V2':
            if 'Sushi' in pool.get('dex', ''):
                return SUSHISWAP_ROUTER_ADDRESS
            else:
                return UNISWAP_V2_ROUTER_ADDRESS
        elif pool['version'] == 'V3':
            return UNISWAP_V3_ROUTER02
        elif pool['version'] == 'Curve':
            return pool['pool_address']
        else:
            raise ValueError(f"Version inconnue: {pool['version']}")
    
    print("\n  Préparation des paramètres de swap...")
    
    try:
        swap1 = encode_swap_params(
            dex_type=get_dex_type(buy_pool),
            router=get_router(buy_pool),
            token_in=token_buy,
            token_out=token_sell,
            amount_in=borrow_amount,
            min_amount_out=min_amount_mid, 
            extra_data=buy_pool.get('fee_tier', 3000) if buy_pool['version'] == 'V3' else b''
        )
        
        swap2 = encode_swap_params(
            dex_type=get_dex_type(sell_pool),
            router=get_router(sell_pool),
            token_in=token_sell,
            token_out=token_buy,
            amount_in=0,
            min_amount_out=borrow_amount, 
            extra_data=sell_pool.get('fee_tier', 3000) if sell_pool['version'] == 'V3' else b''
        )
        
        print(f"     Paramètres préparés")
        
    except Exception as e:
        print(f"     Erreur préparation paramètres: {e}")
        return {'success': False, 'reason': 'params_preparation_failed', 'error': str(e)}
    
    from eth_abi import encode
    
    arb_params = encode(
        ['tuple(tuple(uint8,address,address,address,uint256,uint256,bytes)[],uint256,uint256)'],
        [([swap1, swap2], 0, int(w3.eth.get_block('latest')['timestamp']) + 300)]
    )
    
    print("\n  Simulation finale du contrat d'arbitrage...")
    
    try:
        simulation_result = contract.functions.startFlashLoanArbitrage(
            Web3.to_checksum_address(token_buy),
            borrow_amount,
            arb_params
        ).call({
            'from': account.address,
            'gas': 2000000
        })
        
        print(f"     Contrat simulé avec succès")
        
    except Exception as e:
        print(f"     CONTRAT ÉCHOUERAIT: {e}")
        print(f"\n     TRANSACTION ANNULÉE ")
        
        # Parser l'erreur
        error_msg = str(e).lower()
        if 'insufficient' in error_msg:
            print(f"       Raison probable: Liquidité insuffisante dans un pool")
        elif 'slippage' in error_msg or 'too little' in error_msg:
            print(f"       Raison probable: Slippage trop élevé")
        elif 'allowance' in error_msg or 'transfer' in error_msg:
            print(f"       Raison probable: Problème d'approbation token")
        
        return {
            'success': False,
            'reason': 'contract_simulation_failed',
            'error': str(e)
        }
    
    # ========================================
    # ENVOI DE LA TRANSACTION RÉELLE
    # ========================================
    print("\n" + "=" * 80)
    print(" ENVOI DE LA TRANSACTION RÉELLE")
    print("=" * 80)
    
    try:
        # Construire la transaction
        print("\n  Construction de la transaction...")
        tx = contract.functions.startFlashLoanArbitrage(
            Web3.to_checksum_address(token_buy),
            borrow_amount,
            arb_params
        ).build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gas': 2000000,
            'gasPrice': w3.eth.gas_price,
            'chainId': w3.eth.chain_id
        })
        
        gas_cost_eth = (tx['gas'] * tx['gasPrice']) / 10**18
        print(f"    Gas estimé: {tx['gas']:,} unités")
        print(f"    Gas price: {tx['gasPrice'] / 10**9:.2f} gwei")
        print(f"    Coût max: {gas_cost_eth:.6f} ETH")
        
        print("\n  Signature de la transaction...")
        signed_tx = account.sign_transaction(tx)
        
        print("  Envoi de la transaction sur la blockchain...")
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        
        print(f"\n   Transaction envoyée: {tx_hash.hex()}")
        print(f"     Explorer: https://etherscan.io/tx/{tx_hash.hex()}")
        print(f"\n   Attente de confirmation (15-60 secondes)...")
        
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        
        print(f"\n   Transaction minée dans le bloc #{receipt['blockNumber']}")
        print(f"     Gas utilisé: {receipt['gasUsed']:,} unités")
        
        if receipt['status'] == 1:
            print("\n   ARBITRAGE RÉUSSI !")

            temps = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                sauvegarder_donnees(temps, receipt, best_opp)
            except:
                pass
            
            try:
                logs = contract.events.ArbitrageExecuted().process_receipt(receipt)
                for log in logs:
                    profit_real = log['args']['profit'] / (10 ** decimals_buy)
                    print(f"\n  PROFIT RÉALISÉ: {profit_real:.6f} {buy_pool['token0']['symbol']}")
                    
                actual_gas_cost = (receipt['gasUsed'] * receipt['effectiveGasPrice']) / 10**18
                print(f"     Gas payé: {actual_gas_cost:.6f} ETH")
                
            except Exception as e:
                print(f"     (Vérifiez les logs sur Etherscan pour le profit exact)")
            
            return {
                'success': True,
                'tx_hash': tx_hash.hex(),
                'receipt': receipt,
                'gas_used': receipt['gasUsed'],
                'profit_simulated': profit_formatted
            }
        
        else:
            print("\n  ARBITRAGE ÉCHOUÉ")
            print(f" La transaction a été annulée")
            print(f"     Gas perdu: {receipt['gasUsed']:,} unités")
            
            actual_gas_cost = (receipt['gasUsed'] * receipt['effectiveGasPrice']) / 10**18
            print(f"     Gas payé: {actual_gas_cost:.6f} ETH")
            
            return {
                'success': False,
                'reason': 'transaction_reverted',
                'receipt': receipt,
                'gas_lost': receipt['gasUsed']
            }
        
    except Exception as e:
        print(f"\n Erreur lors de l'envoi: {e}")
        import traceback
        traceback.print_exc()
        return {
            'success': False,
            'reason': 'transaction_send_failed',
            'error': str(e)
        }

#############################################################################################################################################################
# BASE DE DONNÉES POUR SAUVEGARDE DES RÉSULTATS
#############################################################################################################################################################

def creer_base_donnees():
    conn = sqlite3.connect('Arb_results.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS utilisateurs (
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP PRIMARY KEY,
            receipt TEXT,
            best_opp TEXT,
        )
    ''')
    
    conn.commit()
    conn.close()
    print("Base de données créée avec succès!")

def sauvegarder_donnees(timestamp, receipt, best_opp):
    conn = sqlite3.connect('Arb_results.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO utilisateurs (timestamp, receipt, best_opp)
        VALUES (?, ?, ?)
    ''', (timestamp, json.dumps(dict(receipt)), json.dumps(best_opp)))
    
    conn.commit()
    conn.close()
    print(f" Données sauvegardées: {timestamp}, receipt et best_opp")

#############################################################################################################################################################
# COMPILATION ET DÉPLOIEMENT DU CONTRAT ATOMIC ARBITRAGE
#############################################################################################################################################################

def get_router_corrected(pool):
    """Retourne le bon router selon le DEX et la version"""
    if pool['version'] == 'V2':
        if 'Sushi' in pool.get('dex', ''):
            return "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F"  # Sushiswap Router
        else:
            return "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"  # Uniswap V2 Router
    
    elif pool['version'] == 'V3':
        return "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"  # SwapRouter02 (recommandé)
    
    elif pool['version'] == 'Curve':
        return pool['pool_address']  # Pour Curve, on appelle directement le pool
    
    else:
        raise ValueError(f"Version inconnue: {pool['version']}")

def compile_contract(contract_path=r"contracts\MULTIDEX.sol"):
    """Compile le contrat Solidity"""

    print(" Installation de Solidity 0.8.19...")
    install_solc('0.8.19')
    
    print(" Compilation du contrat...")
    with open(contract_path, 'r', encoding='utf-8') as f:
        source = f.read()
    
    compiled = compile_standard({
        'language': 'Solidity',
        'sources': {contract_path: {'content': source}},
        'settings': {
            'optimizer': {
                'enabled': True,
                'runs': 200
            },
            'outputSelection': {
                '*': {'*': ['abi', 'evm.bytecode', 'evm.deployedBytecode']}
            }
        }
    }, solc_version='0.8.19')

    contract_interface = compiled['contracts'][contract_path]['MultiDexArbitrage']
    abi = contract_interface['abi']
    bytecode = contract_interface['evm']['bytecode']['object']
    
    with open('MultiDexArbitrage_ABI.json', 'w') as f:
        json.dump(abi, f, indent=2)
    
    print(" Compilation réussie!")
    return abi, bytecode

def deploy_contract(abi, bytecode):
    """Déploie le contrat sur la blockchain"""
    print("\n Déploiement du contrat...")
    w3 = get_web3()
    
    # Créer le contrat
    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)

    account = w3.eth.account.from_key(PRIVATE_KEY)
    
    # Construire la transaction de déploiement
    tx = Contract.constructor(AAVE_POOL).build_transaction({
        'from': account.address,
        'nonce': w3.eth.get_transaction_count(account.address),
        'gas': 3000000,
        'gasPrice': w3.eth.gas_price,
        'chainId': w3.eth.chain_id
    })
    
    # Signer et envoyer
    signed_tx = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    
    print(f" Transaction hash: {tx_hash.hex()}")
    print("Attente de confirmation...")
    
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    global CONTRACT_ADDRESS
    CONTRACT_ADDRESS = account.address

    if receipt['status'] == 1:
        print(f" Contrat déployé à: {receipt['contractAddress']}")
        
        # Sauvegarder l'adresse
        with open('contract_address.txt', 'w') as f:
            f.write(receipt['contractAddress'])
        
        return receipt['contractAddress']
    else:
        print(" Déploiement échoué!")
        return None

#############################################################################################################################################################
# FONCTION DE VÉRIFICATION DE LIQUIDITÉ
#############################################################################################################################################################

def pre_execution_checks(w3, account_address: str, amount_in: int, opportunity: dict) -> tuple:
    checks = []
    
    # Vérifier le solde ETH de manière détaillée
    balance_check = check_eth_balance(
        w3, 
        account_address,
        required_gas_units=GAS_ESTIMATES['TOTAL_ARBITRAGE'],
        safety_multiplier=2.0  # Marge de sécurité 100% pour pre-check
    )
    
    if not balance_check['has_enough']:
        return False, (f"Balance ETH insuffisante:\n"
                      f"  Disponible: {balance_check['balance_eth']:.6f} ETH\n"
                      f"  Requis: {balance_check['required_eth']:.6f} ETH\n"
                      f"  Manque: {balance_check['shortage_eth']:.6f} ETH")
    
    checks.append(f" Balance ETH suffisante: {balance_check['balance_eth']:.6f} ETH "
                  f"(coût estimé: {balance_check['required_eth']:.6f} ETH)")
    
    # Vérifier la liquidité des pools
    buy_pool = opportunity['buy_pool']
    sell_pool = opportunity['sell_pool']
    
    sufficient, msg = check_sufficient_liquidity(buy_pool, amount_in, 'buy')
    if not sufficient:
        return False, f"Pool d'achat: {msg}"
    checks.append(" Liquidité pool d'achat OK")
    
    sufficient, msg = check_sufficient_liquidity(sell_pool, amount_in, 'sell')
    if not sufficient:
        return False, f"Pool de vente: {msg}"
    checks.append(" Liquidité pool de vente OK")
    
    # Vérifier la rentabilité réelle
    gas_price = w3.eth.gas_price
    profit_analysis = calculate_realistic_profit(opportunity, amount_in, gas_price)
    
    if not profit_analysis['is_profitable']:
        return False, f"Non rentable après coûts: {profit_analysis['net_profit_pct']:.4f}%"
    
    if not profit_analysis['is_worth_it']:
        return False, f"Profit trop faible: {profit_analysis['net_profit_pct']:.4f}% < 0.1% minimum"
    checks.append(f" Rentabilité confirmée: {profit_analysis['net_profit_pct']:.4f}%")
    
    # Vérifier que les tokens sont bien configurés
    if not buy_pool['token0'].get('decimals') or not buy_pool['token1'].get('decimals'):
        return False, "Informations tokens incomplètes"
    checks.append(" Tokens configurés")
    
    # Vérifier les adresses
    try:
        Web3.to_checksum_address(buy_pool['pool_address'])
        Web3.to_checksum_address(sell_pool['pool_address'])
    except Exception as e:
        return False, f"Adresse pool invalide: {e}"
    checks.append(" Adresses pools valides")
    
    # Toutes les vérifications passées
    return True, "\n".join(checks)

def check_sufficient_liquidity(pool_data: dict, amount_in: int, direction: str = 'buy') -> tuple:
    """
    Vérifie qu'il y a assez de liquidité dans le pool
    
    Args:
        pool_data: Données du pool
        amount_in: Montant à échanger
        direction: 'buy' ou 'sell'
    
    Returns:
        (bool, str): (liquidité_suffisante, message)
    """
    if pool_data['version'] == 'V2' or pool_data['version'] == 'Curve':
        reserve0 = pool_data['token0'].get('reserve', 0)
        reserve1 = pool_data['token1'].get('reserve', 0)
        
        # Convertir amount_in en format lisible
        decimals = pool_data['token0']['decimals']
        amount_formatted = amount_in / (10 ** decimals)
        
        # La réserve doit être au moins 10x le montant pour éviter un impact énorme
        target_reserve = reserve1 if direction == 'buy' else reserve0
        
        if target_reserve < amount_formatted * 10:
            return False, f"Liquidité insuffisante: {target_reserve:.2f} < {amount_formatted * 10:.2f}"
        
        # Vérifier l'impact de prix
        price_impact = calculate_price_impact(
            amount_in, reserve0, reserve1,
            pool_data['token0']['decimals'],
            pool_data['token1']['decimals']
        )
        
        if price_impact > 0.05:  # 5% d'impact max
            return False, f"Impact de prix trop élevé: {price_impact*100:.2f}%"
    
    elif pool_data['version'] == 'V3':
        liquidity = pool_data.get('liquidity', 0)
        
        # Pour V3, une liquidité minimale de 100k USD est recommandée
        if liquidity < 100000:
            return False, f"Liquidité V3 insuffisante: {liquidity}"
    
    return True, "OK"

def calculate_realistic_profit(opportunity: dict, amount_in: int, current_gas_price: int) -> dict:
    """
    Calcule le profit NET réel en tenant compte de TOUS les coûts
    
    Args:
        opportunity: Opportunité d'arbitrage
        amount_in: Montant à investir
        current_gas_price: Prix du gas actuel (wei)
    
    Returns:
        dict avec profit détaillé
    """
    buy_pool = opportunity['buy_pool']
    sell_pool = opportunity['sell_pool']
    decimals = buy_pool['token0']['decimals']
    
    # 1. Profit brut (basé sur la différence de prix)
    gross_profit_pct = opportunity['gross_profit_percentage'] / 100
    gross_profit_amount = amount_in * gross_profit_pct
    
    # 2. Fees de trading
    trading_fees_pct = opportunity['total_fees_percentage'] / 100
    trading_fees = amount_in * trading_fees_pct
    
    # 3. Flash loan fee (0.09% sur Aave)
    flash_loan_fee = amount_in * AAVE_FLASH_LOAN_FEE
    
    # 4. Gas costs
    total_gas = GAS_ESTIMATES['TOTAL_ARBITRAGE']
    gas_cost_wei = total_gas * current_gas_price
    gas_cost_eth = gas_cost_wei / (10 ** 18)
    
    # Convertir en token (approximation: 1 ETH = prix ETH en token)
    # Pour simplifier, on suppose que le token vaut ~0.001 ETH
    gas_cost_token = gas_cost_eth * 1000  # À ajuster selon le token réel
    gas_cost_units = int(gas_cost_token * (10 ** decimals))
    
    # 5. Profit net
    net_profit = gross_profit_amount - trading_fees - flash_loan_fee - gas_cost_units
    net_profit_pct = (net_profit / amount_in) * 100
    
    # 6. Vérifier les seuils
    is_profitable = net_profit > 0
    min_profit_threshold = amount_in * 0.001  # Minimum 0.1% de profit
    is_worth_it = net_profit > min_profit_threshold
    
    return {
        'gross_profit': gross_profit_amount,
        'gross_profit_pct': gross_profit_pct * 100,
        'trading_fees': trading_fees,
        'flash_loan_fee': flash_loan_fee,
        'gas_cost': gas_cost_units,
        'gas_cost_eth': gas_cost_eth,
        'net_profit': net_profit,
        'net_profit_pct': net_profit_pct,
        'is_profitable': is_profitable,
        'is_worth_it': is_worth_it,
        'min_threshold': min_profit_threshold
    }

##############################################################################################################################################################
# FONCTION DE SIMULATION D'ARBITRAGE COMPLET
##############################################################################################################################################################

def simulate_full_arbitrage(w3, opportunity: dict, amount_in: int) -> dict:
    """
    Simule l'arbitrage complet avec tous les coûts
    
    Returns:
        dict avec résultats détaillés de simulation
    """
    buy_pool = opportunity['buy_pool']
    sell_pool = opportunity['sell_pool']
    
    token_in = buy_pool['token0']['address']
    token_mid = buy_pool['token1']['address']
    token_out = sell_pool['token0']['address']
    
    results = {
        'success': False,
        'steps': [],
        'final_amount': 0,
        'profit': 0,
        'profit_pct': 0
    }
    
    try:
        # Étape 1: Simuler le premier swap
        if buy_pool['version'] == 'V2':
            router = SUSHISWAP_ROUTER_ADDRESS if 'Sushi' in buy_pool['dex'] else UNISWAP_V2_ROUTER_ADDRESS
            result1 = simulate_swap_v2_router(amount_in, token_in, token_mid, router)
            amount_mid = result1['amount_out'] if result1 else 0
        elif buy_pool['version'] == 'V3':
            result1 = simulate_swap_v3_quoter(token_in, token_mid, buy_pool['fee_tier'], amount_in)
            amount_mid = result1['amount_out'] if result1 else 0
        else:
            return results
        
        if amount_mid == 0:
            results['steps'].append(('swap1', 'failed', 0))
            return results
        
        results['steps'].append(('swap1', 'success', amount_mid))
        
        # Étape 2: Simuler le second swap
        if sell_pool['version'] == 'V2':
            router = SUSHISWAP_ROUTER_ADDRESS if 'Sushi' in sell_pool['dex'] else UNISWAP_V2_ROUTER_ADDRESS
            result2 = simulate_swap_v2_router(amount_mid, token_mid, token_out, router)
            amount_final = result2['amount_out'] if result2 else 0
        elif sell_pool['version'] == 'V3':
            result2 = simulate_swap_v3_quoter(token_mid, token_out, sell_pool['fee_tier'], amount_mid)
            amount_final = result2['amount_out'] if result2 else 0
        else:
            return results
        
        if amount_final == 0:
            results['steps'].append(('swap2', 'failed', 0))
            return results
        
        results['steps'].append(('swap2', 'success', amount_final))
        
        # Calculer le profit
        profit = amount_final - amount_in
        profit_pct = (profit / amount_in) * 100 if amount_in > 0 else 0
        
        results['success'] = True
        results['final_amount'] = amount_final
        results['profit'] = profit
        results['profit_pct'] = profit_pct
        
    except Exception as e:
        results['error'] = str(e)
    
    return results

def execute_arbitrage_safe(opportunity: dict, contract_address: str, borrow_amount: int = None):
    """
    Version sécurisée de la fonction execute_arbitrage
    Avec toutes les vérifications de sécurité
    """
    
    w3 = get_web3()
    account = w3.eth.account.from_key(PRIVATE_KEY)
    
    # Déterminer le montant
    buy_pool = opportunity['buy_pool']
    decimals = buy_pool['token0']['decimals']
    
    if borrow_amount is None:
        # Commencer petit: 100 unités
        borrow_amount = 100 * (10 ** decimals)
    
    print("\n" + "="*80)
    print(" EXÉCUTION SÉCURISÉE D'ARBITRAGE")
    print("="*80)
    
    # ÉTAPE 1: Vérifications pré-exécution
    print("\n Vérifications de sécurité...")
    checks_passed, check_msg = pre_execution_checks(w3, account.address, borrow_amount, opportunity)
    
    if not checks_passed:
        print(f" ÉCHEC DES VÉRIFICATIONS:\n{check_msg}")
        return None
    
    print(f" TOUTES LES VÉRIFICATIONS PASSÉES:\n{check_msg}")
    
    # ÉTAPE 2: Simulation complète
    print("\n Simulation complète de l'arbitrage...")
    simulation = simulate_full_arbitrage(w3, opportunity, borrow_amount)
    
    if not simulation['success']:
        print(f" LA SIMULATION A ÉCHOUÉ")
        print(f"Étapes: {simulation['steps']}")
        return None
    
    print(f" SIMULATION RÉUSSIE:")
    print(f"   Profit simulé: {simulation['profit_pct']:.4f}%")
    print(f"   Montant final: {simulation['final_amount'] / (10**decimals):.6f}")
    
    # ÉTAPE 3: Calcul des coûts réels
    print("\n Calcul des coûts réels...")
    gas_price = w3.eth.gas_price
    profit_analysis = calculate_realistic_profit(opportunity, borrow_amount, gas_price)
    
    print(f"   Profit brut:      {profit_analysis['gross_profit_pct']:.4f}%")
    print(f"   Trading fees:     -{profit_analysis['trading_fees'] / borrow_amount * 100:.4f}%")
    print(f"   Flash loan fee:   -{profit_analysis['flash_loan_fee'] / borrow_amount * 100:.4f}%")
    print(f"   Gas cost:         -{profit_analysis['gas_cost'] / borrow_amount * 100:.4f}% (~{profit_analysis['gas_cost_eth']:.6f} ETH)")
    print(f"    PROFIT NET:    {profit_analysis['net_profit_pct']:.4f}%")
    
    if not profit_analysis['is_worth_it']:
        print(f"\n PROFIT TROP FAIBLE - Transaction annulée")
        return None
    
    # ÉTAPE 4: Calcul des min_amount_out avec slippage
    print("\n Protection slippage...")
    min_amount_mid = calculate_min_amount_out(simulation['steps'][0][2])
    min_amount_final = calculate_min_amount_out(simulation['steps'][1][2])
    
    print(f"   Montant min après swap 1: {min_amount_mid / (10**buy_pool['token1']['decimals']):.6f}")
    print(f"   Montant min après swap 2: {min_amount_final / (10**decimals):.6f}")
    
    if min_amount_final < borrow_amount:
        print(f" PROTECTION SLIPPAGE ÉCHOUÉE - min_amount < emprunt")
        return None
    
    # ÉTAPE 5: Confirmation finale
    print("\n" + "="*80)
    print("  PRÊT À EXÉCUTER LA TRANSACTION RÉELLE")
    print("="*80)
    print(f"Investissement: {borrow_amount / (10**decimals):.2f} {buy_pool['token0']['symbol']}")
    print(f"Profit net estimé: {profit_analysis['net_profit_pct']:.4f}%")
    print(f"Gas max: ~{profit_analysis['gas_cost_eth']:.6f} ETH")
    print("\n Envoi de la transaction...")
    
    # À partir d'ici, votre code d'exécution original
    # MAIS avec min_amount_out correctement calculé
    
    # [Suite du code d'exécution...]
    
    return {
        'prepared': True,
        'checks_passed': True,
        'simulation_success': True,
        'expected_profit': profit_analysis['net_profit_pct'],
        'min_amounts': {
            'mid': min_amount_mid,
            'final': min_amount_final
        }
    }

#############################################################################################################################################################
# VÉRIFICATION DE BALANCE ET ESTIMATION DE GAS
#############################################################################################################################################################

def check_eth_balance(w3, account_address: str, required_gas_units: int = None, gas_price_wei: int = None, 
                      safety_multiplier: float = 1.5) -> dict:
    """
    Vérifie si le compte a assez d'ETH pour payer le gas
    
    Args:
        w3: Instance Web3
        account_address: Adresse du compte à vérifier
        required_gas_units: Nombre d'unités de gas requises (None = utiliser estimation par défaut)
        gas_price_wei: Prix du gas en wei (None = utiliser le prix actuel du réseau)
        safety_multiplier: Multiplicateur de sécurité (1.5 = 150% du gas estimé)
    
    Returns:
        dict: {
            'has_enough': bool,
            'balance_eth': float,
            'balance_wei': int,
            'required_eth': float,
            'required_wei': int,
            'gas_price_gwei': float,
            'estimated_gas_units': int,
            'shortage_eth': float (si insuffisant)
        }
    """
    try:
        # Récupérer la balance actuelle
        balance_wei = w3.eth.get_balance(account_address)
        balance_eth = balance_wei / (10**18)
        
        # Récupérer le prix du gas actuel si non fourni
        if gas_price_wei is None:
            gas_price_wei = w3.eth.gas_price
        
        gas_price_gwei = gas_price_wei / (10**9)
        
        # Utiliser l'estimation par défaut si non fournie
        if required_gas_units is None:
            required_gas_units = GAS_ESTIMATES['TOTAL_ARBITRAGE']
        
        # Appliquer le multiplicateur de sécurité
        safe_gas_units = int(required_gas_units * safety_multiplier)
        
        # Calculer le coût total
        required_wei = safe_gas_units * gas_price_wei
        required_eth = required_wei / (10**18)
        
        # Vérifier si suffisant
        has_enough = balance_wei >= required_wei
        shortage_eth = max(0, required_eth - balance_eth)
        
        return {
            'has_enough': has_enough,
            'balance_eth': balance_eth,
            'balance_wei': balance_wei,
            'required_eth': required_eth,
            'required_wei': required_wei,
            'gas_price_gwei': gas_price_gwei,
            'estimated_gas_units': safe_gas_units,
            'shortage_eth': shortage_eth,
            'safety_multiplier': safety_multiplier
        }
        
    except Exception as e:
        print(f" Erreur lors de la vérification de balance: {e}")
        return {
            'has_enough': False,
            'error': str(e)
        }

def estimate_transaction_cost(w3, transaction_type: str = 'TOTAL_ARBITRAGE', 
                              gas_price_multiplier: float = 1.2) -> dict:
    """
    Estime le coût d'une transaction en ETH et USD
    
    Args:
        w3: Instance Web3
        transaction_type: Type de transaction ('TOTAL_ARBITRAGE', 'V2_SWAP', 'V3_SWAP', etc.)
        gas_price_multiplier: Multiplicateur du gas price (1.2 = +20% pour priorité)
    
    Returns:
        dict: Informations détaillées sur le coût
    """
    try:
        # Récupérer le gas price actuel
        base_gas_price = w3.eth.gas_price
        adjusted_gas_price = int(base_gas_price * gas_price_multiplier)
        
        # Récupérer l'estimation de gas
        gas_units = GAS_ESTIMATES.get(transaction_type, GAS_ESTIMATES['TOTAL_ARBITRAGE'])
        
        # Calculer les coûts
        cost_wei = gas_units * adjusted_gas_price
        cost_eth = cost_wei / (10**18)

        eth_price_usd = get_token_price_usd()
        cost_usd = cost_eth * eth_price_usd if eth_price_usd else None
        
        return {
            'gas_units': gas_units,
            'gas_price_wei': adjusted_gas_price,
            'gas_price_gwei': adjusted_gas_price / (10**9),
            'cost_wei': cost_wei,
            'cost_eth': cost_eth,
            'cost_usd': cost_usd,
            'eth_price_usd': eth_price_usd
        }
        
    except Exception as e:
        print(f" Erreur estimation coût: {e}")
        return None

def format_balance_report(balance_check: dict, cost_estimate: dict = None) -> str:
    """
    Formate un rapport lisible de la balance et des coûts
    
    Returns:
        str: Rapport formaté
    """
    if 'error' in balance_check:
        return f" Erreur: {balance_check['error']}"
    
    report = []
    report.append("\n" + "="*70)
    report.append(" RAPPORT DE BALANCE ETH")
    report.append("="*70)
    
    # Balance actuelle
    report.append(f"\n Balance Actuelle:")
    report.append(f"   {balance_check['balance_eth']:.6f} ETH")
    report.append(f"   {balance_check['balance_wei']:,} wei")
    
    # Coût estimé
    report.append(f"\n Coût Gas Estimé:")
    report.append(f"   Gas units: {balance_check['estimated_gas_units']:,}")
    report.append(f"   Gas price: {balance_check['gas_price_gwei']:.2f} gwei")
    report.append(f"   Coût total: {balance_check['required_eth']:.6f} ETH")
    report.append(f"   (avec multiplicateur sécurité x{balance_check['safety_multiplier']})")
    
    # Coût en USD si disponible
    if cost_estimate and cost_estimate.get('cost_usd'):
        report.append(f"   Coût USD: ~${cost_estimate['cost_usd']:.2f}")
    
    # Verdict
    report.append(f"\n Verdict:")
    if balance_check['has_enough']:
        surplus = balance_check['balance_eth'] - balance_check['required_eth']
        report.append(f"    BALANCE SUFFISANTE")
        report.append(f"   Surplus: {surplus:.6f} ETH")
    else:
        report.append(f"    BALANCE INSUFFISANTE")
        report.append(f"   Manque: {balance_check['shortage_eth']:.6f} ETH")
        report.append(f"   Veuillez recharger votre wallet!")
    
    report.append("="*70)
    
    return "\n".join(report)

#############################################################################################################################################################
# FONCTIONS DE TEST
#############################################################################################################################################################

def simulate_arbitrage_profit(opportunity: Dict) -> float:
    """
    Simule le profit réel en tenant compte du slippage
    
    Returns:
        Profit net estimé en USD
    """
    w3 = get_web3()
    
    buy_pool = opportunity['buy_pool']
    sell_pool = opportunity['sell_pool']
    
    # Montant à emprunter
    decimals = buy_pool['token0']['decimals']
    amount_in = 1000 * (10 ** decimals)
    
    # Simuler le swap 1 (achat)
    if buy_pool['version'] == 'V2':
        result1 = simulate_swap_v2_router(
            amount_in,
            buy_pool['token0']['address'],
            buy_pool['token1']['address'],
            UNISWAP_V2_ROUTER_ADDRESS if 'Uniswap' in buy_pool['dex'] else SUSHISWAP_ROUTER_ADDRESS
        )
        amount_mid = result1['amount_out'] if result1 else 0
    elif buy_pool['version'] == 'V3':
        result1 = simulate_swap_v3_quoter(
            buy_pool['token0']['address'],
            buy_pool['token1']['address'],
            buy_pool['fee_tier'],
            amount_in
        )
        amount_mid = result1['amount_out'] if result1 else 0
    else:
        return 0
    
    if amount_mid == 0:
        return 0
    
    # Simuler le swap 2 (vente)
    if sell_pool['version'] == 'V2':
        result2 = simulate_swap_v2_router(
            amount_mid,
            sell_pool['token0']['address'],
            sell_pool['token1']['address'],
            UNISWAP_V2_ROUTER_ADDRESS if 'Uniswap' in sell_pool['dex'] else SUSHISWAP_ROUTER_ADDRESS
        )
        amount_out = result2['amount_out'] if result2 else 0
    elif sell_pool['version'] == 'V3':
        result2 = simulate_swap_v3_quoter(
            sell_pool['token0']['address'],
            sell_pool['token1']['address'],
            sell_pool['fee_tier'],
            amount_mid
        )
        amount_out = result2['amount_out'] if result2 else 0
    else:
        return 0
    
    # Calculer le profit net
    profit = amount_out - amount_in
    profit_formatted = profit / (10 ** decimals)
    
    return profit_formatted

#############################################################################################################################################################
# DEFINITION DES METHODES POUR ECOUTE DU MEMPOOL
#############################################################################################################################################################

# Pour une écoute du mempool, il nous faut utiliser un websocket cette fois ( on n'appelle plus les résultats mais on les recoit au fur et à mesure )
url_ws = 'wss://mainnet.infura.io/ws/v3/YOUR-PROJECT-ID'

UNISWAP_V3_METHODS = {
    '0x414bf389': 'exactInputSingle',
    '0xc04b8d59': 'exactInput',
    '0xdb3e2198': 'exactOutputSingle',
    '0xf28c0498': 'exactOutput',
    '0xac9650d8': 'multicall',
    '0x5ae401dc': 'multicall'
}

# SushiSwap V2
SUSHISWAP_V2_METHODS = {
    '0x38ed1739': 'swapExactTokensForTokens',
    '0x8803dbee': 'swapTokensForExactTokens',
    '0x7ff36ab5': 'swapExactETHForTokens',
    '0x18cbafe5': 'swapTokensForExactETH',
    '0x4a25d94a': 'swapExactTokensForETH',
    '0xfb3bdb41': 'swapETHForExactTokens',
    '0x791ac947': 'swapExactTokensForETHSupportingFeeOnTransferTokens',
    '0xb6f9de95': 'swapExactETHForTokensSupportingFeeOnTransferTokens'
}

# Curve
CURVE_METHODS = {
    '0x3df02124': 'exchange',
    '0xa6417ed6': 'exchange_underlying',
    '0x5b41b908': 'exchange',
    '0x394747c5': 'exchange',
}

def decode_uniswap_v3_exact_input_single(input_data):
    """
    Décoder exactInputSingle(
        address tokenIn,
        address tokenOut,
        uint24 fee,
        address recipient,
        uint256 deadline,
        uint256 amountIn,
        uint256 amountOutMinimum,
        uint160 sqrtPriceLimitX96
    )
    """
    params = input_data[10:]
    types = ['address', 'address', 'uint24', 'address', 'uint256', 'uint256', 'uint256', 'uint160']
    decoded = w3.codec.decode(types, bytes.fromhex(params))
    
    return {
        'dex': 'Uniswap V3',
        'type': 'exactInputSingle',
        'tokenIn': decoded[0],
        'tokenOut': decoded[1],
        'fee': decoded[2],
        'recipient': decoded[3],
        'deadline': decoded[4],
        'amountIn': decoded[5],
        'amountOutMinimum': decoded[6],
        'sqrtPriceLimitX96': decoded[7]
    }

def decode_uniswap_v3_exact_input(input_data):
    """
    Décoder exactInput(
        bytes path,
        address recipient,
        uint256 deadline,
        uint256 amountIn,
        uint256 amountOutMinimum
    )
    """
    params = input_data[10:]
    types = ['bytes', 'address', 'uint256', 'uint256', 'uint256']
    decoded = w3.codec.decode(types, bytes.fromhex(params))
    
    return {
        'dex': 'Uniswap V3',
        'type': 'exactInput',
        'path': decoded[0].hex(),
        'recipient': decoded[1],
        'deadline': decoded[2],
        'amountIn': decoded[3],
        'amountOutMinimum': decoded[4]
    }

def decode_uniswap_v3_path(path_hex):
    """
    Décoder le path Uniswap V3
    Format: token0 (20 bytes) + fee (3 bytes) + token1 (20 bytes) + ...
    """
    if path_hex.startswith('0x'):
        path_hex = path_hex[2:]
    
    tokens = []
    fees = []
    position = 0
    
    while position < len(path_hex):
        token = '0x' + path_hex[position:position + 40]
        tokens.append(Web3.to_checksum_address(token))
        position += 40
        
        if position < len(path_hex):
            fee = int(path_hex[position:position + 6], 16)
            fees.append(fee)
            position += 6
    
    return tokens, fees

def decode_sushiswap_v2(input_data, method_id, tx_value):
    """Décoder les swaps SushiSwap V2"""
    params = input_data[10:]
    
    # swapExactTokensForTokens(uint amountIn, uint amountOutMin, address[] path, address to, uint deadline)
    if method_id == '0x38ed1739':
        types = ['uint256', 'uint256', 'address[]', 'address', 'uint256']
        decoded = w3.codec.decode(types, bytes.fromhex(params))
        return {
            'dex': 'SushiSwap V2',
            'type': 'swapExactTokensForTokens',
            'amountIn': decoded[0],
            'amountOutMin': decoded[1],
            'path': decoded[2],
            'to': decoded[3],
            'deadline': decoded[4]
        }
    
    # swapExactETHForTokens(uint amountOutMin, address[] path, address to, uint deadline)
    elif method_id == '0x7ff36ab5':
        types = ['uint256', 'address[]', 'address', 'uint256']
        decoded = w3.codec.decode(types, bytes.fromhex(params))
        return {
            'dex': 'SushiSwap V2',
            'type': 'swapExactETHForTokens',
            'amountIn': tx_value,
            'amountOutMin': decoded[0],
            'path': decoded[1],
            'to': decoded[2],
            'deadline': decoded[3]
        }
    
    # swapTokensForExactTokens(uint amountOut, uint amountInMax, address[] path, address to, uint deadline)
    elif method_id == '0x8803dbee':
        types = ['uint256', 'uint256', 'address[]', 'address', 'uint256']
        decoded = w3.codec.decode(types, bytes.fromhex(params))
        return {
            'dex': 'SushiSwap V2',
            'type': 'swapTokensForExactTokens',
            'amountOut': decoded[0],
            'amountInMax': decoded[1],
            'path': decoded[2],
            'to': decoded[3],
            'deadline': decoded[4]
        }
    
    # swapExactTokensForETH(uint amountIn, uint amountOutMin, address[] path, address to, uint deadline)
    elif method_id == '0x4a25d94a':
        types = ['uint256', 'uint256', 'address[]', 'address', 'uint256']
        decoded = w3.codec.decode(types, bytes.fromhex(params))
        return {
            'dex': 'SushiSwap V2',
            'type': 'swapExactTokensForETH',
            'amountIn': decoded[0],
            'amountOutMin': decoded[1],
            'path': decoded[2],
            'to': decoded[3],
            'deadline': decoded[4]
        }
    
    # swapTokensForExactETH(uint amountOut, uint amountInMax, address[] path, address to, uint deadline)
    elif method_id == '0x18cbafe5':
        types = ['uint256', 'uint256', 'address[]', 'address', 'uint256']
        decoded = w3.codec.decode(types, bytes.fromhex(params))
        return {
            'dex': 'SushiSwap V2',
            'type': 'swapTokensForExactETH',
            'amountOut': decoded[0],
            'amountInMax': decoded[1],
            'path': decoded[2],
            'to': decoded[3],
            'deadline': decoded[4]
        }
    
    # swapETHForExactTokens(uint amountOut, address[] path, address to, uint deadline)
    elif method_id == '0xfb3bdb41':
        types = ['uint256', 'address[]', 'address', 'uint256']
        decoded = w3.codec.decode(types, bytes.fromhex(params))
        return {
            'dex': 'SushiSwap V2',
            'type': 'swapETHForExactTokens',
            'amountOut': decoded[0],
            'path': decoded[1],
            'to': decoded[2],
            'deadline': decoded[3],
            'amountInMax': tx_value
        }
    
    # swapExactTokensForETHSupportingFeeOnTransferTokens
    elif method_id == '0x791ac947':
        types = ['uint256', 'uint256', 'address[]', 'address', 'uint256']
        decoded = w3.codec.decode(types, bytes.fromhex(params))
        return {
            'dex': 'SushiSwap V2',
            'type': 'swapExactTokensForETH (FeeOnTransfer)',
            'amountIn': decoded[0],
            'amountOutMin': decoded[1],
            'path': decoded[2],
            'to': decoded[3],
            'deadline': decoded[4]
        }
    
    # swapExactETHForTokensSupportingFeeOnTransferTokens
    elif method_id == '0xb6f9de95':
        types = ['uint256', 'address[]', 'address', 'uint256']
        decoded = w3.codec.decode(types, bytes.fromhex(params))
        return {
            'dex': 'SushiSwap V2',
            'type': 'swapExactETHForTokens (FeeOnTransfer)',
            'amountIn': tx_value,
            'amountOutMin': decoded[0],
            'path': decoded[1],
            'to': decoded[2],
            'deadline': decoded[3]
        }
    
    return None

def decode_curve(input_data, method_id):
    """Décoder les swaps Curve"""
    params = input_data[10:]
    
    # exchange(int128 i, int128 j, uint256 dx, uint256 min_dy)
    if method_id == '0x3df02124':
        types = ['int128', 'int128', 'uint256', 'uint256']
        decoded = w3.codec.decode(types, bytes.fromhex(params))
        return {
            'dex': 'Curve',
            'type': 'exchange',
            'i': decoded[0],
            'j': decoded[1],
            'dx': decoded[2],
            'min_dy': decoded[3]
        }
    
    # exchange_underlying(int128 i, int128 j, uint256 dx, uint256 min_dy)
    elif method_id == '0xa6417ed6':
        types = ['int128', 'int128', 'uint256', 'uint256']
        decoded = w3.codec.decode(types, bytes.fromhex(params))
        return {
            'dex': 'Curve',
            'type': 'exchange_underlying',
            'i': decoded[0],
            'j': decoded[1],
            'dx': decoded[2],
            'min_dy': decoded[3]
        }
    
    # Variante avec address
    elif method_id in ['0x5b41b908', '0x394747c5']:
        try:
            types = ['address', 'address', 'uint256', 'uint256']
            decoded = w3.codec.decode(types, bytes.fromhex(params))
            return {
                'dex': 'Curve',
                'type': 'exchange_with_address',
                'token_in': decoded[0],
                'token_out': decoded[1],
                'amount_in': decoded[2],
                'min_amount_out': decoded[3]
            }
        except:
            # Si ça échoue, essayer le format classique
            types = ['int128', 'int128', 'uint256', 'uint256']
            decoded = w3.codec.decode(types, bytes.fromhex(params))
            return {
                'dex': 'Curve',
                'type': 'exchange',
                'i': decoded[0],
                'j': decoded[1],
                'dx': decoded[2],
                'min_dy': decoded[3]
            }
    
    return None

def get_curve_pool_tokens(pool_address):
    """Mapping des tokens pour les pools Curve courants"""
    pool_tokens = {
        '0xbebc44782c7db0a1a60cb6fe97d0b483032ff1c7': [  # 3pool
            ALL_TOKENS['DAI'],
            ALL_TOKENS['USDC'],
            ALL_TOKENS['USDT']
        ],
        '0xdc24316b9ae028f1497c275eb9192a3ea0f67022': [  # stETH
            '0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84',  # stETH
            ALL_TOKENS['WETH']
        ],
        '0xd51a44d3fae010294c616388b506acda1bfaae46': [  # Tricrypto2
            ALL_TOKENS['USDT'],
            ALL_TOKENS['WBTC'],
            ALL_TOKENS['WETH']
        ],
    }
    return pool_tokens.get(pool_address.lower(), [])

def format_amount(amount, decimals=18):
    """Formater un montant de token"""
    if amount == 0:
        return "0"
    return f"{amount / (10 ** decimals):.6f}"

def get_uniswap_v3_fee_description(fee):
    """Description du fee tier Uniswap V3"""
    fee_tiers = {
        100: '0.01% (Très stable)',
        500: '0.05% (Stablecoins)',
        3000: '0.3% (Standard)',
        10000: '1% (Exotique)'
    }
    return fee_tiers.get(fee, f'{fee / 10000}%')

def shorten_address(address):
    """Raccourcir une adresse"""
    if not address:
        return ""
    return f"{address[:6]}...{address[-4:]}"

def get_token_name(address):
    """Obtenir le nom du token s'il est connu"""
    for name, addr in ALL_TOKENS.items():
        if addr.lower() == address.lower():
            return name
    return shorten_address(address)

###############################################################################################################################################################
# FONCTIONS D'AFFICHAGE DES SWAPS DÉCODÉS
###############################################################################################################################################################

def display_uniswap_v3_swap(tx, decoded):
    """Afficher un swap Uniswap V3"""
    print("\n" + "="*70)
    print(f" UNISWAP V3 - {decoded['type']}")
    print("="*70)
    print(f"Hash:      {tx.hash.hex()}")
    print(f"From:      {tx['from']}")
    print(f"Gas Price: {web3.from_wei(tx['gasPrice'], 'gwei'):.2f} Gwei")
    print(f"Time:      {datetime.now().strftime('%H:%M:%S')}")
    
    if decoded['type'] == 'exactInputSingle':
        print(f"\n Swap Simple")
        print(f"Token In:  {get_token_name(decoded['tokenIn'])} ({shorten_address(decoded['tokenIn'])})")
        print(f"Token Out: {get_token_name(decoded['tokenOut'])} ({shorten_address(decoded['tokenOut'])})")
        print(f"Fee Tier:  {get_uniswap_v3_fee_description(decoded['fee'])}")
        print(f"Amount In: {format_amount(decoded['amountIn'])}")
        print(f"Min Out:   {format_amount(decoded['amountOutMinimum'])}")
        
        # Calculer le slippage approximatif
        if decoded['amountIn'] > 0 and decoded['amountOutMinimum'] > 0:
            ratio = decoded['amountOutMinimum'] / decoded['amountIn']
            print(f"Ratio:     {ratio:.6f}")
    
    elif decoded['type'] == 'exactInput':
        tokens, fees = decode_uniswap_v3_path(decoded['path'])
        print(f"\n Swap Multi-hop ({len(tokens)} tokens)")
        
        route = " → ".join([get_token_name(t) for t in tokens])
        print(f"Route:     {route}")
        
        fee_desc = " → ".join([get_uniswap_v3_fee_description(f) for f in fees])
        print(f"Fees:      {fee_desc}")
        
        print(f"Amount In: {format_amount(decoded['amountIn'])}")
        print(f"Min Out:   {format_amount(decoded['amountOutMinimum'])}")

def display_sushiswap_v2_swap(tx, decoded):
    """Afficher un swap SushiSwap V2"""
    print("\n" + "="*70)
    print(f" SUSHISWAP V2 - {decoded['type']}")
    print("="*70)
    print(f"Hash:      {tx.hash.hex()}")
    print(f"From:      {tx['from']}")
    print(f"Gas Price: {web3.from_wei(tx['gasPrice'], 'gwei'):.2f} Gwei")
    print(f"Time:      {datetime.now().strftime('%H:%M:%S')}")
    
    if 'path' in decoded:
        route = " → ".join([get_token_name(t) for t in decoded['path']])
        print(f"\n Route:  {route}")
    
    # Afficher les montants selon le type de swap
    if 'amountIn' in decoded and decoded['amountIn'] > 0:
        if isinstance(decoded['amountIn'], int):
            print(f"Amount In: {format_amount(decoded['amountIn'])}")
        else:
            print(f"Amount In: {web3.from_wei(decoded['amountIn'], 'ether'):.6f} ETH")
    
    if 'amountOutMin' in decoded:
        print(f"Min Out:   {format_amount(decoded['amountOutMin'])}")
    
    if 'amountOut' in decoded:
        print(f"Exact Out: {format_amount(decoded['amountOut'])}")
    
    if 'amountInMax' in decoded:
        if isinstance(decoded['amountInMax'], int) and decoded['amountInMax'] > 10**18:
            print(f"Max In:    {format_amount(decoded['amountInMax'])}")
        else:
            print(f"Max In:    {web3.from_wei(decoded['amountInMax'], 'ether'):.6f} ETH")
    
    if 'to' in decoded:
        print(f"Recipient: {shorten_address(decoded['to'])}")

def display_curve_swap(tx, decoded, pool_name):
    """Afficher un swap Curve"""
    print("\n" + "="*70)
    print(f" CURVE - {pool_name}")
    print("="*70)
    print(f"Hash:      {tx.hash.hex()}")
    print(f"From:      {tx['from']}")
    print(f"Gas Price: {web3.from_wei(tx['gasPrice'], 'gwei'):.2f} Gwei")
    print(f"Time:      {datetime.now().strftime('%H:%M:%S')}")
    print(f"Type:      {decoded['type']}")
    
    pool_tokens = get_curve_pool_tokens(tx['to'])
    
    if decoded['type'] in ['exchange', 'exchange_underlying']:
        i = decoded['i']
        j = decoded['j']
        
        print(f"\n Swap")
        if pool_tokens and i < len(pool_tokens) and j < len(pool_tokens):
            token_in = get_token_name(pool_tokens[i])
            token_out = get_token_name(pool_tokens[j])
            print(f"From:      Token {i} ({token_in})")
            print(f"To:        Token {j} ({token_out})")
        else:
            print(f"From:      Token index {i}")
            print(f"To:        Token index {j}")
        
        print(f"Amount In: {format_amount(decoded['dx'])}")
        print(f"Min Out:   {format_amount(decoded['min_dy'])}")
    
    elif decoded['type'] == 'exchange_with_address':
        print(f"\n Swap")
        print(f"Token In:  {get_token_name(decoded['token_in'])}")
        print(f"Token Out: {get_token_name(decoded['token_out'])}")
        print(f"Amount In: {format_amount(decoded['amount_in'])}")
        print(f"Min Out:   {format_amount(decoded['min_amount_out'])}")

###############################################################################################################
# GESTION DES TRANSACTIONS DU MEMPOOL
###############################################################################################################

async def handle_transaction(tx_hash):
    """Traiter une transaction du mempool"""
    try:
        tx = web3.eth.get_transaction(tx_hash)
        
        if not tx or not tx['to']:
            return
        
        to_address = tx['to'].lower()
        input_data = tx['input'].hex()
        
        if len(input_data) < 10:
            return
        
        method_id = input_data[:10]
        
        #  UNISWAP V3
        if to_address in [r.lower() for r in UNISWAP_V3_ROUTER.keys()]:
            method_name = UNISWAP_V3_METHODS.get(method_id)
            if not method_name:
                return
            
            decoded = None
            
            if method_id == '0x414bf389':  # exactInputSingle
                decoded = decode_uniswap_v3_exact_input_single(input_data)
            elif method_id == '0xc04b8d59':  # exactInput
                decoded = decode_uniswap_v3_exact_input(input_data)
            
            if decoded:
                display_uniswap_v3_swap(tx, decoded)
        
        #  SUSHISWAP V2
        elif to_address == SUSHISWAP_ROUTER_ADDRESS.lower():
            method_name = SUSHISWAP_V2_METHODS.get(method_id)
            if not method_name:
                return
            
            decoded = decode_sushiswap_v2(input_data, method_id, tx['value'])
            
            if decoded:
                display_sushiswap_v2_swap(tx, decoded)
        
        #  CURVE 
        elif to_address in [p.lower() for p in CURVE_POOLS.keys()]:
            method_name = CURVE_METHODS.get(method_id)
            if not method_name:
                return
            
            pool_name = CURVE_POOLS.get(tx['to'])
            decoded = decode_curve(input_data, method_id)
            
            if decoded:
                display_curve_swap(tx, decoded, pool_name)
        
    except Exception as e:
        print(f"Erreur: {e}")
        pass


async def log_loop(event_filter, poll_interval):
    """Boucle d'écoute du mempool"""
    while True:
        try:
            for tx_hash in event_filter.get_new_entries():
                await handle_transaction(tx_hash)
            await asyncio.sleep(poll_interval)
        except Exception as e:
            print(f"Erreur dans la boucle: {e}")
            await asyncio.sleep(poll_interval)

#############################################################################################################################################################
# MAIN
#############################################################################################################################################################

if __name__ == "__main__":
    # Vérifier si le contrat existe déjà
    contract_address = None
    if os.path.exists('contract_address.txt'):
        with open('contract_address.txt', 'r') as f:
            contract_address = f.read().strip()
        print(f" Contrat existant trouvé: {contract_address}")
        
        # Vérifier qu'il existe sur la blockchain
        w3 = get_web3()
        code = w3.eth.get_code(Web3.to_checksum_address(contract_address))
        if code == b'' or code == b'0x':
            print(" Le contrat n'existe plus à cette adresse, redéploiement...")
            contract_address = None

    # Déployer seulement si nécessaire
    if contract_address is None:
        get_web3()
        print("\n Compilation et déploiement du contrat...")
        abi, bytecode = compile_contract()
        contract_address = deploy_contract(abi, bytecode)
        
        if contract_address is None:
            print(" Déploiement échoué, arrêt du programme")
            exit(1)
    # Mettre à jour la variable globale
    CONTRACT_ADDRESS = contract_address

    if  MEMPOOL_SURVEILLANCE_ENABLED:
        # Créer le filtre pour les transactions pendantes
        pending_filter = web3.eth.filter('pending')
        
        # Lancer la boucle
        loop = asyncio.get_event_loop()
        try:
            loop.run_until_complete(log_loop(pending_filter, 0.5))
        except KeyboardInterrupt:
            print("\n\n" + "="*70)
            print("👋 Arrêt du programme...")
            print("="*70)
        finally:
            loop.close()
    
    try:
        inspect_my_wallet() 
        monitor_pools_continuously(interval_seconds=INTERVAL_PAUSE, max_iterations=10)

    except KeyboardInterrupt:
        print("\n\nArrêt du programme.")
    except Exception as e:
        print(f"\nErreur: {e}")
        import traceback
        traceback.print_exc()
