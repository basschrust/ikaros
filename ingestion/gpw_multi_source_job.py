"""Orchestrate three GPW sources while preserving candidate evidence.

Date bounds are inclusive and always refer to *completed* GPW sessions.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping as MappingType, Sequence

from database.gpw_price_pipeline import (
    Candidate,
    Mapping,
    choose,
    commit_day,
    load_mappings,
    make_candidate,
)
from ingestion.gpw_provider_feeds import GPWPriceFeed

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunResult:
    symbols_processed: int
    provider_requests: int
    provider_errors: tuple[str, ...]
    days_seen: int
    candidates_inserted: int
    promoted: int
    unresolved: int
    preserved_conflicts: int


def _active_mapping(mappings: Sequence[Mapping], code: str, day: date) -> Mapping | None:
    found = [
        m
        for m in mappings
        if m.provider_code == code
        and m.valid_from <= day
        and (m.valid_to is None or day <= m.valid_to)
    ]
    if len(found) > 1:
        raise ValueError(f"Ambiguous provider symbol for {code} at {day}")
    return found[0] if found else None


def run_job(
    connection: Any,
    *,
    feeds: MappingType[str, GPWPriceFeed],
    symbols: Sequence[str] | None,
    start: date,
    end: date,
    write: bool = False,
    request_delay: float = 0.4,
    minimum_sources: int = 1,
    allow_one_volume_source: bool = False,
) -> RunResult:
    if start > end:
        raise ValueError("start must not be after end")
    if minimum_sources < 1 or minimum_sources > 3:
        raise ValueError("minimum_sources must be 1, 2 or 3")
    if request_delay < 0:
        raise ValueError("request_delay must be nonnegative")
    catalog = load_mappings(connection, symbols=symbols)
    failures: list[str] = []
    symbols_processed = requests = days = inserted = promoted = unresolved = conflicts = 0
    for asset_id, mappings in sorted(catalog.items()):
        canonical = mappings[0].canonical_symbol
        configured = {m.provider_code for m in mappings}
        if len(configured) < minimum_sources:
            failures.append(
                f"{canonical}: requires {minimum_sources} active source mappings, "
                f"has {sorted(configured)}"
            )
            continue
        symbols_processed += 1
        day_candidates: dict[date, list[Candidate]] = defaultdict(list)
        for code in ("YFINANCE", "STOOQ", "BIZNESRADAR"):
            feed = feeds.get(code)
            if feed is None:
                failures.append(f"{canonical} {code}: adapter not installed")
                continue
            # Historical symbol changes might require multiple provider requests.
            for mapping in (m for m in mappings if m.provider_code == code):
                low, high = max(start, mapping.valid_from), min(end, mapping.valid_to or end)
                if low > high:
                    continue
                try:
                    requests += 1
                    candles = feed.history(mapping.provider_symbol, low, high)
                    for candle in candles:
                        if not low <= candle.day <= high:
                            raise ValueError(f"out-of-window session {candle.day}")
                        resolved = _active_mapping(mappings, code, candle.day)
                        if resolved != mapping:
                            raise ValueError("Provider returned ambiguous mapping")
                        day_candidates[candle.day].append(make_candidate(mapping, candle))
                except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                    failures.append(f"{canonical} {code}: {type(exc).__name__}: {exc}")
                    LOG.warning("PROVIDER ERROR %s %s: %s", canonical, code, exc)
                if request_delay:
                    time.sleep(request_delay)
        if not day_candidates:
            failures.append(f"{canonical}: no OHLCV observations from any source in {start}..{end}")
        for day, candidates in sorted(day_candidates.items()):
            # One candidate per source and trading session; never arbitrate silently.
            codes = [item.mapping.provider_code for item in candidates]
            if len(codes) != len(set(codes)):
                failures.append(f"{canonical} {day}: duplicate source/day")
                unresolved += 1
                continue
            days += 1
            selection = choose(
                candidates,
                minimum_confirmations=minimum_sources,
                allow_one_volume_source=allow_one_volume_source,
            )
            n, state = commit_day(
                connection, candidates, selection, canonical_symbol=canonical, write=write
            )
            inserted += n
            if state in {"INSERTED", "REPAIRED_SUSPECT", "MATCHED_EXISTING", "UNCHANGED"}:
                promoted += 1
            elif state == "PRESERVED_EXISTING_CONFLICT":
                conflicts += 1
            elif (
                state == "UNRESOLVED"
                or state.startswith("DRY_RUN_")
                and selection.candidate is None
            ):
                unresolved += 1
            chosen = selection.candidate
            if chosen is None:
                for candidate in candidates:
                    c = candidate.candle
                    LOG.warning(
                        "%s %s CANDIDATE %s status=%s reason=%s OHLCV=(%s, %s, %s, %s, %s)",
                        canonical,
                        day,
                        candidate.mapping.provider_code,
                        candidate.status,
                        candidate.reason,
                        c.open,
                        c.high,
                        c.low,
                        c.close,
                        c.volume,
                    )
            elif selection.reason == "PRIORITY_SELECTED_DISAGREEMENT":
                LOG.warning(
                    "%s %s: conflicting VALID source candles; selected %s "
                    "by configured provider priority, other candidates retained",
                    canonical,
                    day,
                    chosen.mapping.provider_code,
                )
            elif selection.reason == "SINGLE_VALID_SOURCE":
                LOG.info(
                    "%s %s: only %s passed full OHLCV validation; "
                    "other observations are retained as candidates",
                    canonical,
                    day,
                    chosen.mapping.provider_code,
                )
            elif selection.reason == "PRICE_CONFIRMED_VOLUME_SINGLE_SOURCE":
                LOG.warning(
                    "%s %s: OHLC confirmed by %s; VOLUME provided only by %s "
                    "(explicit opt-in; no volume cross-check)",
                    canonical,
                    day,
                    selection.confirmed_by,
                    chosen.mapping.provider_code,
                )
            LOG.info(
                "%s %s: %s sources=%s status=%s candidate_rows=%s selected=%s OHLCV=%s",
                canonical,
                day,
                selection.reason,
                selection.confirmed_by,
                state,
                n,
                chosen.mapping.provider_code if chosen else "NONE",
                (
                    chosen.candle.open,
                    chosen.candle.high,
                    chosen.candle.low,
                    chosen.candle.close,
                    chosen.candle.volume,
                )
                if chosen
                else None,
            )
    return RunResult(
        symbols_processed,
        requests,
        tuple(failures),
        days,
        inserted,
        promoted,
        unresolved,
        conflicts,
    )
