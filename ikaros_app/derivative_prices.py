"""Contract-driven, source-backed BiznesRadar derivative OHLCV backfill.

This module never infers synthetic prices, settlement or open interest.
"""

from __future__ import annotations

from datetime import date
from html import unescape
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import pandas as pd

from providers.derivatives.biznesradar import parse_history_html

SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{3,24}$")
SOURCE = "biznesradar|html_history"
BASE = "https://www.biznesradar.pl/notowania-historyczne/"
HREF = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)


def history_url(symbol: str, page: int = 1) -> str:
    if not SYMBOL.fullmatch(symbol) or page < 1:
        raise ValueError("Invalid contract symbol/page")
    return BASE + symbol + (f",{page}" if page > 1 else "")


def advertised_pages(html: str, symbol: str) -> set[int]:
    pages: set[int] = set()
    for match in HREF.finditer(unescape(html)):
        name = urlsplit(unquote(match.group(1))).path.rstrip("/").rsplit("/", 1)[-1]
        page = re.fullmatch(re.escape(symbol) + r",(\d+)", name, re.I)
        if page:
            pages.add(int(page.group(1)))
    return pages


def parse_page(html: str, symbol: str, start: date, end: date) -> list[dict]:
    # Reject HTML response with a different instrument, e.g. site redirect/error.
    if not re.search(r"(?<![A-Z0-9])" + re.escape(symbol) + r"(?![A-Z0-9])", html, re.I):
        raise ValueError(f"Page does not identify expected contract {symbol}")
    # Parse all dates, including observations after the requested end, so that
    # a page containing only newer records cannot masquerade as no history.
    try:
        frame = parse_history_html(
            html, symbol=symbol, start=date(1990, 1, 1), end=date(2100, 1, 1)
        )
    except ValueError as exc:
        if "No historical rows in requested range" in str(exc):
            return []
        raise
    rows = []
    for r in frame.itertuples(index=False):
        rows.append(
            {
                "contract_id": symbol,
                "date": r.date,
                "open": float(r.open),
                "high": float(r.high),
                "low": float(r.low),
                "close": float(r.close),
                "volume": float(r.volume),
                "open_interest": None,
                "settlement": None,
                "source": SOURCE,
            }
        )
    return rows


def collect(
    session,
    symbol: str,
    start: date,
    end: date,
    *,
    delay: float = 0.25,
    timeout: int = 25,
    max_pages: int = 100,
    html_dir: Path | None = None,
    sleep=None,
) -> tuple[list[dict], str, int]:
    """Fetch pages until historical window covered; fail closed on ambiguous pagination."""
    if start > end or max_pages < 1:
        raise ValueError("Invalid date window/max_pages")
    if sleep is None:
        from time import sleep
    collected: dict[date, dict] = {}
    prev_oldest = None
    probing_unadvertised_page = False
    for page in range(1, max_pages + 1):
        if html_dir is None:
            response = session.get(history_url(symbol, page), timeout=timeout)
            response.raise_for_status()
            html = response.text
        else:
            name = symbol + (f",{page}" if page > 1 else "") + ".html"
            path = html_dir / name
            if not path.exists():
                if page == 1:
                    raise FileNotFoundError(path)
                raise RuntimeError(f"Missing source pagination snapshot: {path}")
            html = path.read_text(encoding="utf-8")
        if not html.strip():
            raise RuntimeError(f"Empty historical page: {symbol} page={page}")
        rows = parse_page(html, symbol, start, end)
        pages = advertised_pages(html, symbol)
        if not rows:
            if page == 1:
                return [], "NO_TRADES_OR_NO_HISTORY", page
            if any(i > page for i in pages):
                raise RuntimeError(f"Empty page with further advertised pages: {symbol}")
            if probing_unadvertised_page:
                # The prior page was full but did not advertise a successor.
                # An empty guessed page does not prove the site's history is complete.
                return (
                    sorted(collected.values(), key=lambda x: x["date"]),
                    "UNVERIFIED_SOURCE_END",
                    page,
                )
            return sorted(collected.values(), key=lambda x: x["date"]), "SOURCE_EXHAUSTED", page
        oldest = min(r["date"] for r in rows)
        if prev_oldest is not None and oldest >= prev_oldest:
            raise RuntimeError(f"Non-advancing pagination: {symbol} page={page}")
        prev_oldest = oldest
        for row in rows:
            if start <= row["date"] <= end:
                old = collected.get(row["date"])
                if old and old != row:
                    raise RuntimeError(f"Conflicting overlapping quotes for {symbol} {row['date']}")
                collected[row["date"]] = row
        # Once we have reached the start of the window, earlier pages are irrelevant.
        if oldest <= start:
            return sorted(collected.values(), key=lambda x: x["date"]), "WINDOW_COVERED", page
        if not any(i > page for i in pages):
            if len(rows) >= 50:
                # Pagination controls may only show a sliding window of pages.
                # Probe page + 1 rather than declaring the source truncated.
                # The next page must advance to older observations; otherwise
                # the monotonicity check above rejects repeated pages.
                probing_unadvertised_page = True
            else:
                return (
                    sorted(collected.values(), key=lambda x: x["date"]),
                    "SOURCE_NAVIGATION_EXHAUSTED",
                    page,
                )
        else:
            probing_unadvertised_page = False
        if page == max_pages:
            raise RuntimeError(f"Maximum page limit reached for {symbol}")
        if html_dir is None and delay:
            sleep(delay)
    raise RuntimeError(f"Maximum page limit reached for {symbol}")


def registered(connection, symbols: list[str] | None = None) -> list[tuple[str, str, date]]:
    with connection.cursor() as cur:
        cur.execute(
            'SELECT "CONTRACT_ID", "INSTRUMENT_TYPE", "EXPIRY_DATE" '
            'FROM "DERIVATIVE_CONTRACTS" '
            "WHERE \"INSTRUMENT_TYPE\" IN ('FUTURE','OPTION') ORDER BY \"CONTRACT_ID\""
        )
        all_rows = [
            (str(a), str(b), c.date() if hasattr(c, "date") else c) for a, b, c in cur.fetchall()
        ]
    if symbols:
        wanted = set(symbols)
        missing = wanted - {a for a, _, _ in all_rows}
        if missing:
            raise ValueError(f"Unknown registered contracts: {', '.join(sorted(missing))}")
        return [row for row in all_rows if row[0] in wanted]
    return all_rows


def persist(connection, rows: list[dict]) -> int:
    """Batch upsert in one transaction, refusing cross-source overwrites."""
    if not rows:
        return 0
    contract_ids = {r["contract_id"] for r in rows}
    if len(contract_ids) != 1:
        raise ValueError("persist accepts exactly one contract per transaction")
    frame = pd.DataFrame(rows)
    from ingestion.derivatives import validate_prices

    frame = validate_prices(frame, contract_ids)
    with connection.cursor() as cur:
        cur.execute(
            'CREATE TEMP TABLE IF NOT EXISTS "IKAROS_DERIVATIVE_HISTORY_STAGE" ('
            '"CONTRACT_ID" VARCHAR, "DATE" DATE, "OPEN" FLOAT, "HIGH" FLOAT, '
            '"LOW" FLOAT, "CLOSE" FLOAT, "VOLUME" FLOAT, "SOURCE" VARCHAR)'
        )
        try:
            cur.execute("BEGIN")
            cur.execute('DELETE FROM "IKAROS_DERIVATIVE_HISTORY_STAGE"')
            sql = (
                'INSERT INTO "IKAROS_DERIVATIVE_HISTORY_STAGE" '
                '("CONTRACT_ID","DATE","OPEN","HIGH","LOW","CLOSE","VOLUME","SOURCE") '
                "VALUES (%s,TO_DATE(%s),%s,%s,%s,%s,%s,%s)"
            )
            params = [
                (
                    r.contract_id,
                    r.date.isoformat(),
                    float(r.open),
                    float(r.high),
                    float(r.low),
                    float(r.close),
                    float(r.volume),
                    r.source,
                )
                for r in frame.itertuples(index=False)
            ]
            cur.executemany(sql, params)
            cur.execute(
                'SELECT COUNT(*) FROM "IKAROS_DERIVATIVE_HISTORY_STAGE" s '
                'JOIN "DERIVATIVE_PRICES" t ON t."CONTRACT_ID"=s."CONTRACT_ID" '
                'AND t."DATE"=s."DATE" WHERE t."SOURCE"<>s."SOURCE" OR t."SOURCE" IS NULL'
            )
            conflicts = int(cur.fetchone()[0])
            if conflicts:
                raise ValueError(
                    f"{conflicts} existing quotes belong to another source; refusing overwrite"
                )
            cur.execute(
                'MERGE INTO "DERIVATIVE_PRICES" t USING "IKAROS_DERIVATIVE_HISTORY_STAGE" s '
                'ON t."CONTRACT_ID"=s."CONTRACT_ID" AND t."DATE"=s."DATE" '
                'WHEN MATCHED THEN UPDATE SET t."OPEN"=s."OPEN", t."HIGH"=s."HIGH", '
                't."LOW"=s."LOW",t."CLOSE"=s."CLOSE",t."VOLUME"=s."VOLUME", '
                't."INGESTED_AT"=CURRENT_TIMESTAMP() '
                "WHEN NOT MATCHED THEN INSERT "
                '("CONTRACT_ID","DATE","OPEN","HIGH","LOW","CLOSE","VOLUME",'
                '"OPEN_INTEREST","SETTLEMENT","SOURCE") VALUES '
                '(s."CONTRACT_ID",s."DATE",s."OPEN",s."HIGH",s."LOW",s."CLOSE",'
                's."VOLUME",NULL,NULL,s."SOURCE")'
            )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
    return len(params)
