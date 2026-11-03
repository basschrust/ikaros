"""Plan incremental Snowflake market-data refreshes without rewriting existing data.

One database read per dataset, grouped worker commands, no Kafka dependency.
This is a *latest-date catch-up*, not a scanner for interior historical gaps.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
import sys
from zoneinfo import ZoneInfo

LOG = logging.getLogger(__name__)

# Source-backed yfinance daily FX pairs that have been tested in Ikaros.
# Deliberately exclude synthetically derived currency crosses.
YAHOO_FX = frozenset(
    "AUDPLN=X AUDUSD=X CHFPLN=X DKKPLN=X EURAUD=X EURCAD=X EURCHF=X "
    "EURCNY=X EURDKK=X EURGBP=X EURJPY=X EURNOK=X EURPLN=X EURSEK=X "
    "EURSGD=X EURUSD=X GBPPLN=X GBPUSD=X NZDUSD=X USDCAD=X USDCHF=X "
    "USDCNY=X USDDKK=X USDJPY=X USDNOK=X USDPLN=X USDSEK=X USDSGD=X".split()
)
DATASETS = ("GPW", "FX", "WIG20", "FW20")


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
    """Query registered instruments, retaining those without any price records."""
    if dataset == "GPW":
        sql = '''SELECT s."SYMBOL", MAX(TO_DATE(p."DATE"))
                 FROM "SECURITIES" s LEFT JOIN "PRICES" p
                   ON p."SYMBOL" = s."SYMBOL"
                 WHERE s."VENUE" = 'GPW'
                 GROUP BY s."SYMBOL" ORDER BY s."SYMBOL"'''
    elif dataset == "FX":
        sql = '''SELECT c."SYMBOL", MAX(TO_DATE(r."DATE"))
                 FROM "CURRENCY_PAIRS" c LEFT JOIN "FX_RATES" r
                   ON r."CURRENCY_PAIR_ID" = c."CURRENCY_PAIR_ID"
                 WHERE c."IS_ACTIVE" = TRUE
                 GROUP BY c."SYMBOL" ORDER BY c."SYMBOL"'''
    elif dataset == "WIG20":
        sql = '''SELECT 'WIG20', MAX(TO_DATE("DATE")) FROM "INDEX_PRICES"
                 WHERE "INDEX_SYMBOL" = 'WIG20' '''
    elif dataset == "FW20":
        sql = '''SELECT c."CONTRACT_ID", MAX(TO_DATE(p."DATE")), c."EXPIRY_DATE"
                 FROM "DERIVATIVE_CONTRACTS" c
                 LEFT JOIN "DERIVATIVE_PRICES" p ON p."CONTRACT_ID" = c."CONTRACT_ID"
                 WHERE c."INSTRUMENT_TYPE" = 'FUTURE'
                   AND c."UNDERLYING_SYMBOL" = 'WIG20'
                 GROUP BY c."CONTRACT_ID", c."EXPIRY_DATE"
                 ORDER BY c."CONTRACT_ID"'''
    else:
        raise ValueError(f"Unsupported dataset: {dataset}")
    with closing(connection.cursor()) as cur:
        cur.execute(sql)
        result = cur.fetchall()
    return [
        Instrument(str(row[0]), as_date(row[1]), as_date(row[2]) if len(row) > 2 else None)
        for row in result if row[0]
    ]


def latest_start(
    latest: date | None, target: date, *, overlap: int, bootstrap: date | None,
) -> date | None:
    if latest is None:
        return bootstrap
    if latest > target:
        return None
    # Include a short overlap to recheck missed tail sessions. Existing quotes
    # are not replaced by insert-only backfill workers.
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
    jobs: list[Job] = []
    skipped: list[str] = []
    groups: dict[tuple[str, date], list[str]] = defaultdict(list)
    for dataset in datasets:
        if dataset not in DATASETS:
            raise ValueError(f"Unsupported dataset: {dataset}")
        for item in inventories.get(dataset, []):
            symbol = item.symbol
            if dataset == "FW20" and item.expiry is not None and item.expiry < target:
                skipped.append(f"{dataset} {symbol}: expired {item.expiry}")
                continue
            if dataset == "FX":
                if symbol in YAHOO_FX:
                    source = "FX-YAHOO"
                elif symbol.endswith("PLN=X") and symbol[:3].isalpha() and len(symbol) == 8:
                    source = "FX-NBP"
                else:
                    # No triangulation, and do not assume a supported market feed.
                    skipped.append(f"FX {symbol}: no configured direct feed")
                    continue
            else:
                source = dataset
            if item.latest is None and bootstrap_start is None:
                skipped.append(f"{dataset} {symbol}: no stored history; use --bootstrap-start")
                continue
            if item.latest is not None and item.latest > target:
                skipped.append(f"{dataset} {symbol}: latest {item.latest} is after target")
                continue
            start = latest_start(item.latest, target, overlap=overlap_days, bootstrap=bootstrap_start)
            if start is None:
                continue
            if source in {"GPW", "FX-YAHOO", "FX-NBP"}:
                # Prevent a stale/delisted series from causing an unexpected
                # multi-year update in a routine daily refresh.
                if (target - start).days + 1 > max_gap_days:
                    skipped.append(
                        f"{dataset} {symbol}: {start}..{target} exceeds "
                        f"--max-gap-days={max_gap_days}; run explicit backfill"
                    )
                    continue
                groups[(source, start)].append(symbol)
            elif source == "WIG20":
                # Yahoo hourly API is a recent-history source; a full historical
                # WIG20 repair belongs to the CSV backfill provider.
                days = max(2, (target - start).days + 1)
                if days > max_gap_days:
                    skipped.append("WIG20: too stale for short Yahoo refresh; use CSV backfill")
                    continue
                jobs.append(Job(dataset, (symbol,), start, target,
                                worker("run.refresh_index_prices", "--days", str(days))))
            elif source == "FW20":
                # Recent endpoint only scrapes the newest BiznesRadar page.
                # It must not claim it can repair a long historical gap.
                days = max(2, (target - start).days + 1)
                if days > 12:
                    skipped.append(f"FW20 {symbol}: long gap {start}..{target}; use full-page backfill")
                    continue
                groups[(source, start)].append(symbol)

    for (source, start), symbols in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1])):
        for chunk in _chunks(sorted(set(symbols)), batch_size):
            if source == "GPW":
                argv = worker("run.backfill_gpw_snowflake", "--symbols", *chunk,
                              "--start", start.isoformat(), "--end", target.isoformat(),
                              "--iic-force", "--continue-on-error")
                label = "GPW"
            elif source == "FX-YAHOO":
                # Provider end is commonly exclusive; ask for the next day but
                # only last completed day is meaningful. Backfill normalizer
                # bounds dates to --end, so this cannot invent today's candle.
                argv = worker("run.backfill_fx_snowflake", "--symbols", *chunk,
                              "--start", start.isoformat(), "--end", target.isoformat(),
                              "--iic-force", "--continue-on-error")
                label = "FX-YAHOO"
            elif source == "FX-NBP":
                argv = worker("run.backfill_fx_nbp", "--symbols", *chunk,
                              "--start", start.isoformat(), "--end", target.isoformat(),
                              "--continue-on-error")
                label = "FX-NBP"
            else:
                days = max(2, (target - start).days + 1)
                argv = worker("run.refresh_fw20_prices", "--symbols", *chunk,
                              "--through", target.isoformat(), "--days", str(days))
                label = "FW20"
            jobs.append(Job(label, chunk, start, target, argv))
    # Ordering: GPW and FX before index/futures is not required technically,
    # but makes operator logs easier to compare.
    jobs.sort(key=lambda j: ({"GPW": 0, "FX-YAHOO": 1, "FX-NBP": 2, "WIG20": 3, "FW20": 4}[j.dataset], j.start, j.symbols))
    return Plan(jobs, skipped, target)
