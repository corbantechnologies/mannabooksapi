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


from sqlalchemy import text

MIGRATION_STATEMENTS = [
    # shops table schema alignment
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS primary_color VARCHAR(20) DEFAULT '#000000' NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS plan VARCHAR(30) DEFAULT 'FREE' NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(30) DEFAULT 'ACTIVE' NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS is_lifetime_pro BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",

    # documents table recurring enhancements
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_recurring BOOLEAN DEFAULT FALSE NOT NULL;",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS recurring_interval VARCHAR(20);",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS next_recurring_date TIMESTAMP;",

    # users table schema alignment
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan VARCHAR(30) DEFAULT 'FREE' NOT NULL;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(30) DEFAULT 'ACTIVE' NOT NULL;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_expires_at TIMESTAMP;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;",
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
