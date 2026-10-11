import pytest
from sqlalchemy import select
from app.db.session import engine
from app.models.hr import Employee, ExpenseClaim
from app.models.payment import PaymentMethod, DocumentPayment
from app.models.identity import User, Shop, ShopTerm, ShopMember, ShopCurrency
from app.models.document import Document, DocumentItem, DocumentToken, DocumentNote
from app.models.account import ChartOfAccount, FiscalYear, AccountingPeriod
from app.models.journal import JournalEntry
from app.models.crm import (
    Deal, DealActivity, Proposal, ProposalItem, ProposalToken,
    Contract, ContractHoursLog, Project, ProjectMember, Timesheet, ProjectMilestone
)
from app.models.operations import (
    Budget, CostCenter, BillOfMaterials, BomItem, ProductionOrder,
    Stocktake, StocktakeItem, LoyaltyProgram, MembershipTier,
    ClientLoyaltyAccount, LoyaltyLedger, FixedAsset, TaxInstalment, WhtPayment
)
from app.models.client import Client
from app.models.supplier import Supplier
from app.models.item import Product
from app.models.inventory import StockLocation
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_all_phase1_models_queryable():
    """Verify all realigned SQLAlchemy models can execute SELECT queries without UndefinedColumnError or DatatypeMismatchError."""
    models_to_test = [
        Employee,
        ExpenseClaim,
        PaymentMethod,
        DocumentPayment,
        User,
        Shop,
        ShopTerm,
        ShopMember,
        ShopCurrency,
        Document,
        DocumentItem,
        DocumentToken,
        DocumentNote,
        ChartOfAccount,
        FiscalYear,
        AccountingPeriod,
        JournalEntry,
        Deal,
        DealActivity,
        Proposal,
        ProposalItem,
        ProposalToken,
        Contract,
        ContractHoursLog,
        Project,
        ProjectMember,
        Timesheet,
        ProjectMilestone,
        Budget,
        CostCenter,
        BillOfMaterials,
        BomItem,
        ProductionOrder,
        Stocktake,
        StocktakeItem,
        LoyaltyProgram,
        MembershipTier,
        ClientLoyaltyAccount,
        LoyaltyLedger,
        FixedAsset,
        TaxInstalment,
        WhtPayment,
    ]

    async with AsyncSession(engine) as session:
        for model in models_to_test:
            stmt = select(model).limit(1)
            result = await session.execute(stmt)
            # Fetch without error confirms all mapped column names match database
            _ = result.scalars().first()
