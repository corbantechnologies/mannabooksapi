# app/modules/payroll/rates.py
"""
Kenyan statutory payroll parameters and the pay calculator.

Rates are effective-dated rows in `statutory_rate_sets` (managed in the super admin console).
The calculator is pure (no DB) so it can be unit-tested and reused for previews.

Rates JSON layout (all numbers as strings to keep Decimal precision):
{
  "nssf":         {"rate": "0.06", "lower_limit": "9000", "upper_limit": "108000", "employer_match": true},
  "shif":         {"rate": "0.0275", "minimum": "300"},
  "housing_levy": {"employee_rate": "0.015", "employer_rate": "0.015"},
  "nita":         {"employer_per_employee": "50"},
  "paye": {
    "bands": [["24000", "0.10"], ["8333", "0.25"], ["467667", "0.30"], ["300000", "0.325"], [null, "0.35"]],
    "personal_relief": "2400",
    "deduct_nssf": true, "deduct_shif": true, "deduct_housing_levy": true
  }
}
"""
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import quantize_money
from app.models.payroll import StatutoryRateSet

PAYE_BANDS = [["24000", "0.10"], ["8333", "0.25"], ["467667", "0.30"], ["300000", "0.325"], [None, "0.35"]]


def _ke_rates(lower: str, upper: str) -> dict:
    return {
        "nssf": {"rate": "0.06", "lower_limit": lower, "upper_limit": upper, "employer_match": True},
        "shif": {"rate": "0.0275", "minimum": "300"},
        "housing_levy": {"employee_rate": "0.015", "employer_rate": "0.015"},
        "nita": {"employer_per_employee": "50"},
        "paye": {
            "bands": PAYE_BANDS,
            "personal_relief": "2400",
            "deduct_nssf": True,
            "deduct_shif": True,
            "deduct_housing_levy": True,
        },
    }


# Seeded by Alembic revision 0003. NSSF Act 2013 phase-in: Year 3 (Feb 2025) and Year 4 (Feb 2026).
SEED_RATE_SETS = [
    {
        "name": "Kenya - NSSF Year 3 (Feb 2025)",
        "effective_from": date(2025, 2, 1),
        "rates": _ke_rates("8000", "72000"),
        "is_verified": True,
        "notes": "NSSF LEL 8,000 / UEL 72,000 (max employee 4,320). SHIF 2.75% (min 300), AHL 1.5% + 1.5%, "
                 "NITA 50. SHIF and AHL deductible for PAYE from 27 Dec 2024.",
    },
    {
        "name": "Kenya - NSSF Year 4 (Feb 2026)",
        "effective_from": date(2026, 2, 1),
        "rates": _ke_rates("9000", "108000"),
        "is_verified": False,
        "notes": "NSSF LEL 9,000 / UEL 108,000 (max employee 6,480). CONFIRM against the NSSF circular, "
                 "then mark as verified.",
    },
]

# Used only if the database has no rate sets at all.
DEFAULT_KE_RATES = SEED_RATE_SETS[-1]["rates"]

STATUTORY_KEYS = (
    "nssf_employee", "shif", "housing_levy_employee", "paye",
    "nssf_employer", "housing_levy_employer", "nita",
)


def _d(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value if value not in (None, "") else "0"))


def validate_rates(rates: dict) -> dict:
    """Raise ValueError if the rates JSON is incomplete or not numeric."""
    try:
        n, s, h, t, p = rates["nssf"], rates["shif"], rates["housing_levy"], rates["nita"], rates["paye"]
        for v in (n["rate"], n["lower_limit"], n["upper_limit"], s["rate"], s["minimum"],
                  h["employee_rate"], h["employer_rate"], t["employer_per_employee"], p["personal_relief"]):
            if _d(v) < 0:
                raise ValueError("Rates cannot be negative.")
        if _d(n["upper_limit"]) < _d(n["lower_limit"]):
            raise ValueError("NSSF upper limit must be at least the lower limit.")
        bands = p["bands"]
        if not bands or bands[-1][0] is not None:
            raise ValueError("The last PAYE band must have an open (null) width.")
        for width, rate in bands:
            if width is not None and _d(width) <= 0:
                raise ValueError("PAYE band widths must be positive.")
            if not (Decimal("0") <= _d(rate) <= Decimal("1")):
                raise ValueError("PAYE rates must be between 0 and 1.")
    except (KeyError, TypeError, IndexError, ArithmeticError) as exc:
        raise ValueError(f"Invalid statutory rates: {exc}") from exc
    return rates


def compute_pay(
    rates: dict,
    basic_salary: Any,
    allowances: Any = 0,
    commissions: Any = 0,
    custom_deductions: Any = 0,
    mode: str = "KENYA_STATUTORY",
) -> dict[str, Decimal]:
    """Compute one employee's monthly pay. All values are Decimal rounded half-up to cents."""
    zero = Decimal("0.00")
    basic = quantize_money(_d(basic_salary))
    allow = quantize_money(_d(allowances))
    comms = quantize_money(_d(commissions))
    custom = quantize_money(_d(custom_deductions))
    gross = basic + allow + comms

    result = {
        "basic_salary": basic,
        "allowances": allow,
        "commissions": comms,
        "gross_pay": gross,
        "nssf_employee": zero,
        "shif": zero,
        "housing_levy_employee": zero,
        "taxable_pay": zero,
        "tax_charged": zero,
        "personal_relief": zero,
        "paye": zero,
        "custom_deductions": custom,
        "nssf_employer": zero,
        "housing_levy_employer": zero,
        "nita": zero,
    }

    if mode != "MANUAL_CUSTOM" and gross > 0:
        n, s, h, t, p = rates["nssf"], rates["shif"], rates["housing_levy"], rates["nita"], rates["paye"]

        # NSSF: Tier I on earnings up to the lower limit, Tier II between the lower and upper limits.
        lel, uel, nrate = _d(n["lower_limit"]), _d(n["upper_limit"]), _d(n["rate"])
        pensionable = min(gross, uel)
        nssf = quantize_money(min(pensionable, lel) * nrate + max(Decimal("0"), pensionable - lel) * nrate)
        shif = max(quantize_money(gross * _d(s["rate"])), _d(s["minimum"]))
        ahl = quantize_money(gross * _d(h["employee_rate"]))

        taxable = gross
        if p.get("deduct_nssf", True):
            taxable -= nssf
        if p.get("deduct_shif", True):
            taxable -= shif
        if p.get("deduct_housing_levy", True):
            taxable -= ahl
        taxable = max(zero, taxable)

        tax = Decimal("0")
        remaining = taxable
        for width, rate in p["bands"]:
            if remaining <= 0:
                break
            portion = remaining if width is None else min(remaining, _d(width))
            tax += portion * _d(rate)
            remaining -= portion
        tax = quantize_money(tax)
        relief = min(tax, _d(p["personal_relief"]))

        result.update(
            nssf_employee=nssf,
            shif=shif,
            housing_levy_employee=ahl,
            taxable_pay=taxable,
            tax_charged=tax,
            personal_relief=quantize_money(relief),
            paye=quantize_money(tax - relief),
            nssf_employer=nssf if n.get("employer_match", True) else zero,
            housing_levy_employer=quantize_money(gross * _d(h["employer_rate"])),
            nita=quantize_money(_d(t["employer_per_employee"])),
        )

    total_deductions = (
        result["nssf_employee"] + result["shif"] + result["housing_levy_employee"] + result["paye"] + custom
    )
    result["total_deductions"] = total_deductions
    result["net_pay"] = max(zero, gross - total_deductions)
    result["employer_cost"] = gross + result["nssf_employer"] + result["housing_levy_employer"] + result["nita"]
    return result


def period_start(period: str) -> date:
    """'2026-10' -> date(2026, 10, 1). Raises ValueError for bad input."""
    year, month = period.split("-")
    return date(int(year), int(month), 1)


async def get_rate_set_for(db: AsyncSession, on: date, country: str = "KE") -> Optional[StatutoryRateSet]:
    """Latest rate set effective on `on`; falls back to the earliest set for older periods."""
    row = (
        await db.execute(
            select(StatutoryRateSet)
            .where(StatutoryRateSet.country == country, StatutoryRateSet.effective_from <= on)
            .order_by(StatutoryRateSet.effective_from.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row:
        return row
    return (
        await db.execute(
            select(StatutoryRateSet)
            .where(StatutoryRateSet.country == country)
            .order_by(StatutoryRateSet.effective_from.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
