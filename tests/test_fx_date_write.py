from datetime import date
import pandas as pd
from database.snowflake_storage import SnowflakeRepository


class Cursor:
    def __init__(self):
        self.queries = []
        self.values = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, sql):
        self.queries.append(sql)

    def executemany(self, sql, rows):
        self.queries.append(sql)
        self.values = rows


class Connection:
    def __init__(self):
        self.cursor_instance = Cursor()

    def cursor(self):
        return self.cursor_instance


def test_fx_date_uses_explicit_conversion():
    repo = object.__new__(SnowflakeRepository)
    repo.connection = Connection()
    frame = pd.DataFrame(
        [
            {
                "currency_pair_id": 1,
                "symbol": "EURPLN=X",
                "date": date(2026, 10, 19),
                "open": 4.36,
                "high": 4.37,
                "low": 4.35,
                "close": 4.36,
                "volume": None,
            }
        ]
    )
    repo.save_fx_rates(frame)
    cur = repo.connection.cursor_instance
    assert "TO_TIMESTAMP_NTZ(%s, 'YYYY-MM-DD')" in cur.queries[1]
    assert cur.values[0][2] == "2026-10-19"
    assert cur.values[0][-1] is None
    assert cur.queries[0] == "BEGIN"
    assert cur.queries[-1] == "COMMIT"
