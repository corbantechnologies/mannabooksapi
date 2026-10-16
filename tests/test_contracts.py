"""Frontend/backend contract fixes from the API sweep (no database needed)."""
from decimal import Decimal

from app.core.responses import add_camel_keys
from app.modules.approvals.router import ApprovalDecisionRequest
from app.modules.crm.router import CreateDealRequest
from app.modules.hr.router import CreateEmployeeRequest
from app.modules.portal.router import QuotationDecisionPayload, _with_contact


def test_request_models_accept_camel_case():
    emp = CreateEmployeeRequest.model_validate({"shop_id": "s", "fullName": "Jane", "kraPin": "A1", "baseSalary": "50000"})
    assert emp.full_name == "Jane" and emp.kra_pin == "A1" and emp.base_salary == Decimal("50000")


def test_deal_request_matches_the_form():
    d = CreateDealRequest.model_validate({
        "shop_id": "s", "title": "Big order", "contact_name": "Ann",
        "estimated_value": 120000, "win_probability": 60, "expected_close_date": "2026-12-01",
    })
    assert d.estimated_value == Decimal("120000") and d.win_probability == 60 and d.expected_close_date == "2026-12-01"


def test_approval_decision_alias():
    assert ApprovalDecisionRequest.model_validate({"decision": "APPROVED"}).action == "APPROVED"


def test_portal_amendment_keeps_contact():
    p = QuotationDecisionPayload.model_validate({"amendmentNotes": "Change qty", "clientName": "Ann", "contactEmail": "a@x.co"})
    assert _with_contact(p.amendment_notes, p.client_name, p.contact_email) == "From Ann <a@x.co>: Change qty"


def test_responses_expose_camel_case_without_touching_data_keys():
    out = add_camel_keys({"deal": {"expected_close_date": "2026-12-01"}, "stages": {"PROPOSAL_SENT": 1}})
    assert out["deal"]["expectedCloseDate"] == "2026-12-01"
    assert out["stages"] == {"PROPOSAL_SENT": 1}
