import uuid
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc
from app.models.supplier import Supplier
from app.modules.suppliers.schemas import SupplierCreateRequest, SupplierUpdateRequest


async def list_suppliers(
    db: AsyncSession,
    shop_id: uuid.UUID,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Supplier]:
    q = select(Supplier).where(Supplier.shop_id == shop_id)
    if search:
        s = f"%{search.strip()}%"
        q = q.where(or_(Supplier.name.ilike(s), Supplier.email.ilike(s), Supplier.tax_pin.ilike(s)))
    q = q.order_by(desc(Supplier.created_at)).offset(offset).limit(limit)
    res = await db.execute(q)
    return list(res.scalars().all())


async def get_supplier_by_id(db: AsyncSession, shop_id: uuid.UUID, supplier_id: uuid.UUID) -> Optional[Supplier]:
    q = select(Supplier).where(and_(Supplier.id == supplier_id, Supplier.shop_id == shop_id))
    res = await db.execute(q)
    return res.scalar_one_or_none()


async def create_supplier(db: AsyncSession, shop_id: uuid.UUID, req: SupplierCreateRequest) -> Supplier:
    supplier = Supplier(
        shop_id=shop_id,
        name=req.name.strip(),
        email=req.email.lower().strip(),
        phone=req.phone.strip() if req.phone else None,
        supplier_type=req.supplier_type,
        tax_pin=req.tax_pin.upper().strip() if req.tax_pin else None,
        requires_etims=req.requires_etims or bool(req.tax_pin),
        payment_terms=req.payment_terms or "NET_30",
    )
    db.add(supplier)
    await db.commit()
    await db.refresh(supplier)
    return supplier


async def update_supplier(
    db: AsyncSession, shop_id: uuid.UUID, supplier_id: uuid.UUID, req: SupplierUpdateRequest
) -> Optional[Supplier]:
    supplier = await get_supplier_by_id(db, shop_id, supplier_id)
    if not supplier:
        return None

    if req.name is not None:
        supplier.name = req.name.strip()
    if req.email is not None:
        supplier.email = req.email.lower().strip()
    if req.phone is not None:
        supplier.phone = req.phone.strip() if req.phone else None
    if req.supplier_type is not None:
        supplier.supplier_type = req.supplier_type
    if req.tax_pin is not None:
        supplier.tax_pin = req.tax_pin.upper().strip() if req.tax_pin else None
    if req.requires_etims is not None:
        supplier.requires_etims = req.requires_etims
    if req.payment_terms is not None:
        supplier.payment_terms = req.payment_terms

    await db.commit()
    await db.refresh(supplier)
    return supplier
