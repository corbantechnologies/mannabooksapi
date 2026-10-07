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


async def init_models():
    print("Connecting to database and creating tables if not exists...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Database tables initialized successfully.")


if __name__ == "__main__":
    asyncio.run(init_models())
