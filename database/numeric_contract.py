"""Fixed-point contract for persisted Ikaros financial data.

This inventory is deliberately exhaustive for known financial columns, but the
runtime audit also finds ANY unexpected floating-point columns in the database.
An unknown float is an error, never a reason to guess its business semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

PRICE = "DECIMAL(24,8)"
FX = "DECIMAL(24,10)"
RATE = "DECIMAL(24,10)"
COUNT = "DECIMAL(38,0)"
WEIGHT = "DECIMAL(24,12)"
MULTIPLIER = "DECIMAL(18,4)"
METRIC = "DECIMAL(24,12)"
AVERAGE_VOLUME = "DECIMAL(24,6)"


def _columns(names: str, definition: str) -> dict[str, str]:
    return {name: definition for name in names.split()}


# Column-specific, not table-wide: e.g. asset identifiers and true integer
# counts stay integers, while reference prices and ratios are fixed-point.
COLUMN_TYPES: dict[str, dict[str, str]] = {
    "SECURITIES": {"REFERENCE_PRICE": PRICE},
    "PRICES": {
        **_columns("OPEN HIGH LOW CLOSE DIVIDEND SPLIT_RATIO", PRICE),
        "VOLUME": COUNT,
    },
    "FX_RATES": {
        **_columns("OPEN HIGH LOW CLOSE", FX),
        "VOLUME": COUNT,
    },
    "PRICES_CANDIDATES": {
        **_columns("OPEN HIGH LOW CLOSE DIVIDEND SPLIT_RATIO", PRICE),
        "VOLUME": COUNT,
    },
    # The current FX candidate pipeline explicitly quantizes at 8 places.
    # Keep that declared boundary visible; never imply that the final FX(10)
    # scale means ten digits were fetched from the original source.
    "FX_RATE_CANDIDATES": _columns("OPEN HIGH LOW CLOSE", PRICE),
    "INDEX_PRICES": _columns("OPEN HIGH LOW CLOSE", PRICE),
    "INDEX_PRICE_CANDIDATES": {
        **_columns("OPEN HIGH LOW CLOSE SETTLEMENT", PRICE),
        **_columns("VOLUME OPEN_INTEREST", COUNT),
    },
    "DERIVATIVE_PRICES": {
        **_columns("OPEN HIGH LOW CLOSE SETTLEMENT", PRICE),
        **_columns("VOLUME OPEN_INTEREST", COUNT),
    },
    "DERIVATIVE_PRICE_CANDIDATES": {
        **_columns("OPEN HIGH LOW CLOSE SETTLEMENT", PRICE),
        **_columns("VOLUME OPEN_INTEREST", COUNT),
    },
    "DERIVATIVE_CONTRACTS": {
        "MULTIPLIER": MULTIPLIER,
        "STRIKE": PRICE,
    },
    "DERIVATIVE_SPECIFICATIONS": _columns("TICK_SIZE LOT_SIZE", PRICE),
    "INDEX_CONSTITUENTS": {"WEIGHT": WEIGHT},
    "RATE_OBSERVATIONS": _columns("RATE BID ASK", RATE),
    "RATE_OBSERVATION_CANDIDATES": _columns("RATE BID ASK", RATE),
    "US_OPTION_CHAIN_SNAPSHOTS": {
        **_columns("STRIKE BID ASK LAST_PRICE", PRICE),
        **_columns("VOLUME OPEN_INTEREST", COUNT),
        "IMPLIED_VOLATILITY": METRIC,
    },
    "GPW_STOCK_METRICS_DAILY": {
        **_columns("CLOSE_PRICE CLOSE_SMA_20D CLOSE_SMA_60D HIGH_52W LOW_52W", PRICE),
        **_columns("VOLUME_AVG_20D VOLUME_AVG_60D VOLUME_AVG_252D", AVERAGE_VOLUME),
        **_columns(
            "DAILY_LOG_RETURN DAILY_SIMPLE_RETURN VOLUME_RELATIVE_20D "
            "VOLATILITY_DAILY_20D VOLATILITY_ANNUAL_20D "
            "VOLATILITY_ANNUAL_252D MEAN_ABS_LOG_RETURN_252D POSITION_52W",
            METRIC,
        ),
    },
}

INTEGER_TYPES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "INT",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
    }
)
FLOAT_PATTERN = re.compile(r"^(?:FLOAT(?:4|8)?|DOUBLE(?: PRECISION)?|REAL)$", re.IGNORECASE)
DECIMAL_PATTERN = re.compile(
    r"^(?:(?:DECIMAL|NUMERIC|NUMBER)\((\d+)\s*,\s*(\d+)\))$", re.IGNORECASE
)


def normalized_type(raw: str) -> str:
    value = re.sub(r"\s+", " ", raw.strip().upper())
    match = DECIMAL_PATTERN.fullmatch(value)
    if match:
        return f"DECIMAL({int(match[1])},{int(match[2])})"
    return value


def floating_type(raw: str) -> bool:
    return bool(FLOAT_PATTERN.fullmatch(normalized_type(raw)))


def decimal_parts(sql_type: str) -> tuple[int, int]:
    match = DECIMAL_PATTERN.fullmatch(sql_type)
    if not match:
        raise ValueError(f"Not a fixed-point declaration: {sql_type}")
    precision, scale = map(int, match.groups())
    if not (0 <= scale <= precision <= 38):
        raise ValueError(f"Invalid precision/scale: {sql_type}")
    return precision, scale


@dataclass(frozen=True)
class Column:
    table: str
    name: str
    actual: str
    target: str | None

    @property
    def float_stored(self) -> bool:
        return floating_type(self.actual)

    @property
    def needs_conversion(self) -> bool:
        if not self.target:
            return False
        return normalized_type(self.actual) != normalized_type(self.target) and (
            self.float_stored
            or normalized_type(self.actual).startswith("DECIMAL(")
            or normalized_type(self.actual) in INTEGER_TYPES
        )

    @property
    def unknown_float(self) -> bool:
        return self.float_stored and self.target is None

    @property
    def unknown_storage(self) -> bool:
        return self.target is not None and not (
            self.float_stored
            or normalized_type(self.actual).startswith("DECIMAL(")
            or normalized_type(self.actual) in INTEGER_TYPES
        )


def classify(columns: list[tuple[str, str, str]]) -> list[Column]:
    return [
        Column(
            table.upper(),
            name.upper(),
            actual,
            COLUMN_TYPES.get(table.upper(), {}).get(name.upper()),
        )
        for table, name, actual in columns
    ]
