import asyncio
from app.db.session import engine
from app.db.base import Base

# Import all model modules so they register on Base.metadata
from app.models.identity import (
    User, Session, Shop, ShopMember, PasswordResetToken,
    ShopTerm, ShopCurrency, BillingTransaction
)
from app.models.etims import EtimsConfig, EtimsSequence, EtimsTransmission
from app.models.client import Client
from app.models.supplier import Supplier
from app.models.item import Product
from app.models.inventory import StockLocation, StockLedger
from app.models.account import ChartOfAccount, FiscalYear
from app.models.document import Document, DocumentItem
from app.models.payment import DocumentPayment, PaymentMethod
from app.models.expense import Expense, Income
from app.models.journal import JournalEntry
from app.models.hr import Employee, ExpenseClaim
from app.models.crm import (
    Deal, DealActivity, Proposal, ProposalItem, ProposalToken,
    Contract, ContractHoursLog, Project, ProjectMember, Timesheet, ProjectMilestone
)
from app.models.operations import (
    Budget, CostCenter, BillOfMaterials, BomItem, ProductionOrder,
    Stocktake, StocktakeItem, LoyaltyProgram, MembershipTier,
    ClientLoyaltyAccount, LoyaltyLedger
)
from app.models.governance import AuditLog, Notification

from sqlalchemy import text


MIGRATION_STATEMENTS = [
    # ============================================================
    # 1. CREATE ALL REQUIRED ENUM TYPES
    # ============================================================
    """
    DO $$ BEGIN
        CREATE TYPE client_type AS ENUM ('WALK_IN', 'INDIVIDUAL', 'CORPORATE');
    EXCEPTION WHEN duplicate_object THEN null; END $$;
    """,
    """
    DO $$ BEGIN
        CREATE TYPE doc_type AS ENUM (
            'QUOTATION', 'INVOICE', 'RECEIPT', 'LPO', 'PO', 'LSO',
            'DELIVERY_NOTE', 'CREDIT_NOTE', 'DEBIT_NOTE',
            'GOODS_RECEIVED_NOTE', 'SERVICE_COMPLETION_NOTE',
            'PAYMENT_VOUCHER', 'PAYROLL_VOUCHER'
        );
    EXCEPTION WHEN duplicate_object THEN null; END $$;
    """,
    """
    DO $$ BEGIN
        CREATE TYPE doc_status AS ENUM (
            'DRAFT', 'ISSUED', 'OVERDUE', 'PAID', 'PARTIALLY_PAID',
            'RECEIVED', 'CANCELLED', 'CONFIRMED'
        );
    EXCEPTION WHEN duplicate_object THEN null; END $$;
    """,
    """
    DO $$ BEGIN
        CREATE TYPE recurring_interval AS ENUM ('WEEKLY', 'MONTHLY', 'QUARTERLY', 'YEARLY');
    EXCEPTION WHEN duplicate_object THEN null; END $$;
    """,
    """
    DO $$ BEGIN
        CREATE TYPE tax_type AS ENUM ('V_16', 'V_0', 'EXEMPT');
    EXCEPTION WHEN duplicate_object THEN null; END $$;
    """,
    """
    DO $$ BEGIN
        CREATE TYPE account_type AS ENUM ('ASSET', 'LIABILITY', 'EQUITY', 'REVENUE', 'EXPENSE');
    EXCEPTION WHEN duplicate_object THEN null; END $$;
    """,
    """
    DO $$ BEGIN
        CREATE TYPE expense_category AS ENUM (
            'RENT', 'UTILITIES', 'FUEL', 'MARKETING', 'SALARIES', 'OFFICE_SUPPLIES', 'OTHER'
        );
    EXCEPTION WHEN duplicate_object THEN null; END $$;
    """,
    """
    DO $$ BEGIN
        CREATE TYPE income_category AS ENUM (
            'INTEREST', 'DIVIDENDS', 'ASSET_SALE', 'REFUNDS', 'COMMISSION',
            'RENTAL_INCOME', 'GRANTS_SUBSIDIES', 'OTHER'
        );
    EXCEPTION WHEN duplicate_object THEN null; END $$;
    """,
    """
    DO $$ BEGIN
        CREATE TYPE journal_source AS ENUM (
            'document', 'expense', 'income', 'payroll', 'manual', 'migrated'
        );
    EXCEPTION WHEN duplicate_object THEN null; END $$;
    """,
    """
    DO $$ BEGIN
        CREATE TYPE loyalty_engine_mode AS ENUM ('OFF', 'POINTS_ONLY', 'TIERS_ONLY', 'HYBRID');
    EXCEPTION WHEN duplicate_object THEN null; END $$;
    """,
    """
    DO $$ BEGIN
        CREATE TYPE user_role AS ENUM (
            'OWNER', 'ADMIN', 'MANAGER', 'ACCOUNTANT', 'STOREKEEPER',
            'CASHIER', 'DISPATCHER', 'SALES_REP', 'EMPLOYEE', 'VIEWER'
        );
    EXCEPTION WHEN duplicate_object THEN null; END $$;
    """,
    """
    DO $$ BEGIN
        CREATE TYPE invitation_status AS ENUM ('PENDING', 'ACCEPTED', 'REVOKED');
    EXCEPTION WHEN duplicate_object THEN null; END $$;
    """,
    """
    DO $$ BEGIN
        CREATE TYPE stock_movement_type AS ENUM (
            'PURCHASE_RECEIPT', 'SALE', 'ADJUSTMENT_IN', 'ADJUSTMENT_OUT',
            'TRANSFER_OUT', 'TRANSFER_IN', 'OPENING_BALANCE', 'RETURN', 'VOID'
        );
    EXCEPTION WHEN duplicate_object THEN null; END $$;
    """,
    """
    DO $$ BEGIN
        CREATE TYPE stock_adjustment_reason AS ENUM (
            'DAMAGED', 'EXPIRED', 'THEFT', 'COUNT_CORRECTION', 'PROMOTION', 'OTHER'
        );
    EXCEPTION WHEN duplicate_object THEN null; END $$;
    """,

    # ============================================================
    # 2. CONVERT EXISTING VARCHAR COLUMNS → ENUM
    # ============================================================

    # clients.client_type
    """
    DO $$ BEGIN
        ALTER TABLE clients ALTER COLUMN client_type DROP DEFAULT;
        ALTER TABLE clients ALTER COLUMN client_type TYPE client_type USING client_type::client_type;
        ALTER TABLE clients ALTER COLUMN client_type SET DEFAULT 'WALK_IN'::client_type;
    EXCEPTION
        WHEN undefined_column THEN
            ALTER TABLE clients ADD COLUMN client_type client_type DEFAULT 'WALK_IN'::client_type NOT NULL;
        WHEN others THEN null;
    END $$;
    """,

    # suppliers.supplier_type (re-uses client_type)
    """
    DO $$ BEGIN
        ALTER TABLE suppliers ALTER COLUMN supplier_type DROP DEFAULT;
        ALTER TABLE suppliers ALTER COLUMN supplier_type TYPE client_type USING supplier_type::client_type;
        ALTER TABLE suppliers ALTER COLUMN supplier_type SET DEFAULT 'CORPORATE'::client_type;
    EXCEPTION
        WHEN undefined_column THEN
            ALTER TABLE suppliers ADD COLUMN supplier_type client_type DEFAULT 'CORPORATE'::client_type NOT NULL;
        WHEN others THEN null;
    END $$;
    """,

    # documents.type → doc_type
    """
    DO $$ BEGIN
        ALTER TABLE documents ALTER COLUMN type DROP DEFAULT;
        ALTER TABLE documents ALTER COLUMN type TYPE doc_type USING type::doc_type;
    EXCEPTION
        WHEN undefined_column THEN
            ALTER TABLE documents ADD COLUMN type doc_type NOT NULL;
        WHEN others THEN null;
    END $$;
    """,

    # documents.status → doc_status
    """
    DO $$ BEGIN
        ALTER TABLE documents ALTER COLUMN status DROP DEFAULT;
        ALTER TABLE documents ALTER COLUMN status TYPE doc_status USING status::doc_status;
        ALTER TABLE documents ALTER COLUMN status SET DEFAULT 'DRAFT'::doc_status;
    EXCEPTION
        WHEN undefined_column THEN
            ALTER TABLE documents ADD COLUMN status doc_status DEFAULT 'DRAFT'::doc_status NOT NULL;
        WHEN others THEN null;
    END $$;
    """,

    # documents.recurring_interval → recurring_interval
    """
    DO $$ BEGIN
        ALTER TABLE documents ALTER COLUMN recurring_interval DROP DEFAULT;
        ALTER TABLE documents ALTER COLUMN recurring_interval TYPE recurring_interval 
            USING recurring_interval::recurring_interval;
    EXCEPTION
        WHEN undefined_column THEN
            ALTER TABLE documents ADD COLUMN recurring_interval recurring_interval;
        WHEN others THEN null;
    END $$;
    """,

    # document_items.tax_type → tax_type
    """
    DO $$ BEGIN
        ALTER TABLE document_items ALTER COLUMN tax_type DROP DEFAULT;
        ALTER TABLE document_items ALTER COLUMN tax_type TYPE tax_type USING tax_type::tax_type;
        ALTER TABLE document_items ALTER COLUMN tax_type SET DEFAULT 'V_16'::tax_type;
    EXCEPTION
        WHEN undefined_column THEN
            ALTER TABLE document_items ADD COLUMN tax_type tax_type DEFAULT 'V_16'::tax_type NOT NULL;
        WHEN others THEN null;
    END $$;
    """,

    # products.default_tax_type → tax_type
    """
    DO $$ BEGIN
        ALTER TABLE products ALTER COLUMN default_tax_type DROP DEFAULT;
        ALTER TABLE products ALTER COLUMN default_tax_type TYPE tax_type USING default_tax_type::tax_type;
        ALTER TABLE products ALTER COLUMN default_tax_type SET DEFAULT 'V_16'::tax_type;
    EXCEPTION
        WHEN undefined_column THEN
            ALTER TABLE products ADD COLUMN default_tax_type tax_type DEFAULT 'V_16'::tax_type NOT NULL;
        WHEN others THEN null;
    END $$;
    """,

    # chart_of_accounts.account_type → account_type
    """
    DO $$ BEGIN
        ALTER TABLE chart_of_accounts ALTER COLUMN account_type DROP DEFAULT;
        ALTER TABLE chart_of_accounts ALTER COLUMN account_type TYPE account_type USING account_type::account_type;
    EXCEPTION
        WHEN undefined_column THEN
            ALTER TABLE chart_of_accounts ADD COLUMN account_type account_type NOT NULL;
        WHEN others THEN null;
    END $$;
    """,

    # expenses.category → expense_category
    """
    DO $$ BEGIN
        ALTER TABLE expenses ALTER COLUMN category DROP DEFAULT;
        ALTER TABLE expenses ALTER COLUMN category TYPE expense_category USING category::expense_category;
        ALTER TABLE expenses ALTER COLUMN category SET DEFAULT 'OTHER'::expense_category;
    EXCEPTION
        WHEN undefined_column THEN
            ALTER TABLE expenses ADD COLUMN category expense_category DEFAULT 'OTHER'::expense_category NOT NULL;
        WHEN others THEN null;
    END $$;
    """,

    # incomes.category → income_category
    """
    DO $$ BEGIN
        ALTER TABLE incomes ALTER COLUMN category DROP DEFAULT;
        ALTER TABLE incomes ALTER COLUMN category TYPE income_category USING category::income_category;
        ALTER TABLE incomes ALTER COLUMN category SET DEFAULT 'OTHER'::income_category;
    EXCEPTION
        WHEN undefined_column THEN
            ALTER TABLE incomes ADD COLUMN category income_category DEFAULT 'OTHER'::income_category NOT NULL;
        WHEN others THEN null;
    END $$;
    """,

    # journal_entries.source_type → journal_source
    """
    DO $$ BEGIN
        ALTER TABLE journal_entries ALTER COLUMN source_type DROP DEFAULT;
        ALTER TABLE journal_entries ALTER COLUMN source_type TYPE journal_source USING source_type::journal_source;
        ALTER TABLE journal_entries ALTER COLUMN source_type SET DEFAULT 'manual'::journal_source;
    EXCEPTION
        WHEN undefined_column THEN
            ALTER TABLE journal_entries ADD COLUMN source_type journal_source DEFAULT 'manual'::journal_source NOT NULL;
        WHEN others THEN null;
    END $$;
    """,

    # shops.loyalty_engine_mode → loyalty_engine_mode
    """
    DO $$ BEGIN
        ALTER TABLE shops ALTER COLUMN loyalty_engine_mode DROP DEFAULT;
        ALTER TABLE shops ALTER COLUMN loyalty_engine_mode TYPE loyalty_engine_mode 
            USING loyalty_engine_mode::loyalty_engine_mode;
        ALTER TABLE shops ALTER COLUMN loyalty_engine_mode SET DEFAULT 'OFF'::loyalty_engine_mode;
    EXCEPTION
        WHEN undefined_column THEN
            ALTER TABLE shops ADD COLUMN loyalty_engine_mode loyalty_engine_mode DEFAULT 'OFF'::loyalty_engine_mode NOT NULL;
        WHEN others THEN null;
    END $$;
    """,

    # shop_members.role → user_role
    """
    DO $$ BEGIN
        ALTER TABLE shop_members ALTER COLUMN role DROP DEFAULT;
        ALTER TABLE shop_members ALTER COLUMN role TYPE user_role USING role::user_role;
        ALTER TABLE shop_members ALTER COLUMN role SET DEFAULT 'EMPLOYEE'::user_role;
    EXCEPTION
        WHEN undefined_column THEN
            ALTER TABLE shop_members ADD COLUMN role user_role DEFAULT 'EMPLOYEE'::user_role NOT NULL;
        WHEN others THEN null;
    END $$;
    """,

    # stock_ledger.movement_type → stock_movement_type
    """
    DO $$ BEGIN
        ALTER TABLE stock_ledger ALTER COLUMN movement_type DROP DEFAULT;
        ALTER TABLE stock_ledger ALTER COLUMN movement_type TYPE stock_movement_type 
            USING movement_type::stock_movement_type;
    EXCEPTION
        WHEN undefined_column THEN
            ALTER TABLE stock_ledger ADD COLUMN movement_type stock_movement_type NOT NULL;
        WHEN others THEN null;
    END $$;
    """,

    # stock_ledger.adjustment_reason → stock_adjustment_reason
    """
    DO $$ BEGIN
        ALTER TABLE stock_ledger ALTER COLUMN adjustment_reason DROP DEFAULT;
        ALTER TABLE stock_ledger ALTER COLUMN adjustment_reason TYPE stock_adjustment_reason 
            USING adjustment_reason::stock_adjustment_reason;
    EXCEPTION
        WHEN undefined_column THEN
            ALTER TABLE stock_ledger ADD COLUMN adjustment_reason stock_adjustment_reason;
        WHEN others THEN null;
    END $$;
    """,

    # ============================================================
    # 3. REGULAR COLUMN ADDITIONS (safe IF NOT EXISTS)
    # ============================================================

    # --- SHOPS ---
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
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS is_suspended BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS suspended_reason TEXT;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMP;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS subscription_expires_at TIMESTAMP;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS grace_period_ends_at TIMESTAMP;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # --- USERS ---
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

    # --- SHOP MEMBERS ---
    "ALTER TABLE shop_members ADD COLUMN IF NOT EXISTS custom_permissions TEXT;",
    "ALTER TABLE shop_members ADD COLUMN IF NOT EXISTS assigned_location_ids JSONB;",
    "ALTER TABLE shop_members ADD COLUMN IF NOT EXISTS hide_cost_prices BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE shop_members ADD COLUMN IF NOT EXISTS direct_approval_limit NUMERIC(14, 2) DEFAULT 0.00 NOT NULL;",
    "ALTER TABLE shop_members ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE NOT NULL;",
    "ALTER TABLE shop_members ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # --- PRODUCTS ---
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS item_cls_cd VARCHAR(20) DEFAULT '24101601' NOT NULL;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS pkg_unit_cd VARCHAR(10) DEFAULT 'PCE' NOT NULL;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS item_type VARCHAR(20) DEFAULT 'PRODUCT' NOT NULL;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS cost_price NUMERIC(12, 2) DEFAULT 0.00 NOT NULL;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS track_stock BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS stock_quantity NUMERIC(12, 2) DEFAULT 0.00 NOT NULL;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS reorder_threshold NUMERIC(12, 2) DEFAULT 5.00 NOT NULL;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS default_location_id UUID;",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # --- CLIENTS ---
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS tax_pin VARCHAR(30);",
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS requires_etims BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS phone VARCHAR(20);",
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # --- SUPPLIERS ---
    "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS tax_pin VARCHAR(30);",
    "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS requires_etims BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS phone VARCHAR(20);",
    "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # --- DOCUMENTS ---
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS kra_cu_invoice_number VARCHAR(100);",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS parent_document_id UUID;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS requires_etims BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_recurring BOOLEAN DEFAULT FALSE NOT NULL;",
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

    # --- DOCUMENT ITEMS ---
    "ALTER TABLE document_items ADD COLUMN IF NOT EXISTS tax_amount NUMERIC(12, 2) DEFAULT 0.00 NOT NULL;",
    "ALTER TABLE document_items ADD COLUMN IF NOT EXISTS item_total NUMERIC(12, 2) DEFAULT 0.00 NOT NULL;",
    "ALTER TABLE document_items ADD COLUMN IF NOT EXISTS notes TEXT;",

    # --- DOCUMENT PAYMENTS ---
    "ALTER TABLE document_payments ADD COLUMN IF NOT EXISTS payment_channel VARCHAR(50) DEFAULT 'BANK' NOT NULL;",
    "ALTER TABLE document_payments ADD COLUMN IF NOT EXISTS payment_reference VARCHAR(100);",
    "ALTER TABLE document_payments ADD COLUMN IF NOT EXISTS notes TEXT;",
    "ALTER TABLE document_payments ADD COLUMN IF NOT EXISTS recorded_by_user_id UUID;",
    "ALTER TABLE document_payments ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # --- EXPENSES ---
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS receipt_url TEXT;",
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS is_non_deductible BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS payment_channel VARCHAR(50);",
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS payment_reference VARCHAR(100);",
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # --- INCOMES ---
    "ALTER TABLE incomes ADD COLUMN IF NOT EXISTS attachment_url TEXT;",
    "ALTER TABLE incomes ADD COLUMN IF NOT EXISTS payment_channel VARCHAR(50);",
    "ALTER TABLE incomes ADD COLUMN IF NOT EXISTS payment_reference VARCHAR(100);",
    "ALTER TABLE incomes ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # --- JOURNAL ENTRIES ---
    "ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS cost_center_id UUID;",
    "ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS reference_number VARCHAR(100);",
    "ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS is_backdated BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS backdated_reason TEXT;",
    "ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # --- EMPLOYEES ---
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

    # --- EXPENSE CLAIMS ---
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
                print(f"[Schema Sync Warning] {stmt[:80]}... : {e}")
    print("Database tables and columns initialized successfully.")


if __name__ == "__main__":
    asyncio.run(init_models())