import duckdb
from pathlib import Path
import pandas as pd

class Repository:
    def __init__(self, output_dir: Path|str = "output"):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        self.localdb = output_dir / "ikaros.duckdb"
        self.connection = duckdb.connect(str(self.localdb))
        self.prepare_tables()


    def prepare_tables(self):
        # securities
        self.connection.execute("""
                    CREATE TABLE IF NOT EXISTS securities (
                            asset_id INTEGER,
                            instrument_class STRING,
                            market_type STRING,
                            name STRING,
                            reference_price DOUBLE,
                            symbol STRING,
                            currency STRING
                    )
                """)

        # prices
        self.connection.execute("""
                    CREATE TABLE IF NOT EXISTS prices (
                        symbol STRING,
                        date TIMESTAMP,
                        open DOUBLE,
                        high DOUBLE,
                        low DOUBLE,
                        close DOUBLE,
                        volume DOUBLE,
                        dividend DOUBLE,
                        split_ratio DOUBLE
                    )
                """)


    def save_prices(self, prices_df: pd.DataFrame):
        self.connection.register("prices_df", prices_df)

        self.connection.execute("""
                    INSERT INTO prices (symbol, date, open, high, low, close, volume, dividend, split_ratio)
                    SELECT symbol, date, open, high, low, close, volume, dividend, split_ratio
                    FROM prices_df
                """)

        self.connection.unregister("prices_df")
