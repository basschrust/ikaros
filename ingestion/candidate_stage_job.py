"""Fetch external observations and stage evidence, never final prices.

SQL is intentionally delegated to database.*; promotion is a separate process.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Sequence

from database.candidate_catalog import active_foreign_mappings, fx_pairs
from database.candidate_stage_queries import store_equity_candidates, store_market_candidate
from database.candidate_observations import Observation, register_rate_series, stage
from database.gpw_price_pipeline import (
    load_mappings as equity_mappings,
    make_candidate as equity_candidate,
)
from database.market_quote_pipeline import (
    load_mappings as market_mappings,
    make_candidate as market_candidate,
)
from ingestion.gpw_provider_feeds import default_feeds as equity_feeds, _one
from ingestion.market_quote_feeds import default_feeds as market_feeds

LOG = logging.getLogger(__name__)
FOREIGN = frozenset({"US", "PSE", "XETRA", "PARIS", "LSE", "BSE"})


@dataclass(frozen=True)
class StageReport:
    instruments: int
    observations: int
    newly_stored: int
    errors: tuple[str, ...]


def _stage_equities(
    connection: Any,
    *,
    venue: str,
    symbols: Sequence[str] | None,
    start: date,
    end: date,
    backend: str,
    write: bool,
) -> StageReport:
    if venue == "GPW":
        catalog = equity_mappings(connection, symbols=symbols)
        mapping_sets = list(catalog.values())
    else:
        mapping_sets = [
            [m] for m in active_foreign_mappings(connection, venue=venue, symbols=symbols)
        ]
    feeders = equity_feeds() if venue == "GPW" else {}
    seen = new = 0
    errors = []
    for mappings in mapping_sets:
        for mapping in mappings:
            low = max(start, mapping.valid_from)
            high = min(end, mapping.valid_to or end)
            if low > high:
                continue
            try:
                if venue == "GPW":
                    adapter = feeders[mapping.provider_code]
                    candles = adapter.history(mapping.provider_symbol, low, high)
                else:
                    from ingestion.prices import fetch_eod_prices
                    from run.backfill_equities import normalize_equity_quote_units

                    frame = fetch_eod_prices(
                        symbol=mapping.provider_symbol,
                        start_date=low.isoformat(),
                        end_date=(high + timedelta(days=1)).isoformat(),
                    )
                    frame = normalize_equity_quote_units(frame, venue, mapping.canonical_symbol)
                    candles = [
                        _one(r.date, r.open, r.high, r.low, r.close, r.volume)
                        for r in frame.itertuples(index=False)
                    ]
                candidates = [
                    equity_candidate(mapping, c, positive_volume=True)
                    for c in candles
                    if low <= c.day <= high
                ]
            except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
                errors.append(f"{mapping.canonical_symbol} {mapping.provider_code}: {exc}")
                LOG.warning(
                    "SOURCE FAILURE %s %s: %s", mapping.canonical_symbol, mapping.provider_code, exc
                )
                continue
            seen += len(candidates)
            # Database failures must propagate; otherwise a partial ingest
            # could be incorrectly reported as a successful source window.
            if write and candidates:
                new += store_equity_candidates(connection, candidates, backend=backend)
    return StageReport(len(mapping_sets), seen, new, tuple(errors))


def _stage_market(
    connection: Any,
    *,
    dataset: str,
    symbols: Sequence[str] | None,
    start: date,
    end: date,
    backend: str,
    write: bool,
) -> StageReport:
    kind = "INDEX" if dataset == "WIG20" else "DERIVATIVE"
    catalog = market_mappings(connection, kind, symbols=symbols, backend=backend)
    sources = market_feeds(kind)
    seen = new = 0
    errors = []
    for symbol, mappings in sorted(catalog.items()):
        for mapping in mappings:
            low = max(start, mapping.valid_from)
            high = min(end, mapping.valid_to or end)
            if low > high:
                continue
            if mapping.provider_code not in sources:
                errors.append(f"{symbol} {mapping.provider_code}: adapter unavailable")
                continue
            try:
                candles = sources[mapping.provider_code].history(mapping.provider_symbol, low, high)
                if len({c.day for c in candles}) != len(candles):
                    raise ValueError("Duplicate source/session candles")
                if any(not low <= candle.day <= high for candle in candles):
                    raise ValueError("Out-of-window source session")
                candidates = [market_candidate(kind, mapping, candle) for candle in candles]
            except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
                errors.append(f"{symbol} {mapping.provider_code}: {exc}")
                LOG.warning("SOURCE FAILURE %s %s: %s", symbol, mapping.provider_code, exc)
                continue
            seen += len(candidates)
            for item in candidates:
                if write:
                    new += int(
                        store_market_candidate(
                            connection, kind=kind, candidate=item, backend=backend
                        )
                    )
    return StageReport(len(catalog), seen, new, tuple(errors))


def _stage_fx(
    connection: Any,
    *,
    symbols: Sequence[str] | None,
    start: date,
    end: date,
    backend: str,
    write: bool,
) -> StageReport:
    from ikaros_app.update import YAHOO_FX
    from run.ingest_fx_rates import fetch_fx_history

    pairs = fx_pairs(connection, symbols=symbols)
    seen = new = 0
    errors = []
    # Single provider for each pair is sufficient. NBP reference mids are not
    # comparable to executable market OHLC; preserve quote-kind provenance.
    nbp_symbols = [s for s in pairs if s not in YAHOO_FX]
    for symbol in (s for s in pairs if s in YAHOO_FX):
        try:
            df = fetch_fx_history(
                symbol, start_date=start.isoformat(), end_date=(end + timedelta(days=1)).isoformat()
            )
            observations = []
            for rec in df.itertuples(index=False):
                if not start <= rec.date <= end:
                    continue

                def val(name: str) -> Decimal | None:
                    import pandas as pd

                    raw = getattr(rec, name, None)
                    return (
                        Decimal(str(raw)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
                        if raw is not None and not pd.isna(raw)
                        else None
                    )

                volume = val("volume")
                volume_int = (
                    int(volume)
                    if volume is not None and volume == volume.to_integral_value()
                    else None
                )
                observations.append(
                    Observation(
                        "FX",
                        symbol,
                        rec.date,
                        "YFINANCE",
                        ref_id=pairs[symbol],
                        open=val("open"),
                        high=val("high"),
                        low=val("low"),
                        close=val("close"),
                        volume=volume_int,
                    )
                )
        except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
            errors.append(f"{symbol} YFINANCE: {exc}")
            continue
        n, k = stage(connection, observations, backend=backend, write=write)
        seen += n
        new += k
    if nbp_symbols:
        import requests
        from ingestion.nbp_fx import cross_rows, windows
        from run.backfill_fx_nbp import fetch_tables

        with requests.Session() as session:
            for begin, stop in windows(start, end):
                try:
                    daily = fetch_tables(session, begin, stop)
                    rows = cross_rows(daily, nbp_symbols)
                    observations = [
                        Observation(
                            "FX",
                            symbol,
                            day,
                            "NBP",
                            ref_id=pairs[symbol],
                            quote_kind="REFERENCE",
                            close=Decimal(str(close)).quantize(
                                Decimal("0.00000001"), rounding=ROUND_HALF_UP
                            ),
                        )
                        for symbol, day, close, _comment in rows
                    ]
                except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
                    errors.append(f"NBP {begin}..{stop}: {exc}")
                    continue
                n, k = stage(connection, observations, backend=backend, write=write)
                seen += n
                new += k
    return StageReport(len(pairs), seen, new, tuple(errors))


def _rate_providers() -> list[Any]:
    from run import backfill_rates as r

    return [
        r.TreasuryYieldProvider(),
        r.ECBSpotYieldProvider(),
        r.NYFedOvernightProvider(),
        r.BoERatesProvider(),
        r.ECBESTRProvider(),
        r.SNBPolicyProvider(),
        r.NBPPolicyProvider(),
        r.CNBPolicyProvider(),
        r.MNBPolicyProvider(),
        r.BoEGiltYieldProvider(),
        r.EurostatGovYieldProvider(),
        r.SNBGovernmentYieldProvider(),
        r.TreasuryRealYieldProvider(),
        r.TreasuryBillYieldProvider(),
    ]


def _stage_rates(
    connection: Any,
    *,
    symbols: Sequence[str] | None,
    start: date,
    end: date,
    backend: str,
    write: bool,
) -> StageReport:
    import requests
    from run.backfill_rates import select_series

    series, sources = select_series(list(symbols) if symbols is not None else None)
    register_rate_series(connection, series.values(), backend=backend, write=write)
    seen = new = 0
    errors = []
    with requests.Session() as session:
        session.headers.update({"User-Agent": "IkarosMarketData/1.0"})
        for provider in _rate_providers():
            if provider.source not in sources:
                continue
            try:
                candidates = []
                for r in provider.history(session, start, end):
                    if r.series_id not in series or not start <= r.day <= end:
                        continue
                    raw = Decimal(str(r.rate)).quantize(
                        Decimal("0.0000000001"), rounding=ROUND_HALF_UP
                    )
                    bid = getattr(r, "bid", None)
                    ask = getattr(r, "ask", None)
                    candidates.append(
                        Observation(
                            "RATE",
                            r.series_id,
                            r.day,
                            series[r.series_id].source,
                            quote_kind="RATE",
                            rate=raw,
                            bid=Decimal(str(bid)).quantize(Decimal("0.0000000001"))
                            if bid is not None
                            else None,
                            ask=Decimal(str(ask)).quantize(Decimal("0.0000000001"))
                            if ask is not None
                            else None,
                        )
                    )
            except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
                errors.append(f"{provider.source}: {exc}")
                continue
            n, k = stage(connection, candidates, backend=backend, write=write)
            seen += n
            new += k
    return StageReport(len(series), seen, new, tuple(errors))


def run_stage(
    connection: Any,
    *,
    dataset: str,
    symbols: Sequence[str] | None,
    start: date,
    end: date,
    backend: str,
    write: bool,
) -> StageReport:
    """No caller can promote as a side effect of staging."""
    if start > end:
        raise ValueError("Invalid inclusive date range")
    if dataset in {"GPW", *FOREIGN}:
        return _stage_equities(
            connection,
            venue=dataset,
            symbols=symbols,
            start=start,
            end=end,
            backend=backend,
            write=write,
        )
    if dataset in ("WIG20", "DERIVATIVES", "FW20"):
        return _stage_market(
            connection,
            dataset=dataset,
            symbols=symbols,
            start=start,
            end=end,
            backend=backend,
            write=write,
        )
    if dataset == "FX":
        return _stage_fx(
            connection, symbols=symbols, start=start, end=end, backend=backend, write=write
        )
    if dataset == "RATES":
        return _stage_rates(
            connection, symbols=symbols, start=start, end=end, backend=backend, write=write
        )
    raise ValueError(f"Unsupported candidate source dataset: {dataset}")
