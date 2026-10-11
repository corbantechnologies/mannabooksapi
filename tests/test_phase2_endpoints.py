import pytest
import uuid
from decimal import Decimal
from datetime import date
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.session import async_session_factory
from app.models.identity import Shop, User, ShopMember
from app.models.client import Client
from app.models.account import ChartOfAccount
from app.models.item import Product
from app.models.inventory import StockLocation


@pytest.mark.asyncio
async def test_crm_operations_hr_phase2_endpoints_lifecycle():
    """End-to-end integration test verifying CRM, Operations, HR, and Finance HTTP endpoints."""
    settings = get_settings()
    test_user_id = uuid.uuid4()
    test_shop_id = uuid.uuid4()
    test_slug = f"test-p2-{test_shop_id.hex[:8]}"

    # Setup DB seeds
    async with async_session_factory() as session:
        user = User(
            id=test_user_id,
            name="Phase 2 Manager",
            email=f"p2-manager-{test_user_id.hex[:8]}@mannabooks.co.ke",
            password_hash="mock_hash",
        )
        session.add(user)
        await session.flush()

        shop = Shop(
            id=test_shop_id,
            owner_id=test_user_id,
            name="Phase 2 Enterprise Hub",
            slug=test_slug,
            currency="KES",
        )
        session.add(shop)
        await session.flush()

        member = ShopMember(
            shop_id=test_shop_id,
            user_id=test_user_id,
            role="OWNER",
            is_active=True,
        )
        session.add(member)

        client_rec = Client(
            shop_id=test_shop_id,
            name="Safaricom Business Solutions",
            email="enterprise@safaricom.co.ke",
            client_type="CORPORATE",
            tax_pin="P051999888Z",
        )
        session.add(client_rec)

        coa = ChartOfAccount(
            shop_id=test_shop_id,
            code="6100",
            name="Marketing & Advertising",
            account_type="EXPENSE",
        )
        session.add(coa)

        raw_prod = Product(
            shop_id=test_shop_id,
            name="Raw Steel Sheet 2mm",
            sku="RAW-STL-02",
            item_type="PRODUCT",
            unit_price=Decimal("1200.00"),
        )
        fin_prod = Product(
            shop_id=test_shop_id,
            name="Fabricated Server Rack 42U",
            sku="FIN-SRV-42U",
            item_type="PRODUCT",
            unit_price=Decimal("45000.00"),
        )
        loc = StockLocation(
            shop_id=test_shop_id,
            name="Main Warehouse",
            code="WH-MAIN",
            is_default=True,
            is_active=True,
        )
        session.add_all([raw_prod, fin_prod, loc])
        await session.commit()

        client_id_str = str(client_rec.id)
        coa_id_str = str(coa.id)
        raw_prod_id_str = str(raw_prod.id)
        fin_prod_id_str = str(fin_prod.id)
        loc_id_str = str(loc.id)

    token = create_access_token(test_user_id)
    auth_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    internal_headers = {
        "X-Internal-Key": settings.INTERNAL_API_KEY,
        "Content-Type": "application/json",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # =====================================================================
        # 1. CRM PIPELINE: DEALS, PROPOSALS, CONTRACTS & PROJECTS
        # =====================================================================
        # 1.1 Create Deal
        deal_payload = {
            "shop_id": str(test_shop_id),
            "client_id": client_id_str,
            "title": "Cloud Infrastructure Migration",
            "value": "250000.00",
            "currency": "KES",
            "stage": "QUALIFIED",
            "probability": 70,
            "close_date": "2026-11-30",
        }
        res = await client.post("/v1/crm/deals", json=deal_payload, headers=auth_headers)
        assert res.status_code == 200, res.text
        deal_data = res.json()
        assert deal_data["success"] is True
        deal_id = deal_data["deal"]["id"]

        # 1.2 List Deals
        res = await client.get(f"/v1/crm/deals?shop_id={test_shop_id}", headers=auth_headers)
        assert res.status_code == 200
        assert len(res.json()["deals"]) >= 1

        # 1.3 Update Deal
        patch_deal = {
            "stage": "NEGOTIATION",
            "probability": 85,
        }
        res = await client.patch(f"/v1/crm/deals/{deal_id}?shop_id={test_shop_id}", json=patch_deal, headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["success"] is True

        # 1.4 Create Proposal
        prop_payload = {
            "shop_id": str(test_shop_id),
            "deal_id": deal_id,
            "client_id": client_id_str,
            "title": "SLA & Cloud Migration Proposal",
            "currency": "KES",
            "valid_until": "2026-12-15",
            "notes": "Includes initial audit and migration",
            "terms_and_conditions": "Payment due within 30 days of acceptance",
            "items": [
                {
                    "description": "Enterprise Cloud Setup",
                    "quantity": "1.00",
                    "unit_price": "150000.00",
                    "discount_amount": "0.00",
                    "tax_amount": "0.00",
                    "item_total": "150000.00",
                },
                {
                    "description": "Network Hardening",
                    "quantity": "2.00",
                    "unit_price": "50000.00",
                    "discount_amount": "0.00",
                    "tax_amount": "0.00",
                    "item_total": "100000.00",
                }
            ]
        }
        res = await client.post("/v1/crm/proposals", json=prop_payload, headers=auth_headers)
        assert res.status_code == 200, res.text
        prop_resp = res.json()
        assert prop_resp["success"] is True
        guest_token = prop_resp["token"]
        prop_id = prop_resp["proposalId"]

        # 1.4b Edit / Update Proposal
        edit_payload = {
            "title": "Corporate Cyber Audit 2026 - Updated Scope",
            "notes": "Added endpoint monitoring and server audits",
            "items": [
                {
                    "description": "Network Hardening & Firewall Upgrade",
                    "quantity": "2.00",
                    "unit_price": "60000.00",
                    "discount_amount": "0.00",
                    "item_total": "120000.00",
                }
            ]
        }
        res_edit = await client.put(f"/v1/crm/proposals/{prop_id}", json=edit_payload, headers=auth_headers)
        assert res_edit.status_code == 200, res_edit.text
        assert res_edit.json()["success"] is True

        # Verify updated proposal
        res_get_prop = await client.get(f"/v1/crm/proposals/{prop_id}", headers=auth_headers)
        assert res_get_prop.status_code == 200
        get_data = res_get_prop.json()
        assert get_data["proposal"]["title"] == "Corporate Cyber Audit 2026 - Updated Scope"
        assert get_data["proposal"]["subtotal"] == 120000.00

        # 1.5 Public View & Accept Proposal (No Login Required)
        res_pub = await client.get(f"/v1/crm/proposals/public/{guest_token}")
        assert res_pub.status_code == 200, res_pub.text
        pub_data = res_pub.json()
        assert pub_data["success"] is True
        assert pub_data["proposal"]["subtotal"] == 120000.00

        res_accept = await client.post(f"/v1/crm/proposals/public/{guest_token}/accept")
        assert res_accept.status_code == 200
        assert res_accept.json()["success"] is True

        # 1.6 Create Contract
        contract_payload = {
            "shop_id": str(test_shop_id),
            "client_id": client_id_str,
            "deal_id": deal_id,
            "title": "Managed Services Retainer 2026",
            "currency": "KES",
            "monthly_fee": "45000.00",
            "is_retainer_hours": True,
            "monthly_hours_allocated": "20.00",
            "hourly_rate": "3500.00",
            "start_date": "2026-10-01",
            "end_date": "2027-09-30",
            "terms_and_conditions": "Monthly renewal",
        }
        res = await client.post("/v1/crm/contracts", json=contract_payload, headers=auth_headers)
        assert res.status_code == 200, res.text
        contract_data = res.json()
        assert contract_data["success"] is True
        contract_id = contract_data["contract"]["id"]

        # 1.7 Create Project
        project_payload = {
            "shop_id": str(test_shop_id),
            "client_id": client_id_str,
            "contract_id": contract_id,
            "name": "Datacenter Migration Alpha",
            "description": "Migration of legacy on-prem infrastructure",
            "currency": "KES",
            "budget_type": "FIXED",
            "budget_amount": "500000.00",
            "budget_hours": "120.00",
            "start_date": "2026-10-15",
            "end_date": "2027-01-31",
        }
        res = await client.post("/v1/crm/projects", json=project_payload, headers=auth_headers)
        assert res.status_code == 200, res.text
        assert res.json()["success"] is True

        # =====================================================================
        # 2. OPERATIONS: BUDGETS, COST CENTERS, BOM, PRODUCTION & LOYALTY
        # =====================================================================
        # 2.1 Create Budget
        budget_payload = {
            "shop_id": str(test_shop_id),
            "account_id": coa_id_str,
            "month": 11,
            "year": 2026,
            "monthly_limit": "75000.00",
        }
        res = await client.post("/v1/operations/budgets", json=budget_payload, headers=auth_headers)
        assert res.status_code == 200, res.text
        assert res.json()["success"] is True

        # 2.2 Create Cost Center
        cc_payload = {
            "shop_id": str(test_shop_id),
            "code": "DEV-OPS",
            "name": "DevOps & Cloud Engineering",
            "department": "Engineering",
        }
        res = await client.post("/v1/operations/cost-centers", json=cc_payload, headers=auth_headers)
        assert res.status_code == 200, res.text
        assert res.json()["success"] is True

        # 2.3 Create Bill of Materials (BOM)
        bom_payload = {
            "shop_id": str(test_shop_id),
            "finished_product_id": fin_prod_id_str,
            "name": "Server Rack 42U Standard Assembly",
            "labor_cost_estimate": "3500.00",
            "overhead_cost_estimate": "1500.00",
            "items": [
                {
                    "raw_material_product_id": raw_prod_id_str,
                    "quantity_required": "4.00",
                    "wastage_percentage": "5.00",
                }
            ]
        }
        res = await client.post("/v1/operations/bom", json=bom_payload, headers=auth_headers)
        assert res.status_code == 200, res.text
        bom_id = res.json()["bomId"]

        # 2.4 Create Production Order
        po_payload = {
            "shop_id": str(test_shop_id),
            "bom_id": bom_id,
            "target_location_id": loc_id_str,
            "units_to_produce": "10.00",
            "total_cost_kes": "53000.00",
            "notes": "Batch production run for telco client",
        }
        res = await client.post("/v1/operations/production-orders", json=po_payload, headers=auth_headers)
        assert res.status_code == 200, res.text
        assert res.json()["success"] is True

        # 2.5 Update Loyalty Program
        loyalty_payload = {
            "shop_id": str(test_shop_id),
            "name": "Gold Club Rewards",
            "points_per_kes": "0.05",
            "kes_per_point": "1.00",
            "min_redeem_points": 50,
            "points_expiry_days": 180,
            "is_active": True,
        }
        res = await client.post("/v1/operations/loyalty/program", json=loyalty_payload, headers=auth_headers)
        assert res.status_code == 200, res.text
        assert res.json()["success"] is True

        # 2.6 Adjust Loyalty Points (Earn & Redeem)
        adjust_earn = {
            "shop_id": str(test_shop_id),
            "client_id": client_id_str,
            "points": 500,
            "movement_type": "EARN",
            "reason": "Welcome bonus points",
        }
        res = await client.post("/v1/operations/loyalty/adjust", json=adjust_earn, headers=auth_headers)
        assert res.status_code == 200, res.text
        assert res.json()["success"] is True
        assert res.json()["account"]["currentPoints"] == 500

        # =====================================================================
        # 3. HR, STATUTORY PAYROLL & EXPENSE CLAIMS
        # =====================================================================
        # 3.1 Create Employee
        emp_payload = {
            "shop_id": str(test_shop_id),
            "full_name": "Daniel Kiprono",
            "email": "daniel.k@mannabooks.co.ke",
            "national_id": "32114455",
            "kra_pin": "A009988776B",
            "nhif_number": "NHIF-7788",
            "nssf_number": "NSSF-9900",
            "base_salary": "85000.00",
            "department": "Engineering",
            "designation": "Lead DevOps Engineer",
            "employment_type": "FULL_TIME",
            "mpesa_phone": "+254712345678",
        }
        res = await client.post("/v1/employees", json=emp_payload, headers=auth_headers)
        assert res.status_code == 200, res.text
        emp_data = res.json()
        assert emp_data["success"] is True
        emp_id = emp_data["employee"]["id"]

        # 3.2 Calculate Statutory Payroll
        calc_payload = [
            {
                "employee_id": emp_id,
                "employee_name": "Daniel Kiprono",
                "base_salary": 85000.0,
                "allowances": 15000.0,
                "commissions": 0.0,
                "custom_deductions": 0.0,
            }
        ]
        res = await client.post("/v1/payroll/calculate", json=calc_payload)
        assert res.status_code == 200, res.text
        calc_resp = res.json()
        assert calc_resp["success"] is True
        calc_line = calc_resp["calculations"][0]
        assert calc_line["gross_salary"] == 100000.0
        assert calc_line["nssf"] == 2160.0
        assert calc_line["shif"] == 2750.0
        assert calc_line["housing_levy"] == 1500.0

        # 3.3 Commit Payroll Run
        commit_payload = {
            "shop_id": str(test_shop_id),
            "payroll_period_code": "OCTOBER-2026",
            "mode": "KENYA_STATUTORY",
            "status": "PAID",
            "lines": calc_payload,
        }
        res = await client.post("/v1/payroll/commit", json=commit_payload, headers=auth_headers)
        assert res.status_code == 200, res.text
        assert res.json()["success"] is True
        assert res.json()["docNumber"].startswith("PAY-")

        # 3.4 Create & Review Expense Claim
        claim_payload = {
            "shop_id": str(test_shop_id),
            "employee_id": emp_id,
            "title": "On-site Client Network Deployment Expenses",
            "description": "Fuel and site cablings",
            "amount": "4800.00",
            "claim_date": "2026-10-10",
            "category": "FUEL",
            "submit_now": True,
        }
        res = await client.post("/v1/expense-claims", json=claim_payload, headers=auth_headers)
        assert res.status_code == 200, res.text
        claim_data = res.json()
        assert claim_data["success"] is True
        claim_id = claim_data["claim"]["id"]

        review_payload = {
            "decision": "APPROVED",
            "notes": "Verified against receipt attachments",
        }
        res = await client.post(f"/v1/expense-claims/{claim_id}/review?shop_id={test_shop_id}", json=review_payload, headers=auth_headers)
        assert res.status_code == 200, res.text
        assert res.json()["success"] is True

        # 3.5 Disburse Expense Claim (creates official expense record)
        disburse_payload = {
            "payment_channel": "MPESA",
            "payment_reference": "QK12345678",
        }
        res = await client.post(f"/v1/expense-claims/{claim_id}/disburse?shop_id={test_shop_id}", json=disburse_payload, headers=auth_headers)
        assert res.status_code == 200, res.text
        assert res.json()["success"] is True

        # 3.6 Link Employee to User & Check ESS Profile
        link_payload = {"user_id": str(test_user_id)}
        res = await client.post(f"/v1/employees/{emp_id}/link-user?shop_id={test_shop_id}", json=link_payload, headers=auth_headers)
        assert res.status_code == 200, res.text
        assert res.json()["success"] is True

        res = await client.get(f"/v1/ess/profile?shop_id={test_shop_id}", headers=auth_headers)
        assert res.status_code == 200, res.text
        ess_data = res.json()
        assert ess_data["isLinked"] is True
        assert ess_data["employee"]["fullName"] == "Daniel Kiprono"

        # =====================================================================
        # 4. FINANCE & TAX SETTINGS
        # =====================================================================
        res = await client.get(f"/v1/finance/tax/settings?shop_id={test_shop_id}", headers=internal_headers)
        assert res.status_code == 200, res.text
        tax_data = res.json()
        assert tax_data["success"] is True
        assert tax_data["citRate"] == 30.0

        tax_update = {
            "shop_id": str(test_shop_id),
            "isCitActive": True,
            "citRate": 25.0,
            "taxPin": "P051999888Z",
            "isVatRegistered": True,
            "taxRegime": "CIT",
        }
        res = await client.patch("/v1/finance/tax/settings", json=tax_update, headers=internal_headers)
        assert res.status_code == 200, res.text
        tax_resp = res.json()
        assert tax_resp["success"] is True
        assert tax_resp["citRate"] == 25.0
        assert tax_resp["taxPin"] == "P051999888Z"
