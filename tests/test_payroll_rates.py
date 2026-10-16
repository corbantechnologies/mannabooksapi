"""Statutory payroll calculator. Expected figures are worked by hand from the seeded rate sets."""
from decimal import Decimal as D

import pytest

from app.modules.payroll.rates import SEED_RATE_SETS, compute_pay, validate_rates
from app.modules.payroll.service import normalize_period

Y2025 = SEED_RATE_SETS[0]["rates"]  # NSSF LEL 8,000 / UEL 72,000
Y2026 = SEED_RATE_SETS[1]["rates"]  # NSSF LEL 9,000 / UEL 108,000


def test_mid_salary_2026():
    c = compute_pay(Y2026, D("50000"))
    assert c["nssf_employee"] == D("3000.00")  # 540 + 2,460
    assert c["shif"] == D("1375.00")
    assert c["housing_levy_employee"] == D("750.00")
    assert c["taxable_pay"] == D("44875.00")  # NSSF, SHIF and AHL deducted
    assert c["tax_charged"] == D("8245.85")
    assert c["paye"] == D("5845.85")
    assert c["net_pay"] == D("39029.15")
    assert c["nssf_employer"] == D("3000.00")
    assert c["housing_levy_employer"] == D("750.00")
    assert c["nita"] == D("50.00")
    assert c["employer_cost"] == D("53800.00")


def test_low_salary_relief_covers_tax_2025():
    c = compute_pay(Y2025, D("15000"))
    assert c["nssf_employee"] == D("900.00")
    assert c["shif"] == D("412.50")
    assert c["paye"] == D("0.00")
    assert c["personal_relief"] == c["tax_charged"]


def test_shif_minimum():
    assert compute_pay(Y2025, D("8000"))["shif"] == D("300.00")


def test_high_salary_hits_top_bands_and_nssf_cap_2025():
    c = compute_pay(Y2025, D("1000000"))
    assert c["nssf_employee"] == D("4320.00")
    assert c["taxable_pay"] == D("953180.00")
    assert c["paye"] == D("293496.35")


def test_manual_mode_only_applies_custom_deductions():
    c = compute_pay(Y2026, D("40000"), allowances=D("5000"), custom_deductions=D("2000"), mode="MANUAL_CUSTOM")
    assert c["gross_pay"] == D("45000.00")
    assert c["paye"] == c["nssf_employee"] == c["nita"] == D("0.00")
    assert c["net_pay"] == D("43000.00")


def test_zero_pay():
    c = compute_pay(Y2026, 0)
    assert c["net_pay"] == D("0.00") and c["shif"] == D("0.00") and c["nita"] == D("0.00")


@pytest.mark.parametrize(
    "raw,expected",
    [("2026-10", "2026-10"), ("2026-03-15", "2026-03"), ("OCTOBER-2026", "2026-10"), ("Oct 2026", "2026-10")],
)
def test_normalize_period(raw, expected):
    assert normalize_period(raw) == expected


def test_validate_rates_rejects_bad_bands():
    bad = {**Y2026, "paye": {**Y2026["paye"], "bands": [["24000", "0.10"]]}}
    with pytest.raises(ValueError):
        validate_rates(bad)
    assert validate_rates(Y2026) is Y2026
