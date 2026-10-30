"""Backfill daily WIG20 index OHLC from an authorized Stooq CSV source.

Defaults to read-only dry-run. --write performs an insert-only Snowflake MERGE.
Examples:
  uv run --extra snowflake python -m run.backfill_wig20_prices --csv data/wig20.csv
  uv run --extra snowflake python -m run.backfill_wig20_prices --csv data/wig20.csv --write
  uv run --extra snowflake python -m run.backfill_wig20_prices --write

Stooq may require an API key or a manual browser download. We do not
attempt to bypass access controls. Set STOOQ_API_KEY in a gitignored .env
or use --csv with a downloaded, authorized CSV.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from ingestion.wig20_stooq import parse_csv, check_overlaps, insert_missing

ROOT = Path(__file__).resolve().parents[1]
STOOQ_URL = "https://stooq.pl/q/d/l/"
STOOQ_KEY_PAGE = "https://stooq.pl/q/d/?s=wig20&get_apikey"


def read_key_from_environment() -> str | None:
    """Use explicit shell credentials first, then gitignored root .env."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        # Still allow an exported env var and manual CSV without python-dotenv.
        pass
    else:
        load_dotenv(ROOT / ".env", override=False)
    return os.environ.get("STOOQ_API_KEY") or None


def validate_csv_response(content: str, content_type: str = "") -> str:
    """Never treat provider HTML, quota notices or denial text as CSV."""
    first = content.lstrip("\ufeff \r\n\t")[:350].lower()
    ctype = content_type.lower()
    if ("html" in ctype or first.startswith(("<", "<!doctype"))
            or "access denied" in first or "unauthorized" in first
            or "exceeded the daily" in first or "uzyskaj apikey" in first
            or "apikey" in first and ("get_apikey" in first or "captcha" in first)):
        raise RuntimeError(
            "Stooq denied CSV download or returned an HTML/key/quota page. "
            "Obtain authorized access at " + STOOQ_KEY_PAGE + " and set STOOQ_API_KEY, "
            "or download WIG20 history in your browser and use --csv PATH. "
            "No data was written."
        )
    if not first.startswith(("date,", "data,")):
        raise RuntimeError(
            "Stooq did not return an expected daily CSV header (Date,Open,High,Low,Close...). "
            "Check source access or use --csv PATH. No data was written."
        )
    return content


def fetch_csv(start: date, end: date, *, timeout: int = 60, api_key: str | None = None) -> str:
    import requests

    params = {
        "s": "wig20",
        "d1": start.strftime("%Y%m%d"),
        "d2": end.strftime("%Y%m%d"),
        "i": "d",
    }
    if api_key:
        params["apikey"] = api_key
    try:
        response = requests.get(
            STOOQ_URL, params=params, timeout=timeout,
            headers={"User-Agent": "Ikaros/1.0 historical-index-backfill"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        # Do not leak an API key from a requests exception or its URL.
        raise RuntimeError("Stooq download failed (HTTP/network). Check authorized access "
                           "or use --csv PATH. No data was written.") from None
    return validate_csv_response(response.text, response.headers.get("Content-Type", ""))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(1994, 1, 1))
    parser.add_argument(
        "--end", type=date.fromisoformat,
        default=datetime.now(ZoneInfo("Europe/Warsaw")).date() - timedelta(days=1),
    )
    parser.add_argument("--csv", type=Path, help="Authorized local Stooq WIG20 daily CSV")
    parser.add_argument("--write", action="store_true", help="Insert missing rows into Snowflake")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--overlap-tolerance", type=float, default=0.5)
    args = parser.parse_args()
    if args.start > args.end or args.timeout < 1 or args.overlap_tolerance < 0:
        parser.error("Invalid start/end, timeout or overlap tolerance")

    if args.csv:
        content = validate_csv_response(args.csv.read_text(encoding="utf-8-sig"))
        origin = f"local CSV {args.csv}"
    else:
        content = fetch_csv(args.start, args.end, timeout=args.timeout,
                            api_key=read_key_from_environment())
        origin = "Stooq CSV endpoint"
    frame = parse_csv(content, start=args.start, end=args.end)
    print(f"WIG20 source={origin} rows={len(frame)} "
          f"first={frame.iloc[0]['date']} last={frame.iloc[-1]['date']} write={args.write}")
    print(frame.tail(5).to_string(index=False))
    if not args.write:
        print("DRY RUN: Snowflake unchanged. Add --write to insert missing dates.")
        return

    from config.config import load_config
    from database.repositories.factory import repository_from_config

    cfg = load_config("database.yaml")
    if cfg.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("Set Snowflake driver in config/database.yaml")
    with repository_from_config(cfg) as repo:
        count, mismatches = check_overlaps(repo.connection, frame,
                                           tolerance=args.overlap_tolerance)
        if mismatches:
            raise RuntimeError(
                f"Existing WIG20 closes disagree with the incoming source on "
                f"{len(mismatches)} dates; first five: {mismatches[:5]}. "
                "No writes performed."
            )
        print(f"Previously stored WIG20 dates in range: {count}; overlaps within tolerance")
        inserted = insert_missing(repo.connection, frame)
        print(f"WIG20 backfill finished: inserted={inserted} source_rows={len(frame)}")


if __name__ == "__main__":
    main()
