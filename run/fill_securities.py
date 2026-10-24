import pandas as pd
from pathlib import Path


from database.duckdb_storage import Repository
from utils.id_generator import id_generator


def load_wig_data(
    csv_path: str | Path = "data/gpw_stocks.csv",
) -> pd.DataFrame:
    df = pd.read_csv(csv_path, dtype={"ticker": str, "name": str})

    if "ticker" not in df.columns:
        raise ValueError("Expected a 'ticker' column")

    df["symbol"] = df["ticker"] + ".WA"

    return df

    # return [f"{ticker}.WA" for ticker in tickers]


if __name__ == "__main__":
    print("Filling securities...")
    repository = Repository()

    # instruments_config = load_config("instruments.yaml")
    instruments = []

    wig_df = load_wig_data()
    wig_df["asset_id"] = [id_generator.next() for _ in range(len(wig_df))]
    wig_df["instrument_class"] = "stock"
    wig_df["market_type"] = "continuous"
    wig_df["currency"] = "PLN"
    wig_df["calendar_id"] = 0
    wig_df["init_date"] = "2025-01-01"
    wig_df["venue"] = "GPW"

    repository.save_securities(securities_df=wig_df)
