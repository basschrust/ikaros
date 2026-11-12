"""Query and plot published yield observations; never bootstrap or interpolate curves.

Each rendered curve is one source/curve-type/compounding/frequency group.
Rates are stored as decimals and displayed as annual percentages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re

from matplotlib.ticker import FuncFormatter, FixedLocator

TENOR_RE = re.compile(r"^(\d+)([DWMY])$")
TENOR_YEARS = {"D": 1 / 365.25, "W": 7 / 365.25, "M": 1 / 12, "Y": 1.0}


@dataclass(frozen=True)
class YieldPoint:
    series_id: str
    currency: str
    source: str
    curve_type: str
    tenor: str
    years: float
    compounding: str
    observation_date: date
    rate: float


def years_from_tenor(tenor: str) -> float | None:
    match = TENOR_RE.fullmatch(tenor.strip().upper())
    if not match:
        return None
    amount = int(match.group(1))
    if amount < 1:
        return None
    return amount * TENOR_YEARS[match.group(2)]


def fetch_points(connection, as_of: date, currencies: list[str] | None = None) -> list[YieldPoint]:
    """Latest observation per series on or before as_of. No look-ahead."""
    sql = """SELECT s."SERIES_ID", s."CURRENCY", s."SOURCE", s."CURVE_TYPE",
                    s."TENOR", s."COMPOUNDING", o."DATE", o."RATE"
             FROM "RATE_SERIES" s
             JOIN "RATE_OBSERVATIONS" o ON o."SERIES_ID" = s."SERIES_ID"
             WHERE s."IS_ACTIVE" = TRUE AND o."DATE" <= %s
               AND s."CURVE_TYPE" LIKE 'GOV%%'
               AND s."CURRENCY" IN ("""
    requested = sorted(
        set(c.upper() for c in (currencies or ["CHF", "CZK", "EUR", "GBP", "HUF", "PLN", "USD"]))
    )
    if not requested or any(not re.fullmatch(r"[A-Z]{3}", c) for c in requested):
        raise ValueError("Invalid currencies")
    sql += (
        ",".join(["%s"] * len(requested))
        + """)
             QUALIFY ROW_NUMBER() OVER (
               PARTITION BY s."SERIES_ID" ORDER BY o."DATE" DESC
             ) = 1"""
    )
    with connection.cursor() as cur:
        cur.execute(sql, (as_of, *requested))
        rows = cur.fetchall()
    points = []
    for sid, currency, source, kind, tenor, convention, day, rate in rows:
        years = years_from_tenor(str(tenor))
        if years is None:
            continue
        observation_date = day.date() if hasattr(day, "date") and not isinstance(day, date) else day
        if observation_date > as_of:
            raise ValueError(f"Future observation for {sid}")
        points.append(
            YieldPoint(
                str(sid),
                str(currency),
                str(source),
                str(kind),
                str(tenor),
                years,
                str(convention),
                observation_date,
                float(rate),
            )
        )
    return points


def grouped_points(
    points: list[YieldPoint], as_of: date, max_age_days: int = 7, monthly_max_age_days: int = 75
) -> tuple[dict[tuple[str, str, str, str], list[YieldPoint]], list[str]]:
    """Group comparable published yields. Monthly groups use a separate age limit."""
    if max_age_days < 0 or monthly_max_age_days < 0:
        raise ValueError("Max age must be nonnegative")
    groups: dict[tuple[str, str, str, str], list[YieldPoint]] = {}
    warnings = []
    for point in points:
        monthly = "MONTHLY" in point.series_id or "MONTHLY" in point.curve_type
        limit = monthly_max_age_days if monthly else max_age_days
        age = (as_of - point.observation_date).days
        if age < 0 or age > limit:
            warnings.append(f"SKIP {point.series_id}: observation age {age}d exceeds {limit}d")
            continue
        key = (point.currency, point.source, point.curve_type, point.compounding)
        groups.setdefault(key, []).append(point)
    for key, values in groups.items():
        values.sort(key=lambda p: (p.years, p.series_id))
        if len({p.years for p in values}) != len(values):
            raise ValueError(f"Duplicate tenors in {key}")
    return dict(sorted(groups.items())), warnings


def plot_groups(groups: dict, as_of: date, output_dir: Path, *, show: bool = False) -> list[Path]:
    """One PNG per currency/curve family. No smoothing or extrapolation."""
    import matplotlib.pyplot as plt  # Optional dependency, loaded only for plotting.

    output_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for (currency, source, kind, convention), points in groups.items():
        if not points:
            continue
        fig, ax = plt.subplots(figsize=(9, 5.5))
        x = [p.years for p in points]
        y = [p.rate * 100 for p in points]
        # One tenor is an observation, not a curve.
        if len(points) == 1:
            ax.scatter(x, y, s=45)
        else:
            ax.plot(x, y, marker="o")
        for p in points:
            ax.annotate(
                p.tenor,
                (p.years, p.rate * 100),
                xytext=(0, 8),
                textcoords="offset points",
                ha="center",
                fontsize=8,
            )
        ax.axhline(0, linewidth=0.8, alpha=0.6)
        ax.set_title(f"{currency}: {source} / {kind} (as of {as_of.isoformat()})")
        ax.set_xlabel("Maturity (years)")
        ax.set_ylabel("Published yield (%)")
        ax.grid(alpha=0.25)
        dates = sorted({p.observation_date for p in points})
        date_desc = dates[0].isoformat() if len(dates) == 1 else f"{dates[0]} to {dates[-1]}"
        fig.text(
            0.02,
            0.01,
            f"Observations: {date_desc} | {convention} | no interpolation / no bootstrapping",
            fontsize=8,
        )
        ax.set_xscale("log")
        maturities = [0.25, 0.5, 1, 2, 3, 5, 10, 20, 30]
        ax.xaxis.set_major_locator(FixedLocator(maturities))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda x, pos: f"{x:g}Y"))
        ax.set_xlabel("Maturity (years, logarithmic scale)")
        fig.tight_layout(rect=(0, 0.04, 1, 1))

        def safe(text: str) -> str:
            return re.sub(r"[^A-Za-z0-9_-]", "_", text)

        path = (
            output_dir
            / f"yield_{safe(currency)}_{safe(source)}_{safe(kind)}_{safe(convention)}_{as_of:%Y%m%d}.png"
        )
        fig.savefig(path, dpi=160)
        saved.append(path)
        if show:
            plt.show()
        plt.close(fig)
    return saved
