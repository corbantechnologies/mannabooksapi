import uuid
from decimal import Decimal
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc
from app.models.item import Product
from app.models.inventory import StockLedger
from app.modules.items.schemas import ItemCreateRequest, ItemUpdateRequest, StockAdjustmentRequest


def resolve_kra_classification(item_type: str, item_cls_cd: Optional[str]) -> str:
    """Enforce agreed KRA default classifications: 24101601 for Goods, 80141600 for Services."""
    if item_cls_cd and item_cls_cd.strip():
        return item_cls_cd.strip()
    return "80141600" if item_type == "SERVICE" else "24101601"


async def list_items(
    db: AsyncSession,
    shop_id: uuid.UUID,
    item_type: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Product]:
    q = select(Product).where(Product.shop_id == shop_id)
    if item_type:
        q = q.where(Product.item_type == item_type)
    if search:
        s = f"%{search.strip()}%"
        q = q.where(or_(Product.name.ilike(s), Product.sku.ilike(s), Product.item_cls_cd.ilike(s)))
    q = q.order_by(desc(Product.created_at)).offset(offset).limit(limit)
    res = await db.execute(q)
    return list(res.scalars().all())


async def get_item_by_id(db: AsyncSession, shop_id: uuid.UUID, item_id: uuid.UUID) -> Optional[Product]:
    q = select(Product).where(and_(Product.id == item_id, Product.shop_id == shop_id))
    res = await db.execute(q)
    return res.scalar_one_or_none()


async def create_item(db: AsyncSession, shop_id: uuid.UUID, req: ItemCreateRequest) -> Product:
    cls_cd = resolve_kra_classification(req.item_type, req.item_cls_cd)
    item = Product(
        shop_id=shop_id,
        name=req.name.strip(),
        sku=req.sku.strip() if req.sku else None,
        item_type=req.item_type,
        unit_price=req.unit_price,
        cost_price=req.cost_price,
        default_tax_type=req.default_tax_type,
        track_stock=req.track_stock,
        stock_quantity=req.stock_quantity if req.track_stock else Decimal("0.00"),
        reorder_threshold=req.reorder_threshold,
        default_location_id=req.default_location_id,
        item_cls_cd=cls_cd,
        pkg_unit_cd=req.pkg_unit_cd.upper().strip() if req.pkg_unit_cd else "PCE",
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def update_item(
    db: AsyncSession, shop_id: uuid.UUID, item_id: uuid.UUID, req: ItemUpdateRequest
) -> Optional[Product]:
    item = await get_item_by_id(db, shop_id, item_id)
    if not item:
        return None

    if req.name is not None:
        item.name = req.name.strip()
    if req.sku is not None:
        item.sku = req.sku.strip() if req.sku else None
    if req.item_type is not None:
        item.item_type = req.item_type
    if req.unit_price is not None:
        item.unit_price = req.unit_price
    if req.cost_price is not None:
        item.cost_price = req.cost_price
    if req.default_tax_type is not None:
        item.default_tax_type = req.default_tax_type
    if req.track_stock is not None:
        item.track_stock = req.track_stock
    if req.reorder_threshold is not None:
        item.reorder_threshold = req.reorder_threshold
    if req.default_location_id is not None:
        item.default_location_id = req.default_location_id
    if req.item_cls_cd is not None:
        item.item_cls_cd = req.item_cls_cd.strip()
    if req.pkg_unit_cd is not None:
        item.pkg_unit_cd = req.pkg_unit_cd.upper().strip()

    await db.commit()
    await db.refresh(item)
    return item


async def record_stock_adjustment(
    db: AsyncSession, shop_id: uuid.UUID, req: StockAdjustmentRequest, user_id: Optional[uuid.UUID] = None
) -> StockLedger:
    """Row-locked atomic stock update and immutable audit ledger creation."""
    q_item = (
        select(Product)
        .where(and_(Product.id == req.product_id, Product.shop_id == shop_id))
        .with_for_update()
    )
    res_item = await db.execute(q_item)
    item = res_item.scalar_one_or_none()
    if not item:
        raise ValueError("Product not found")

    # Positive adjustments increment; negative decrements
    is_inflow = req.movement_type in ("ADJUSTMENT_IN", "RETURN", "PURCHASE_RECEIPT")
    new_balance = item.stock_quantity + req.quantity if is_inflow else item.stock_quantity - req.quantity
    item.stock_quantity = new_balance

    ledger = StockLedger(
        shop_id=shop_id,
        product_id=req.product_id,
        location_id=req.location_id,
        movement_type=req.movement_type,
        quantity=req.quantity,
        unit_cost=req.unit_cost or item.cost_price,
        running_balance=new_balance,
        adjustment_reason=req.adjustment_reason,
        notes=req.notes,
        created_by_id=user_id,
    )
    db.add(ledger)
    await db.commit()
    await db.refresh(ledger)
    return ledger
