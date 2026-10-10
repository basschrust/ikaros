
from pathlib import Path

import pandas as pd


def load_wig_symbols(
    csv_path: str | Path = "data/wig_constituents.csv",
) -> list[str]:
    df = pd.read_csv(csv_path, dtype={"ticker": str})

    if "ticker" not in df.columns:
        raise ValueError("Expected a 'ticker' column")

    tickers = (
        df["ticker"]
        .dropna()
        .str.strip()
        .str.upper()
    )

    tickers = tickers[tickers != ""]

    # Remove duplicates while preserving order
    tickers = tickers.drop_duplicates()

    return [f"{ticker}.WA" for ticker in tickers]
