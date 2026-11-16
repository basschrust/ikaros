"""Loss-aware fixed-point conversion at the Snowflake ingestion boundary.

Using Decimal(str(value)) avoids adding a *second* binary-float conversion;
it cannot recover precision lost by a source that already supplied float.
All database bindings use decimal text, with explicit Snowflake TO_DECIMAL.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math

PRICE_SCALE = 8
PRICE_PRECISION = 24
DIVIDEND_SCALE = 8
VOLUME_PRECISION = 38


def decimal_text(value: object, *, scale: int = PRICE_SCALE, allow_null: bool = True) -> str | None:
    """Prepare decimal text, reject NaN/infinity and out-of-range quantities.

    Values finer than the configured fixed-point scale are rounded HALF_UP;
    source quote precision and true tick sizes should be validated upstream.
    """
    if value is None or str(value) in {"<NA>", "nan", "NaN", "NaT", "None"}:
        if allow_null:
            return None
        raise ValueError("Required decimal value is null")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Nonfinite decimal value")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid decimal value: {value!r}") from exc
    if not number.is_finite():
        raise ValueError("Nonfinite decimal value")
    if scale < 0 or scale >= PRICE_PRECISION:
        raise ValueError("Invalid decimal scale")
    quantized = number.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
    if len(quantized.as_tuple().digits) - quantized.as_tuple().exponent > PRICE_PRECISION + scale:
        raise ValueError("Decimal exceeds storage precision")
    if abs(quantized) >= Decimal(10) ** (PRICE_PRECISION - scale):
        raise ValueError("Decimal exceeds storage precision")
    return format(quantized, f".{scale}f")


def integer_text(value: object, *, allow_null: bool = True) -> str | None:
    text = decimal_text(value, scale=0, allow_null=allow_null)
    if text is None:
        return None
    number = Decimal(str(value))
    if number != number.to_integral_value():
        raise ValueError(f"Nonintegral volume: {value!r}")
    if abs(number) >= Decimal(10) ** VOLUME_PRECISION:
        raise ValueError("Volume exceeds storage precision")
    return text


def candle_params(record: object) -> tuple[str | None, ...]:
    return (
        decimal_text(record.open),
        decimal_text(record.high),
        decimal_text(record.low),
        decimal_text(record.close),
        integer_text(record.volume),
        decimal_text(record.dividend),
        decimal_text(record.split_ratio),
    )
