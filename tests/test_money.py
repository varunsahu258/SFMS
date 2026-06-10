"""Exact decimal payment validation tests."""

from decimal import Decimal

import pytest

from money import OverpaymentError, validate_payment_amount


def test_valid_amount():
    assert validate_payment_amount("1234.50", "2000") == Decimal("1234.50")


@pytest.mark.parametrize("value", ["inf", "Infinity", "NaN", "-1", "0", "1.001", "10000000"])
def test_invalid_amounts(value):
    with pytest.raises(ValueError):
        validate_payment_amount(value, "20000000")


def test_overpayment_without_flag_reports_exact_excess():
    with pytest.raises(OverpaymentError) as error:
        validate_payment_amount("100.01", "100.00")
    assert error.value.excess == Decimal("0.01")


def test_overpayment_with_flag_is_allowed():
    assert validate_payment_amount("125.00", "100.00", allow_overpayment=True) == Decimal("125.00")
