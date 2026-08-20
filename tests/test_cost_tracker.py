from decimal import Decimal

import pytest

from minicode.cost_tracker import calculate_cost


def test_calculate_cost_returns_float_for_decimal_token_math() -> None:
    cost = calculate_cost(
        model="deepseek-v4-flash",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_creation_tokens=1_000_000,
    )

    assert isinstance(cost, float)
    assert cost == pytest.approx(0.5628)


def test_calculate_cost_accepts_decimal_token_counts() -> None:
    cost = calculate_cost(
        model="deepseek-v4-flash",
        input_tokens=Decimal("1000000"),
    )

    assert cost == pytest.approx(0.14)
