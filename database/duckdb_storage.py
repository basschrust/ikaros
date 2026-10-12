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
                            currency STRING,
                            calendar_id INTEGER,
                            init_date TIMESTAMP,
                            venue STRING
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

        # calendars
        self.connection.execute("""
                    CREATE TABLE IF NOT EXISTS calendars (
                        calendar_id INTEGER,
                        name STRING,
                        days_per_year INTEGER default 365,
                        init_date TIMESTAMP,
                        working_days STRING,
                        comment STRING
                    )
                """)

        # dates for the calendar - trading_days
        self.connection.execute("""
                    CREATE TABLE IF NOT EXISTS trading_days (
                        date TIMESTAMP,
                        calendar_id INTEGER,
                        comment STRING,
                        load_status STRING,
                        load_date TIMESTAMP,
                    )
                """)

        self.connection.execute("""
                    CREATE TABLE IF NOT EXISTS non_trading_days (
                        date TIMESTAMP,
                        calendar_id INTEGER,
                        comment STRING
                    )
                """)



#################################################################################################
#####################################    saving      ############################################
#################################################################################################


    # calendar fill
    def save_calendars(self, calendars_df: pd.DataFrame):
        self.connection.register("calendars_df", calendars_df)
        self.connection.execute("""
                    INSERT INTO calendars (
                                calendar_id, name, days_per_year, init_date, working_days, comment
                    )
                    SELECT calendar_id, name, days_per_year, init_date, working_days, comment
                    FROM calendars_df
                """)

        self.connection.unregister("calendars_df")


    def save_trading_days(self, trading_days_df: pd.DataFrame):
        self.connection.register("trading_days_df", trading_days_df)
        self.connection.execute("""
                            INSERT INTO trading_days (date, calendar_id, comment, load_status, load_date)
                            SELECT date, calendar_id, comment, load_status, load_date
                            FROM trading_days_df
                """)

        self.connection.unregister("trading_days_df")


    def save_non_trading_days(self, non_trading_days_df: pd.DataFrame):
        self.connection.register("non_trading_days_df", non_trading_days_df)
        self.connection.execute("""
                            INSERT INTO non_trading_days (date, calendar_id, comment)
                            SELECT date, calendar_id, comment
                            FROM non_trading_days_df
                """)

        self.connection.unregister("non_trading_days_df")


    # securities fill
    def save_securities(self, securities_df: pd.DataFrame):
        self.connection.register("securities_df", securities_df)
        self.connection.execute("""
                            INSERT INTO securities (asset_id, instrument_class, market_type, name,
                             symbol, currency, calendar_id, init_date, venue)
                            SELECT asset_id, instrument_class, market_type, name,
                             symbol, currency, calendar_id, init_date, venue
                            FROM securities_df
                """)

        self.connection.unregister("securities_df")


    # the ingest - prices & rates

    def save_prices(self, prices_df: pd.DataFrame):
        self.connection.register("prices_df", prices_df)

        self.connection.execute("""
                    INSERT INTO prices (symbol, date, open, high, low, close, volume, dividend, split_ratio)
                    SELECT symbol, date, open, high, low, close, volume, dividend, split_ratio
                    FROM prices_df
                """)

        self.connection.unregister("prices_df")


#################################################################################################
##############################          retrieving           ####################################
#################################################################################################

    def get_calendar(self, calendar_id: int):
        return self.connection.execute(query="""SELECT calendar_id, name, days_per_year, init_date, working_days, comment
                            FROM calendars 
                            WHERE calendar_id = ?
                            """, parameters=[calendar_id]).fetchdf()

    def get_trading_days(self, calendar_id: int, year: int):
        begin = f"{year}-01-01"
        end = f"{year}-12-31"
        return self.connection.execute(query="""SELECT date, calendar_id, comment, load_status, load_date
                            FROM trading_days 
                            WHERE calendar_id = ? AND date between ? and ? 
                            ORDER BY date""", parameters=[calendar_id, begin, end]).fetchdf()

    def get_securities(self, venue: str|None=None):
        return self.connection.execute(query="""SELECT asset_id, instrument_class, market_type, name, symbol,
                                    currency, calendar_id, init_date, venue
                                    FROM securities
                                    WHERE venue = ?
                    """, parameters=[venue]).fetchdf()




