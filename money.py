"""Exact currency validation for SFMS financial entry workflows."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

DEFAULT_MAX_PAYMENT = Decimal("9999999.00")
MAX_PAYMENT_SETTING = "max_payment_amount"


class OverpaymentError(ValueError):
    """Raised when an amount exceeds the payable balance without approval."""

    def __init__(self, excess: Decimal):
        self.excess = excess
        super().__init__(f"Amount exceeds the due amount by {excess:.2f}.")


def max_payment_amount(conn=None) -> Decimal:
    """Read the configurable maximum, falling back safely to 9,999,999.00."""
    if conn is None:
        return DEFAULT_MAX_PAYMENT
    row = conn.execute(
        "SELECT value FROM settings WHERE key=?", (MAX_PAYMENT_SETTING,)
    ).fetchone()
    if not row:
        return DEFAULT_MAX_PAYMENT
    try:
        value = Decimal(str(row[0]))
    except (InvalidOperation, ValueError):
        return DEFAULT_MAX_PAYMENT
    return value if value.is_finite() and value > 0 else DEFAULT_MAX_PAYMENT


def validate_payment_amount(
    raw_value,
    due_amount,
    allow_overpayment: bool = False,
    *,
    maximum: Decimal | str | int | None = None,
) -> Decimal:
    """Parse and validate a positive, finite, two-decimal currency amount."""
    try:
        amount = Decimal(str(raw_value).strip())
        due = Decimal(str(due_amount).strip())
        limit = DEFAULT_MAX_PAYMENT if maximum is None else Decimal(str(maximum))
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise ValueError("Amount must be a valid number.") from exc
    if not amount.is_finite():
        raise ValueError("Amount must be finite.")
    if not due.is_finite() or not limit.is_finite() or limit <= 0:
        raise ValueError("Configured currency limits are invalid.")
    if amount.as_tuple().exponent < -2:
        raise ValueError("Amount cannot have more than two decimal places.")
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    if amount > limit:
        raise ValueError(f"Amount exceeds the configured maximum of {limit:.2f}.")
    if not allow_overpayment and amount > due:
        raise OverpaymentError(amount - due)
    return amount
