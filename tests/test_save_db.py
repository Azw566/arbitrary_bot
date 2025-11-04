import builtins
import pytest
from types import SimpleNamespace

import scripts.onchainprice as ocp


class DummyCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, query, params=None):
        # store the query and params for assertions
        self.log.append((query, params))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DummyConnection:
    def __init__(self):
        self.queries = []
        self.committed = False
        self.closed = False
        self.client_encoding = None

    def cursor(self):
        return DummyCursor(self.queries)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True

    def set_client_encoding(self, enc):
        self.client_encoding = enc


def test_save_to_database_inserts(monkeypatch):
    # Prepare dummy connection and patch safe_psycopg2_connect
    dummy = DummyConnection()

    def fake_connect(cfg):
        return dummy

    monkeypatch.setattr(ocp, 'safe_psycopg2_connect', fake_connect)

    pool = {
        'pool_address': '0xabc',
        'version': 'V2',
        'pair': 'USDC/WETH',
        'token0': {'address': '0x1', 'symbol': 'USDC', 'decimals': 6, 'reserve': 1000},
        'token1': {'address': '0x2', 'symbol': 'WETH', 'decimals': 18, 'reserve': 1},
        'price_0_in_1': 0.00025,
        'price_1_in_0': 4000,
        'fee_tier': 3000,
        'fee_percentage': 0.3
    }

    opp = {
        'pair': 'USDC/WETH',
        'buy_pool': pool,
        'sell_pool': pool,
        'gross_profit_percentage': 1.5,
        'total_fees_percentage': 0.6,
        'net_profit_percentage': 0.9
    }

    result = ocp.save_to_database([pool], [opp], block_number=123456)

    assert result is True
    # Ensure queries were executed for pool and opportunity
    executed_sql = "\n".join(q for q, p in dummy.queries if isinstance(q, str))
    assert 'INSERT INTO pool_prices' in executed_sql
    assert 'INSERT INTO arbitrage_opportunities' in executed_sql
    assert dummy.committed
    assert dummy.closed
