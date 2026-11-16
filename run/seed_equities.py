"""Seed GPW or US securities into existing SECURITIES, dry run by default.

CSV columns: ticker,name. US Yahoo symbols use '-' in class shares (BRK-B).
"""

from __future__ import annotations

import argparse
from pathlib import Path
import logging
import re

import pandas as pd


LOG = logging.getLogger(__name__)
VENUE_CCY = {
    "GPW": "PLN",
    "US": "USD",
    "PSE": "CZK",
    "XETRA": "EUR",
    "PARIS": "EUR",
    "LSE": "GBP",
    "BSE": "HUF",
}
VENUE_SUFFIX = {"PSE": ".PR", "XETRA": ".DE", "PARIS": ".PA", "LSE": ".L", "BSE": ".BD"}
US_TICKER = re.compile(r"^[A-Z][A-Z0-9.-]*$")


def normalize_us_securities(frame: pd.DataFrame) -> pd.DataFrame:
    if not {"ticker", "name"} <= set(frame.columns):
        raise ValueError("CSV must contain ticker,name")
    result = frame[["ticker", "name"]].copy()
    if result.isna().any().any():
        raise ValueError("Null ticker/name")
    result["symbol"] = result["ticker"].str.strip().str.upper().str.replace(".", "-", regex=False)
    result["name"] = result["name"].str.strip()
    if (result["name"] == "").any() or not result["symbol"].map(
        lambda s: bool(US_TICKER.fullmatch(s))
    ).all():
        raise ValueError("Invalid US security name/symbol")
    if result["symbol"].duplicated().any():
        raise ValueError("Duplicate Yahoo US symbols")
    return result[["symbol", "name"]].reset_index(drop=True)


def normalize_europe_securities(frame: pd.DataFrame, venue: str) -> pd.DataFrame:
    """Normalize an explicit, reviewed Yahoo listing-symbol CSV.

    Czech/German/French listings retain the Yahoo suffix; unlike US share
    classes, the dot is not replaced by a dash.
    """
    if venue not in VENUE_SUFFIX:
        raise ValueError(f"Unsupported European venue: {venue}")
    if not {"ticker", "name"} <= set(frame.columns):
        raise ValueError("CSV must contain ticker,name")
    result = frame[["ticker", "name"]].copy()
    if result.isna().any().any():
        raise ValueError("Null ticker/name")
    result["symbol"] = result["ticker"].str.strip().str.upper()
    result["name"] = result["name"].str.strip()
    suffix = VENUE_SUFFIX[venue]
    valid = result["symbol"].map(
        lambda s: bool(re.fullmatch(r"[A-Z0-9][A-Z0-9.-]*", s)) and s.endswith(suffix)
    )
    if not valid.all() or (result["name"] == "").any():
        raise ValueError(f"Invalid {venue} Yahoo ticker/name; expected suffix {suffix}")
    if result["symbol"].duplicated().any():
        raise ValueError(f"Duplicate {venue} Yahoo symbols")
    return result[["symbol", "name"]].reset_index(drop=True)


def main(argv: list[str] | None = None, *, default_venue: str = "GPW") -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venue", choices=VENUE_CCY, default=default_venue)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    defaults = {
        "GPW": "data/gpw_stocks.csv",
        "US": "data/us_sp500.csv",
        "PSE": "data/europe_pse.csv",
        "XETRA": "data/europe_xetra.csv",
        "PARIS": "data/europe_paris.csv",
        "LSE": "data/europe_lse.csv",
        "BSE": "data/europe_bse.csv",
    }
    path = args.csv or Path(defaults[args.venue])
    if not path.is_file():
        parser.error(f"Missing CSV: {path}")
    from config.config import load_config
    from database.repositories.factory import repository_from_config
    from ingestion.gpw_backfill import normalize_securities

    raw = pd.read_csv(path, dtype=str)
    securities = (
        normalize_securities(raw)
        if args.venue == "GPW"
        else normalize_us_securities(raw)
        if args.venue == "US"
        else normalize_europe_securities(raw, args.venue)
    )
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("Snowflake required")
    with repository_from_config(config) as repo:
        with repo.connection.cursor() as cur:
            cur.execute('SELECT "SYMBOL", "ASSET_ID", "VENUE", "CURRENCY" FROM "SECURITIES"')
            by_symbol = {}
            for sym, asset_id, venue, currency in cur.fetchall():
                if sym in by_symbol:
                    raise RuntimeError(f"Duplicate existing security: {sym}")
                by_symbol[str(sym)] = (int(asset_id), venue, currency)
            for sym in securities["symbol"]:
                if sym in by_symbol and by_symbol[sym][1:] != (args.venue, VENUE_CCY[args.venue]):
                    raise RuntimeError(f"Security {sym}: conflicting venue/currency")
            unseen = securities[~securities["symbol"].isin(by_symbol)]
            LOG.info(
                "venue=%s csv=%d existing=%d new=%d write=%s",
                args.venue,
                len(securities),
                len(securities) - len(unseen),
                len(unseen),
                args.write,
            )
            if not args.write or unseen.empty:
                return
            # Same sequential-ID strategy as existing GPW seeder. Do not run seeders concurrently.
            try:
                cur.execute("BEGIN")
                cur.execute('SELECT COALESCE(MAX("ASSET_ID"), 0) FROM "SECURITIES"')
                next_id = int(cur.fetchone()[0])
                params = []
                for row in unseen.itertuples(index=False):
                    next_id += 1
                    params.append(
                        (
                            next_id,
                            "stock",
                            "continuous",
                            row.name,
                            row.symbol,
                            VENUE_CCY[args.venue],
                            0,
                            None,
                            args.venue,
                        )
                    )
                cur.executemany(
                    'INSERT INTO "SECURITIES" ("ASSET_ID", "INSTRUMENT_CLASS", '
                    '"MARKET_TYPE", "NAME", "SYMBOL", "CURRENCY", "CALENDAR_ID", '
                    '"INIT_DATE", "VENUE") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    params,
                )
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
            LOG.info("Registered %d %s securities", len(unseen), args.venue)


if __name__ == "__main__":
    main()
