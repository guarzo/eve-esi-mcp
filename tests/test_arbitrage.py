from __future__ import annotations

import os

os.environ.setdefault("EVE_ESI_MCP_CONTACT", "test@example.com")

from eve_esi_mcp.tools.arbitrage import freight_cost, profit_after_fees  # noqa: E402


def test_freight_cost_minimum_fee_applied():
    result = freight_cost(m3=10, collateral=1_000_000, rate_per_m3=1000, collateral_pct=1.0, minimum_fee=500_000)
    assert result["minimum_applied"] is True
    assert result["fee_isk"] == 500_000


def test_freight_cost_volume_dominates():
    result = freight_cost(m3=10_000, collateral=100_000_000, rate_per_m3=1000, collateral_pct=1.0, minimum_fee=500_000)
    # 10_000 * 1000 = 10_000_000 per_m3; 100M * 1% = 1_000_000 collateral
    assert result["per_m3_component"] == 10_000_000
    assert result["collateral_component"] == 1_000_000
    assert result["fee_isk"] == 11_000_000
    assert result["minimum_applied"] is False


def test_profit_after_fees_positive():
    r = profit_after_fees(
        buy_price=5_000_000,
        sell_price=5_500_000,
        qty=10,
        sales_tax_pct=3.6,
        broker_fee_pct_buy=3.0,
        broker_fee_pct_sell=3.0,
        freight_isk=500_000,
    )
    cost_basis = 5_000_000 * 10 * 1.03
    gross = 5_500_000 * 10
    sell_fees = gross * 0.066
    expected_net = gross - sell_fees - cost_basis - 500_000
    assert abs(r["net_profit_isk"] - expected_net) < 1e-6
    assert r["cost_basis_isk"] == cost_basis


def test_profit_after_fees_negative_when_tight():
    r = profit_after_fees(
        buy_price=1_000_000,
        sell_price=1_010_000,
        qty=1,
        sales_tax_pct=3.6,
        broker_fee_pct_buy=3.0,
        broker_fee_pct_sell=3.0,
        freight_isk=0,
    )
    assert r["net_profit_isk"] < 0
