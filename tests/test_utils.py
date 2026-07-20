from __future__ import annotations

from omega_quant.utils import as_float, finite_float, money, now_ms, pct


def test_finite_float_rejects_nan_inf():
    assert finite_float(float("nan")) is None
    assert finite_float(float("inf")) is None
    assert finite_float(float("-inf")) is None
    assert finite_float(1.5) == 1.5
    assert finite_float(0) == 0.0
    assert finite_float("bad") is None
    assert finite_float(None) is None


def test_pct_default_missing():
    assert pct(0.1234) == "12.34%"
    assert pct(None) == "n/a"
    assert pct("bad") == "n/a"


def test_pct_custom_missing():
    assert pct(None, missing="--") == "--"
    assert pct(0.5, missing="--") == "50.00%"


def test_money_default_missing():
    assert money(1234.5) == "1,234.50 USD"
    assert money(None) == "n/a"
    assert money("bad") == "n/a"


def test_money_custom_missing():
    assert money(None, missing="--") == "--"
    assert money(100.0, missing="--") == "100.00 USD"


def test_money_custom_currency():
    assert money(50.0, currency="HKD") == "50.00 HKD"


def test_as_float_default():
    assert as_float(None) == 0.0
    assert as_float("bad") == 0.0
    assert as_float(3.14) == 3.14
    assert as_float(None, default=1.0) == 1.0


def test_now_ms_is_reasonable():
    import time
    before = int(time.time() * 1000)
    result = now_ms()
    after = int(time.time() * 1000)
    assert before <= result <= after
