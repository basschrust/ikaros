"""Plan incremental Snowflake market-data refreshes, without Kafka.

Latest-date catch-up; does not repair historical interior gaps.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import sys
from zoneinfo import ZoneInfo

YAHOO_FX = frozenset(
    "AUDPLN=X AUDUSD=X CHFPLN=X DKKPLN=X EURAUD=X EURCAD=X EURCHF=X "
    "EURCNY=X EURDKK=X EURGBP=X EURJPY=X EURNOK=X EURPLN=X EURSEK=X "
    "EURSGD=X EURUSD=X GBPPLN=X GBPUSD=X NZDUSD=X USDCAD=X USDCHF=X "
    "USDCNY=X USDDKK=X USDJPY=X USDNOK=X USDPLN=X USDSEK=X USDSGD=X".split()
)

# Keep FW20 selectable for old operators; DERIVATIVES supersedes it in defaults.
DATASETS = ("GPW", "US", "FX", "WIG20", "FW20", "DERIVATIVES", "RATES")
DEFAULT_DATASETS = ("GPW", "US", "FX", "WIG20", "DERIVATIVES", "RATES")
DERIVATIVE_EMPTY_LOOKBACK_DAYS = 7


@dataclass(frozen=True)
class Instrument:
    symbol: str
    latest: date | None
    expiry: date | None = None


@dataclass(frozen=True)
class Job:
    dataset: str
    symbols: tuple[str, ...]
    start: date
    end: date
    argv: tuple[str, ...]


@dataclass
class Plan:
    jobs: list[Job]
    skipped: list[str]
    target: date


def yesterday_warsaw() -> date:
    return datetime.now(ZoneInfo("Europe/Warsaw")).date() - timedelta(days=1)


def as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def fetch_inventory(connection, dataset: str) -> list[Instrument]:
    """Retain registered instruments without any observations."""
    if dataset in {"GPW", "US"}:
        sql = '''SELECT s."SYMBOL", MAX(TO_DATE(p."DATE"))
                 FROM "SECURITIES" s LEFT JOIN "PRICES" p ON p."SYMBOL" = s."SYMBOL"
                 WHERE s."VENUE" = %s
                 GROUP BY s."SYMBOL" ORDER BY s."SYMBOL"'''
        params = (dataset,)
    elif dataset == "FX":
        sql = '''SELECT c."SYMBOL", MAX(TO_DATE(r."DATE"))
                 FROM "CURRENCY_PAIRS" c LEFT JOIN "FX_RATES" r
                    ON r."CURRENCY_PAIR_ID" = c."CURRENCY_PAIR_ID"
                 WHERE c."IS_ACTIVE" = TRUE
                 GROUP BY c."SYMBOL" ORDER BY c."SYMBOL"'''
        params = ()
    elif dataset == "WIG20":
        sql = """SELECT 'WIG20', MAX(TO_DATE("DATE")) FROM "INDEX_PRICES"
                 WHERE "INDEX_SYMBOL" = 'WIG20' """
        params = ()
    elif dataset == "RATES":
        sql = '''SELECT s."SERIES_ID", MAX(o."DATE")
                 FROM "RATE_SERIES" s LEFT JOIN "RATE_OBSERVATIONS" o
                 ON s."SERIES_ID"=o."SERIES_ID" WHERE s."IS_ACTIVE"=TRUE
                 GROUP BY s."SERIES_ID" ORDER BY s."SERIES_ID"'''
        params = ()
    elif dataset in {"FW20", "DERIVATIVES"}:
        predicate = (
            """c."INSTRUMENT_TYPE" = 'FUTURE' AND c."UNDERLYING_SYMBOL" = 'WIG20' """
            if dataset == "FW20"
            else """c."INSTRUMENT_TYPE" IN ('FUTURE','OPTION') """
        )
        sql = (
            """SELECT c."CONTRACT_ID", MAX(TO_DATE(p."DATE")), c."EXPIRY_DATE"
               FROM "DERIVATIVE_CONTRACTS" c LEFT JOIN "DERIVATIVE_PRICES" p
               ON p."CONTRACT_ID" = c."CONTRACT_ID" WHERE """
            + predicate
            + '''GROUP BY c."CONTRACT_ID", c."EXPIRY_DATE" ORDER BY c."CONTRACT_ID"'''
        )
        params = ()
    else:
        raise ValueError(f"Unsupported dataset: {dataset}")
    with closing(connection.cursor()) as cur:
        cur.execute(sql, params) if params else cur.execute(sql)
        rows = cur.fetchall()
    return [
        Instrument(str(r[0]), as_date(r[1]), as_date(r[2]) if len(r) > 2 else None)
        for r in rows
        if r[0]
    ]


def latest_start(
    latest: date | None, target: date, *, overlap: int, bootstrap: date | None
) -> date | None:
    if latest is None:
        return bootstrap
    if latest > target:
        return None
    return latest + timedelta(days=1 - overlap)


def worker(module: str, *args: str) -> tuple[str, ...]:
    return (sys.executable, "-m", module, *args)


def _chunks(values: list[str], size: int):
    for offset in range(0, len(values), size):
        yield tuple(values[offset : offset + size])


def build_plan(
    inventories: dict[str, list[Instrument]],
    *,
    target: date,
    overlap_days: int = 2,
    bootstrap_start: date | None = None,
    max_gap_days: int = 90,
    batch_size: int = 30,
    datasets: tuple[str, ...] = DATASETS,
) -> Plan:
    if overlap_days < 0 or max_gap_days < 2 or batch_size < 1:
        raise ValueError("Invalid overlap, max gap, or batch size")
    if bootstrap_start is not None and bootstrap_start > target:
        raise ValueError("Bootstrap start must not be after target")
    for dataset in datasets:
        if dataset not in DATASETS:
            raise ValueError(f"Unsupported dataset: {dataset}")
    jobs: list[Job] = []
    skipped: list[str] = []
    groups: dict[tuple[str, date], list[str]] = defaultdict(list)
    derivative_groups: dict[tuple[date, date], list[str]] = defaultdict(list)
    rate_groups: dict[date, list[str]] = defaultdict(list)
    # When both were selected, avoid scheduling WIG20 futures twice.
    supersede_fw20 = "DERIVATIVES" in datasets and "DERIVATIVES" in inventories
    for dataset in datasets:
        if dataset == "FW20" and supersede_fw20:
            continue
        for item in inventories.get(dataset, []):
            symbol = item.symbol
            if dataset in {"FW20", "DERIVATIVES"} and item.expiry is None:
                skipped.append(f"{dataset} {symbol}: missing verified expiry date")
                continue
            if dataset in {"FW20", "DERIVATIVES"} and item.expiry < target:
                skipped.append(f"{dataset} {symbol}: expired {item.expiry}")
                continue
            if dataset == "FX":
                if symbol in YAHOO_FX:
                    source = "FX-YAHOO"
                elif symbol.endswith("PLN=X") and symbol[:3].isalpha() and len(symbol) == 8:
                    source = "FX-NBP"
                else:
                    skipped.append(f"FX {symbol}: no configured direct feed")
                    continue
            else:
                source = dataset
            if item.latest is None:
                if dataset == "RATES":
                    start = bootstrap_start or target - timedelta(days=30)
                elif dataset == "DERIVATIVES":
                    start = bootstrap_start or target - timedelta(
                        days=DERIVATIVE_EMPTY_LOOKBACK_DAYS - 1
                    )
                else:
                    start = bootstrap_start
                if start is None:
                    skipped.append(f"{dataset} {symbol}: no stored history; use --bootstrap-start")
                    continue
            else:
                if item.latest > target:
                    skipped.append(f"{dataset} {symbol}: latest {item.latest} is after target")
                    continue
                start = latest_start(
                    item.latest, target, overlap=overlap_days, bootstrap=bootstrap_start
                )
            if start is None or start > target:
                continue
            duration = (target - start).days + 1
            if source == "RATES":
                if duration > max_gap_days:
                    skipped.append(f"RATES {symbol}: {duration} days exceeds max gap")
                    continue
                rate_groups[start].append(symbol)
            elif source == "DERIVATIVES":
                if duration > max_gap_days:
                    skipped.append(f"DERIVATIVES {symbol}: {duration} days exceeds max gap")
                    continue
                derivative_groups[(start, target)].append(symbol)
            elif source in {"GPW", "US", "FX-YAHOO", "FX-NBP"}:
                if duration > max_gap_days:
                    skipped.append(
                        f"{dataset} {symbol}: {start}..{target} exceeds --max-gap-days={max_gap_days}; run explicit backfill"
                    )
                    continue
                groups[(source, start)].append(symbol)
            elif source == "WIG20":
                days = max(2, duration)
                if days > max_gap_days:
                    skipped.append("WIG20: too stale for short Yahoo refresh; use CSV backfill")
                    continue
                jobs.append(
                    Job(
                        dataset,
                        (symbol,),
                        start,
                        target,
                        worker("run.refresh_index_prices", "--days", str(days)),
                    )
                )
            elif source == "FW20":
                days = max(2, duration)
                if days > 12:
                    skipped.append(
                        f"FW20 {symbol}: long gap {start}..{target}; use full-page backfill"
                    )
                    continue
                groups[(source, start)].append(symbol)
    for (source, start), symbols in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1])):
        for chunk in _chunks(sorted(set(symbols)), batch_size):
            if source in {"GPW", "US"}:
                argv = worker(
                    "run.backfill_equities",
                    "--venue",
                    source,
                    "--symbols",
                    *chunk,
                    "--start",
                    start.isoformat(),
                    "--end",
                    target.isoformat(),
                    "--iic-force",
                    "--continue-on-error",
                )
            elif source == "FX-YAHOO":
                argv = worker(
                    "run.backfill_fx_snowflake",
                    "--symbols",
                    *chunk,
                    "--start",
                    start.isoformat(),
                    "--end",
                    target.isoformat(),
                    "--iic-force",
                    "--continue-on-error",
                )
            elif source == "FX-NBP":
                argv = worker(
                    "run.backfill_fx_nbp",
                    "--symbols",
                    *chunk,
                    "--start",
                    start.isoformat(),
                    "--end",
                    target.isoformat(),
                    "--continue-on-error",
                )
            else:
                days = max(2, (target - start).days + 1)
                argv = worker(
                    "run.refresh_fw20_prices",
                    "--symbols",
                    *chunk,
                    "--through",
                    target.isoformat(),
                    "--days",
                    str(days),
                )
            jobs.append(Job(source, chunk, start, target, argv))
    for (start, end), symbols in sorted(derivative_groups.items()):
        for batch_no, chunk in enumerate(_chunks(sorted(set(symbols)), batch_size), 1):
            report = (
                f"output/derivative_update_{start:%Y%m%d}_{end:%Y%m%d}_{batch_no}_{chunk[0]}.csv"
            )
            argv = worker(
                "run.backfill_derivative_prices",
                "--start",
                start.isoformat(),
                "--end",
                end.isoformat(),
                "--symbols",
                *chunk,
                "--report",
                report,
            )
            jobs.append(Job("DERIVATIVES", chunk, start, end, argv))
    for start, symbols in sorted(rate_groups.items()):
        for chunk in _chunks(sorted(set(symbols)), batch_size):
            jobs.append(
                Job(
                    "RATES",
                    chunk,
                    start,
                    target,
                    worker(
                        "run.backfill_rates",
                        "--start",
                        start.isoformat(),
                        "--end",
                        target.isoformat(),
                        "--series",
                        *chunk,
                    ),
                )
            )
    priority = {
        "GPW": 0,
        "US": 1,
        "FX-YAHOO": 2,
        "FX-NBP": 3,
        "WIG20": 4,
        "FW20": 5,
        "DERIVATIVES": 5,
        "RATES": 6,
    }
    jobs.sort(key=lambda j: (priority[j.dataset], j.start, j.symbols))
    return Plan(jobs, skipped, target)
