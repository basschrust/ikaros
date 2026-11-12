"""Repair explicitly selected zero-volume equity candles without touching good rows.

Read-only preview by default. Replacements are from fresh OpenBB/Yahoo daily
history, or an independently verified local CSV (--csv). No prices are invented.

Example:
  uv run --extra snowflake python -m run.repair_equity_prices \
    --symbol PZU.WA --start 2026-10-26 --end 2026-11-11
  uv run --extra snowflake python -m run.repair_equity_prices \
    --symbol PZU.WA --start 2026-10-26 --end 2026-11-11 --write
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ingestion.equity_quality import suspect_zero_volume, validate_repair_candles

SELECT = (
    'SELECT TO_DATE("DATE"), "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME" '
    'FROM "PRICES" WHERE "SYMBOL"=%s AND TO_DATE("DATE") BETWEEN %s AND %s '
    'ORDER BY TO_DATE("DATE")'
)
UPDATE = (
    'UPDATE "PRICES" SET "OPEN"=%s, "HIGH"=%s, "LOW"=%s, '
    '"CLOSE"=%s, "VOLUME"=%s '
    'WHERE "SYMBOL"=%s AND TO_DATE("DATE")=%s AND "VOLUME"=0 '
)


def load_current(connection, symbol: str, start: date, end: date) -> pd.DataFrame:
    with closing(connection.cursor()) as cur:
        cur.execute(SELECT, (symbol, start.isoformat(), end.isoformat()))
        rows = cur.fetchall()
    frame = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    if frame["date"].duplicated().any():
        raise ValueError(
            f"Duplicate stored trading dates for {symbol}; manual reconciliation needed"
        )
    return frame


def plan_repair(current: pd.DataFrame, replacement: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Only replace stored zero-volume observations with verified active candles."""
    valid = validate_repair_candles(replacement, symbol)
    flagged = current.loc[suspect_zero_volume(current, symbol)].copy()
    if flagged.empty:
        return valid.iloc[0:0].copy()
    missing = set(flagged["date"]) - set(valid["date"])
    if missing:
        raise ValueError(f"Source has no usable replacement for suspect dates: {sorted(missing)}")
    planned = valid.loc[valid["date"].isin(flagged["date"])].copy()
    if set(planned["date"]) != set(flagged["date"]):
        raise ValueError("Incomplete replacement coverage")
    return planned.sort_values("date").reset_index(drop=True)


def plan_repair_partial(
    current: pd.DataFrame, replacement: pd.DataFrame, symbol: str
) -> tuple[pd.DataFrame, list[tuple[date, str]]]:
    """Prepare only valid suspect dates; report missing/invalid sources individually.

    Never repair stored normal-volume days. An ambiguous provider date or
    wrong-symbol response fails the whole operation before any write.
    """
    flagged = current.loc[suspect_zero_volume(current, symbol)].copy()
    if flagged.empty:
        return replacement.iloc[0:0].copy(), []
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    absent = required - set(replacement.columns)
    if absent:
        raise ValueError(f"Missing replacement columns: {sorted(absent)}")
    source = replacement.copy()
    if not source.empty and not source["symbol"].eq(symbol).all():
        raise ValueError("Provider returned a different symbol")
    source["date"] = pd.to_datetime(source["date"], errors="raise").dt.date
    if source["date"].duplicated().any():
        raise ValueError("Duplicate replacement dates; ambiguous provider response")
    source = source.set_index("date", drop=False)
    planned = []
    problems: list[tuple[date, str]] = []
    for day in sorted(flagged["date"]):
        if day not in source.index:
            problems.append((day, "MISSING_SOURCE"))
            continue
        row = source.loc[[day]].reset_index(drop=True)
        try:
            valid = validate_repair_candles(row, symbol)
        except (ValueError, TypeError) as exc:
            problems.append((day, f"INVALID_SOURCE: {exc}"))
            continue
        planned.append(valid)
    empty = replacement.iloc[0:0].copy()
    result = pd.concat(planned, ignore_index=True) if planned else empty
    return result, problems


def apply_repair(connection, symbol: str, plan: pd.DataFrame) -> int:
    """Transactional updates guarded by prior zero volume and unique rowcount."""
    if plan.empty:
        return 0
    with closing(connection.cursor()) as cur:
        try:
            cur.execute("BEGIN")
            for row in plan.itertuples(index=False):
                cur.execute(
                    UPDATE,
                    (
                        float(row.open),
                        float(row.high),
                        float(row.low),
                        float(row.close),
                        float(row.volume),
                        symbol,
                        row.date.isoformat(),
                    ),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(
                        f"Unexpected update count for {symbol} {row.date}: {cur.rowcount}; rolled back"
                    )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
    return len(plan)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument(
        "--csv", type=Path, help="Verified OHLCV CSV instead of current OpenBB feed"
    )
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("start must not follow end")
    if not args.symbol.endswith(".WA"):
        parser.error("This repair worker is restricted to GPW Yahoo .WA symbols")
    from config.config import load_config
    from database.repositories.factory import repository_from_config

    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("Snowflake is required")
    with repository_from_config(config) as repo:
        with closing(repo.connection.cursor()) as cur:
            cur.execute('SELECT "VENUE" FROM "SECURITIES" WHERE "SYMBOL"=%s', (args.symbol,))
            venues = cur.fetchall()
        if venues != [("GPW",)]:
            raise ValueError(f"Expected exactly one GPW security for {args.symbol}: {venues}")
        current = load_current(repo.connection, args.symbol, args.start, args.end)
        flagged = current.loc[suspect_zero_volume(current, args.symbol)]
        print(f"{args.symbol}: stored={len(current)} suspect_zero_volume={len(flagged)}")
        if flagged.empty:
            print("Nothing to repair")
            return
        if args.csv:
            raw = pd.read_csv(args.csv)
            raw.columns = [str(col).strip().lower() for col in raw.columns]
            if "symbol" not in raw.columns:
                raw["symbol"] = args.symbol
        else:
            from ingestion.prices import fetch_eod_prices

            raw = fetch_eod_prices(
                symbol=args.symbol,
                start_date=args.start.isoformat(),
                end_date=(args.end + timedelta(days=1)).isoformat(),
            )
        planned, problems = plan_repair_partial(current, raw, args.symbol)
        print(f"Valid repairs={len(planned)} unresolved={len(problems)}")
        if not planned.empty:
            print(
                planned[["date", "open", "high", "low", "close", "volume"]].to_string(index=False)
            )
        for day, reason in problems:
            print(f"UNRESOLVED {args.symbol} {day}: {reason}")
        if not args.write:
            print("DRY RUN: nothing changed. Inspect prices and rerun with --write")
            return
        if planned.empty:
            raise RuntimeError(
                "No usable replacement candles; nothing written. "
                "The current Yahoo/OpenBB feed is still defective for these dates. "
                "Supply independently verified --csv OHLCV observations."
            )
        count = apply_repair(repo.connection, args.symbol, planned)
        verified = load_current(repo.connection, args.symbol, args.start, args.end)
        remaining = verified.loc[suspect_zero_volume(verified, args.symbol)]
        print(f"REPAIRED {count} rows for {args.symbol}; remaining suspect={len(remaining)}")
        if not remaining.empty:
            raise RuntimeError(
                f"Partial repair completed; unresolved dates: {remaining['date'].tolist()}. "
                "Supply independently verified --csv OHLCV for remaining dates."
            )


if __name__ == "__main__":
    main()
