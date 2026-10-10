import asyncio
from app.db.session import engine
from app.db.base import Base
# Import all model modules so they register on Base.metadata
from app.models.identity import User, Session, Shop, ShopMember
from app.models.etims import EtimsConfig, EtimsSequence, EtimsTransmission
from app.models.client import Client
from app.models.supplier import Supplier
from app.models.item import Product
from app.models.inventory import StockLocation, StockLedger
from app.models.account import ChartOfAccount, FiscalYear
from app.models.document import Document, DocumentItem
from app.models.payment import DocumentPayment
from app.models.expense import Expense, Income
from app.models.journal import JournalEntry
from app.models.hr import Employee, ExpenseClaim
from app.models.crm import Deal, DealActivity, Proposal, ProposalItem, ProposalToken, Contract, ContractHoursLog, Project, ProjectMember, Timesheet, ProjectMilestone
from app.models.operations import Budget, CostCenter, BillOfMaterials, BomItem, ProductionOrder, Stocktake, StocktakeItem, LoyaltyProgram, MembershipTier, ClientLoyaltyAccount, LoyaltyLedger
from app.models.governance import AuditLog, Notification


from sqlalchemy import text

MIGRATION_STATEMENTS = [
    # 1. SHOPS TABLE
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS short_name VARCHAR(50);",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS logo_url TEXT;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS primary_color VARCHAR(20) DEFAULT '#000000' NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS plan VARCHAR(30) DEFAULT 'FREE' NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(30) DEFAULT 'ACTIVE' NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS is_lifetime_pro BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS code VARCHAR(10);",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS currency VARCHAR(3) DEFAULT 'KES' NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS phone VARCHAR(30);",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS website VARCHAR(255);",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS email VARCHAR(255);",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS tax_pin VARCHAR(30);",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS is_vat_registered BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS vat_number VARCHAR(50);",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS fiscal_year_start_month INTEGER DEFAULT 1 NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS hide_onboarding BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS is_gl_enabled BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS gl_onboarding_mode BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS tax_regime VARCHAR(30) DEFAULT 'EXEMPT' NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS is_cit_active BOOLEAN DEFAULT TRUE NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS is_tot_active BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS cit_rate NUMERIC(5, 2) DEFAULT 30.00 NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS estimated_annual_profit NUMERIC(15, 2) DEFAULT 0.00 NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS auto_stock_deduction_enabled BOOLEAN DEFAULT TRUE NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS business_mode VARCHAR(30) DEFAULT 'HYBRID' NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS loyalty_engine_mode VARCHAR(30) DEFAULT 'OFF' NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS is_suspended BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS suspended_reason TEXT;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMP;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS subscription_expires_at TIMESTAMP;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS grace_period_ends_at TIMESTAMP;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # 2. USERS TABLE
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan VARCHAR(30) DEFAULT 'FREE' NOT NULL;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(30) DEFAULT 'ACTIVE' NOT NULL;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_expires_at TIMESTAMP;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS grace_period_ends_at TIMESTAMP;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS auto_renew_enabled BOOLEAN DEFAULT TRUE NOT NULL;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS auto_renew_phone VARCHAR(30);",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_renewal_prompt_at TIMESTAMP;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_lifetime_pro BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_super_admin BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # 3. SHOP MEMBERS TABLE
    "ALTER TABLE shop_members ADD COLUMN IF NOT EXISTS role VARCHAR(30) DEFAULT 'OPERATOR' NOT NULL;",
    "ALTER TABLE shop_members ADD COLUMN IF NOT EXISTS custom_permissions TEXT;",
    "ALTER TABLE shop_members ADD COLUMN IF NOT EXISTS assigned_location_ids JSONB;",
    "ALTER TABLE shop_members ADD COLUMN IF NOT EXISTS hide_cost_prices BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE shop_members ADD COLUMN IF NOT EXISTS direct_approval_limit NUMERIC(14, 2) DEFAULT 0.00 NOT NULL;",
    "ALTER TABLE shop_members ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE NOT NULL;",
    "ALTER TABLE shop_members ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # 4. PRODUCTS TABLE
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS item_cls_cd VARCHAR(20) DEFAULT '24101601' NOT NULL;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS pkg_unit_cd VARCHAR(10) DEFAULT 'PCE' NOT NULL;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS item_type VARCHAR(20) DEFAULT 'PRODUCT' NOT NULL;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS cost_price NUMERIC(12, 2) DEFAULT 0.00 NOT NULL;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS default_tax_type VARCHAR(20) DEFAULT 'V_16' NOT NULL;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS track_stock BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS stock_quantity NUMERIC(12, 2) DEFAULT 0.00 NOT NULL;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS reorder_threshold NUMERIC(12, 2) DEFAULT 5.00 NOT NULL;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS default_location_id UUID;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # 5. CLIENTS & SUPPLIERS TABLE
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS client_type VARCHAR(30) DEFAULT 'WALK_IN' NOT NULL;",
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS tax_pin VARCHAR(30);",
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS requires_etims BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS phone VARCHAR(20);",
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS tax_pin VARCHAR(30);",
    "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS requires_etims BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS phone VARCHAR(20);",
    "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # 6. DOCUMENTS TABLE
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS kra_cu_invoice_number VARCHAR(100);",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS parent_document_id UUID;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS requires_etims BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_recurring BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS recurring_interval VARCHAR(20);",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS next_recurring_date TIMESTAMP;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS last_reminder_sent_at TIMESTAMP;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS location_id UUID;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS exchange_rate NUMERIC(12, 4) DEFAULT 1.0000 NOT NULL;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS base_currency VARCHAR(3);",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS base_grand_total NUMERIC(12, 2);",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS notes TEXT;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS terms_and_conditions TEXT;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS payment_channel VARCHAR(50);",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS payment_reference VARCHAR(100);",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS attachment_url TEXT;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS attachment_name VARCHAR(255);",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS attachment_size INTEGER;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_read_by_recipient BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS client_portal_response VARCHAR(50);",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS client_amendment_notes TEXT;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS email_delivery_status VARCHAR(50) DEFAULT 'NOT_SENT' NOT NULL;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS resend_email_id VARCHAR(100);",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS last_email_sent_at TIMESTAMP;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS last_email_opened_at TIMESTAMP;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS loyalty_points_earned INTEGER DEFAULT 0 NOT NULL;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS loyalty_points_redeemed INTEGER DEFAULT 0 NOT NULL;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS loyalty_discount_amount NUMERIC(12, 2) DEFAULT 0.00 NOT NULL;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS due_date TIMESTAMP;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # 7. DOCUMENT ITEMS TABLE
    "ALTER TABLE document_items ADD COLUMN IF NOT EXISTS tax_type VARCHAR(20) DEFAULT 'V_16' NOT NULL;",
    "ALTER TABLE document_items ADD COLUMN IF NOT EXISTS tax_amount NUMERIC(12, 2) DEFAULT 0.00 NOT NULL;",
    "ALTER TABLE document_items ADD COLUMN IF NOT EXISTS item_total NUMERIC(12, 2) DEFAULT 0.00 NOT NULL;",
    "ALTER TABLE document_items ADD COLUMN IF NOT EXISTS notes TEXT;",

    # 8. DOCUMENT PAYMENTS TABLE
    "ALTER TABLE document_payments ADD COLUMN IF NOT EXISTS payment_channel VARCHAR(50) DEFAULT 'BANK' NOT NULL;",
    "ALTER TABLE document_payments ADD COLUMN IF NOT EXISTS payment_reference VARCHAR(100);",
    "ALTER TABLE document_payments ADD COLUMN IF NOT EXISTS notes TEXT;",
    "ALTER TABLE document_payments ADD COLUMN IF NOT EXISTS recorded_by_user_id UUID;",
    "ALTER TABLE document_payments ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # 9. EXPENSES & INCOMES TABLE
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS receipt_url TEXT;",
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS is_non_deductible BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS payment_channel VARCHAR(50);",
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS payment_reference VARCHAR(100);",
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    "ALTER TABLE incomes ADD COLUMN IF NOT EXISTS attachment_url TEXT;",
    "ALTER TABLE incomes ADD COLUMN IF NOT EXISTS payment_channel VARCHAR(50);",
    "ALTER TABLE incomes ADD COLUMN IF NOT EXISTS payment_reference VARCHAR(100);",
    "ALTER TABLE incomes ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # 10. JOURNAL ENTRIES TABLE
    "ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS cost_center_id UUID;",
    "ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS reference_number VARCHAR(100);",
    "ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS is_backdated BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS backdated_reason TEXT;",
    "ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # 11. EMPLOYEES & EXPENSE CLAIMS TABLE
    "ALTER TABLE employees ADD COLUMN IF NOT EXISTS user_id UUID;",
    "ALTER TABLE employees ADD COLUMN IF NOT EXISTS nhif_number VARCHAR(50);",
    "ALTER TABLE employees ADD COLUMN IF NOT EXISTS nssf_number VARCHAR(50);",
    "ALTER TABLE employees ADD COLUMN IF NOT EXISTS base_salary NUMERIC(14, 2) DEFAULT 0.00 NOT NULL;",
    "ALTER TABLE employees ADD COLUMN IF NOT EXISTS commission_rate NUMERIC(5, 2) DEFAULT 0.00 NOT NULL;",
    "ALTER TABLE employees ADD COLUMN IF NOT EXISTS department VARCHAR(100);",
    "ALTER TABLE employees ADD COLUMN IF NOT EXISTS designation VARCHAR(100);",
    "ALTER TABLE employees ADD COLUMN IF NOT EXISTS employment_type VARCHAR(50) DEFAULT 'FULL_TIME' NOT NULL;",
    "ALTER TABLE employees ADD COLUMN IF NOT EXISTS bank_name VARCHAR(100);",
    "ALTER TABLE employees ADD COLUMN IF NOT EXISTS bank_account_number VARCHAR(100);",
    "ALTER TABLE employees ADD COLUMN IF NOT EXISTS bank_branch VARCHAR(100);",
    "ALTER TABLE employees ADD COLUMN IF NOT EXISTS mpesa_phone VARCHAR(50);",
    "ALTER TABLE employees ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE NOT NULL;",
    "ALTER TABLE employees ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    "ALTER TABLE expense_claims ADD COLUMN IF NOT EXISTS claim_number VARCHAR(50);",
    "ALTER TABLE expense_claims ADD COLUMN IF NOT EXISTS category VARCHAR(50) DEFAULT 'OTHER' NOT NULL;",
    "ALTER TABLE expense_claims ADD COLUMN IF NOT EXISTS cost_center_id UUID;",
    "ALTER TABLE expense_claims ADD COLUMN IF NOT EXISTS receipt_url TEXT;",
    "ALTER TABLE expense_claims ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'DRAFT' NOT NULL;",
    "ALTER TABLE expense_claims ADD COLUMN IF NOT EXISTS approved_by_id UUID;",
    "ALTER TABLE expense_claims ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP;",
    "ALTER TABLE expense_claims ADD COLUMN IF NOT EXISTS approval_notes TEXT;",
    "ALTER TABLE expense_claims ADD COLUMN IF NOT EXISTS disbursed_expense_id UUID;",
    "ALTER TABLE expense_claims ADD COLUMN IF NOT EXISTS disbursed_at TIMESTAMP;",
    "ALTER TABLE expense_claims ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",
]


async def init_models():
    print("Connecting to database and creating tables if not exists...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in MIGRATION_STATEMENTS:
            try:
                await conn.execute(text(stmt))
            except Exception as e:
                print(f"[Schema Sync Warning] {stmt}: {e}")
    print("Database tables and columns initialized successfully.")


if __name__ == "__main__":
    asyncio.run(init_models())
