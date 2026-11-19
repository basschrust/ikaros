"""Find *interior* missing GPW trading sessions in authoritative price histories.

Unlike latest-date catch-up, this scanner detects holes before the latest row.
Use only on exchange-traded index/equity series with a known GPW calendar.
A missing derivative contract quote can mean *no trade*, so derivative series
are intentionally excluded from automatic gap detection.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Sequence

from database.gap_queries import session_coverage

from ikaros_app.update import Job, worker
from run.ingest_gpw_prices import last_gpw_sessions


def _chunks(items: Sequence[str], size: int) -> list[tuple[str, ...]]:
    return [tuple(items[i : i + size]) for i in range(0, len(items), size)]


def missing_sessions(
    connection: Any,
    *,
    dataset: str,
    symbols: Sequence[str],
    through: date,
    lookback_sessions: int = 20,
    batch_size: int = 30,
    include_trailing: bool = False,
) -> dict[str, tuple[date, ...]]:
    """Detect historical interior gaps; never assume a stock traded before listing.

    The persisted MIN/MAX dates bound each series. Dates after the last stored
    candle belong to normal incremental catch-up, not interior-gap repair.
    Missing GPW stock days are candidates for review (suspensions are possible),
    never evidence that a zero-volume candle should be manufactured.
    """
    if dataset not in ("WIG20", "GPW"):
        raise ValueError(f"No GPW session calendar for {dataset}")
    if not 1 <= lookback_sessions <= 100 or batch_size < 1:
        raise ValueError("Invalid lookback_sessions or batch_size")
    ordered = sorted(set(symbols))
    if not ordered:
        return {}
    if dataset == "WIG20" and ordered != ["WIG20"]:
        raise ValueError("Only canonical WIG20 index is supported")
    sessions = last_gpw_sessions(through, lookback_sessions)
    first_session = sessions[0]
    gaps: dict[str, tuple[date, ...]] = {}
    # Single batched query per dataset chunk; query composition stays in database/.
    for batch in _chunks(ordered, batch_size):
        extent, recorded = session_coverage(
            connection,
            dataset=dataset,
            symbols=batch,
            start=first_session,
            end=through,
        )
        for symbol in batch:
            if symbol not in extent:
                # No history: explicit bootstrap, never infer a listing date.
                continue
            first, last = extent[symbol]
            missing = tuple(
                session
                for session in sessions
                if first <= session
                and (include_trailing or session < last)
                and session not in recorded.get(symbol, set())
            )
            if missing:
                gaps[symbol] = missing
    return gaps


def _windows(missing: Sequence[date], sessions: Sequence[date]) -> list[tuple[date, date]]:
    """Coalesce consecutive trading sessions into one fetch (weekends are okay)."""
    if not missing:
        return []
    position = {day: n for n, day in enumerate(sessions)}
    windows: list[tuple[date, date]] = []
    start = previous = missing[0]
    for day in missing[1:]:
        if position[day] != position[previous] + 1:
            windows.append((start, previous))
            start = day
        previous = day
    windows.append((start, previous))
    return windows


def gap_repair_jobs(
    connection: Any,
    *,
    dataset: str,
    symbols: Sequence[str],
    through: date,
    lookback_sessions: int = 20,
    batch_size: int = 30,
) -> tuple[list[Job], dict[str, tuple[date, ...]]]:
    """Plan exact missing windows, preserving normal planner's immutable Job shape."""
    gaps = missing_sessions(
        connection,
        dataset=dataset,
        symbols=symbols,
        through=through,
        lookback_sessions=lookback_sessions,
        batch_size=batch_size,
    )
    sessions = last_gpw_sessions(through, lookback_sessions)
    by_window: dict[tuple[date, date], list[str]] = defaultdict(list)
    for symbol, missing in gaps.items():
        for start, end in _windows(missing, sessions):
            by_window[start, end].append(symbol)
    jobs: list[Job] = []
    for (start, end), symbols_in_window in sorted(by_window.items()):
        for batch in _chunks(sorted(symbols_in_window), batch_size):
            jobs.append(
                Job(
                    dataset=dataset,
                    symbols=batch,
                    start=start,
                    end=end,
                    # Existing run.update_all.command_for_job handles the new
                    # candidate pipelines; no legacy worker may be executed.
                    argv=worker("run.update_all", "--repair-only"),
                )
            )
    return jobs, gaps
