import pytest
import json
from decimal import Decimal
from unittest.mock import patch, MagicMock
from scripts.onchainprice import save_to_database

# Import de la fonction à tester
from scripts.onchainprice import save_to_database  # <-- adapte selon ton chemin de fichier

@pytest.fixture
def sample_pools():
    return [
        {
            "pool_address": "0x123",
            "version": "v3",
            "pair": "ETH/USDC",
            "token0": {"address": "0xaaa", "symbol": "ETH", "decimals": 18, "reserve": Decimal("10.5")},
            "token1": {"address": "0xbbb", "symbol": "USDC", "decimals": 6, "reserve": Decimal("20000")},
            "price_0_in_1": Decimal("1900.5"),
            "price_1_in_0": Decimal("0.000526"),
            "fee_tier": 3000,
            "fee_percentage": 0.3,
            "liquidity": 123456.789,
            "tick": 20202,
            "sqrt_price_x96": "79228162514264337593543950336"
        }
    ]


@pytest.fixture
def sample_opps():
    return [
        {
            "pair": "ETH/USDC",
            "buy_pool": {"pool_address": "0x123", "version": "v3", "price_0_in_1": 1900.0},
            "sell_pool": {"pool_address": "0x456", "version": "v2", "price_0_in_1": 1910.0},
            "gross_profit_percentage": 0.5,
            "total_fees_percentage": 0.1,
            "net_profit_percentage": 0.4
        }
    ]


@patch("psycopg2.connect")
def test_save_to_database_success(mock_connect, sample_pools, sample_opps):
    # Mock connexion et curseur
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    # Exécution de la fonction
    result = save_to_database(sample_pools, sample_opps, block_number=123456)

    # Vérifications principales
    assert result is True
    mock_connect.assert_called_once()
    assert mock_cursor.execute.call_count >= 2  # 1 insert pool + 1 insert opp
    mock_conn.commit.assert_called()


@patch("psycopg2.connect", side_effect=Exception("DB connection failed"))
def test_save_to_database_connection_error(mock_connect, sample_pools):
    # Cas où la connexion échoue
    result = save_to_database(sample_pools, opportunities=None, block_number=999)
    assert result is False


@patch("psycopg2.connect")
def test_save_to_database_no_data(mock_connect):
    # Cas où il n'y a rien à sauvegarder
    result = save_to_database([], [])
    assert result is True
    mock_connect.assert_not_called()