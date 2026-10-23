import yfinance as yf

symbols = [
    "FW20Z2620",
    "FW20Z2620.WA",
    "FW20Z26.WA",
]

for symbol in symbols:
    df = yf.download(
        symbol,
        start="2026-01-01",
        auto_adjust=False,
        progress=False,
    )
    print(symbol, len(df), "rows")
