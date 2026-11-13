"""Review European equity candidate CSV against Yahoo/OpenBB before registration.

Reads data/candidates_<venue>.csv, writes only confirmed ticker,name pairs to
an explicitly named --approved-csv. Never connects to Snowflake or seeds data.

Example:
  uv run python -m run.review_equity_candidates --venue PSE

A successful daily candle is not proof of historical index membership. Keep
candidate source/snapshot documentation separately from security reference data.
"""

from __future__ import annotations

import argparse
import csv
import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ikaros_app.update import yesterday_warsaw

LOG = logging.getLogger(__name__)
VENUES = {
    "PSE": (".PR", "CZK"),
    "XETRA": (".DE", "EUR"),
    "PARIS": (".PA", "EUR"),
    "BSE": (".BD", "HUF"),
}


def normalized_candidates(path: Path, venue: str) -> list[tuple[str, str]]:
    if venue not in VENUES:
        raise ValueError(f"Unsupported venue: {venue}")
    suffix = VENUES[venue][0]
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not {"ticker", "name"} <= set(reader.fieldnames or []):
            raise ValueError("Expected ticker,name columns")
        seen = set()
        rows = []
        for row in reader:
            symbol = (row.get("ticker") or "").strip().upper()
            name = (row.get("name") or "").strip()
            if not symbol.endswith(suffix) or not name or symbol in seen:
                raise ValueError(f"Invalid or duplicated {venue} ticker/name: {symbol}")
            seen.add(symbol)
            rows.append((symbol, name))
    if not rows:
        raise ValueError(f"No candidates in {path}")
    return rows


def currency_for_symbol(symbol: str) -> str | None:
    """Yahoo metadata validation; missing metadata fails closed."""
    import yfinance as yf

    info = yf.Ticker(symbol).fast_info
    value = info.get("currency")
    return str(value).upper() if value else None


def eligible(
    prices: pd.DataFrame,
    *,
    through: date,
    lookback_days: int,
    min_sessions: int,
    max_stale_days: int,
) -> tuple[bool, str]:
    if prices.empty:
        return False, "NO_DATA"
    if "date" not in prices or "close" not in prices:
        return False, "MISSING_COLUMNS"
    days = pd.to_datetime(prices["date"], errors="coerce").dt.date
    close = pd.to_numeric(prices["close"], errors="coerce")
    good = (
        days.notna()
        & days.ge(through - timedelta(days=lookback_days))
        & days.le(through)
        & close.gt(0)
        & close.map(lambda x: pd.notna(x) and float("-inf") < x < float("inf"))
    )
    valid = set(days.loc[good])
    if len(valid) < min_sessions:
        return False, f"TOO_FEW_SESSIONS:{len(valid)}"
    most_recent = max(valid)
    if (through - most_recent).days > max_stale_days:
        return False, f"STALE:{most_recent}"
    return True, f"OK:{len(valid)}:{most_recent}"


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--venue", required=True, choices=VENUES)
    p.add_argument("--candidates", type=Path)
    p.add_argument("--approved-csv", type=Path)
    p.add_argument("--report", type=Path)
    p.add_argument("--through", type=date.fromisoformat, default=yesterday_warsaw())
    p.add_argument("--lookback-days", type=int, default=35)
    p.add_argument("--min-sessions", type=int, default=5)
    p.add_argument("--max-stale-days", type=int, default=7)
    p.add_argument(
        "--price-only",
        action="store_true",
        help="Bypass Yahoo currency metadata ONLY after manual review",
    )
    a = p.parse_args(argv)
    if a.lookback_days < 1 or a.min_sessions < 1 or a.max_stale_days < 0:
        p.error("Invalid lookback, session or freshness limits")
    default = f"data/candidates_{a.venue.lower()}.csv"
    candidates = a.candidates or Path(default)
    approved = a.approved_csv or Path(f"data/approved_{a.venue.lower()}.csv")
    report = a.report or Path(f"output/review_{a.venue.lower()}.csv")
    if approved.resolve() == candidates.resolve() or approved.resolve() == report.resolve():
        p.error("Candidate, approved and report files must have distinct paths")
    from ingestion.prices import fetch_eod_prices

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    rows = normalized_candidates(candidates, a.venue)
    expected_ccy = VENUES[a.venue][1]
    results = []
    accepted = []
    begin = a.through - timedelta(days=a.lookback_days)
    for symbol, name in rows:
        status = ""
        currency = ""
        try:
            history = fetch_eod_prices(
                symbol=symbol,
                start_date=begin.isoformat(),
                end_date=(a.through + timedelta(days=1)).isoformat(),
            )
            good, status = eligible(
                history,
                through=a.through,
                lookback_days=a.lookback_days,
                min_sessions=a.min_sessions,
                max_stale_days=a.max_stale_days,
            )
            if good and not a.price_only:
                currency = currency_for_symbol(symbol) or ""
                if currency != expected_ccy:
                    status = (
                        f"CURRENCY_UNVERIFIED_OR_MISMATCH:{currency or 'MISSING'}!= {expected_ccy}"
                    )
                    good = False
            if good:
                accepted.append((symbol, name))
        except Exception as exc:
            status = f"ERROR:{type(exc).__name__}:{exc}"
        LOG.info("%s %s %s", a.venue, symbol, status)
        results.append(
            (symbol, name, currency, status, "YES" if (symbol, name) in accepted else "NO")
        )
    for path in (approved, report):
        path.parent.mkdir(parents=True, exist_ok=True)
    with approved.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("ticker", "name"))
        writer.writerows(accepted)
    with report.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("ticker", "name", "currency", "detail", "approved"))
        writer.writerows(results)
    LOG.info(
        "%s reviewed=%d approved=%d rejected=%d; %s; %s",
        a.venue,
        len(rows),
        len(accepted),
        len(rows) - len(accepted),
        approved,
        report,
    )
    if not accepted:
        raise SystemExit("No candidates approved; inspect report and Yahoo metadata")


if __name__ == "__main__":
    main()
