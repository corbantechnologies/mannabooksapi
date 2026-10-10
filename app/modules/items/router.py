import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.modules.items.schemas import (
    ItemCreateRequest,
    ItemUpdateRequest,
    ItemResponse,
    StockAdjustmentRequest,
    StockLedgerResponse,
)
from app.modules.items.service import (
    list_items,
    get_item_by_id,
    create_item,
    update_item,
    record_stock_adjustment,
)

router = APIRouter(prefix="/items", tags=["Items & Inventory Master Data"], dependencies=[Depends(verify_internal_api_key)])


@router.get("", response_model=List[ItemResponse])
async def list_items_endpoint(
    shop_id: uuid.UUID = Query(...),
    item_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await list_items(db, shop_id=shop_id, item_type=item_type, search=search, limit=limit, offset=offset)


@router.get("/{item_id}", response_model=ItemResponse)
async def get_item_endpoint(
    item_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    item = await get_item_by_id(db, shop_id=shop_id, item_id=item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return item


@router.post("", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
async def create_item_endpoint(
    req: ItemCreateRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    return await create_item(db, shop_id=shop_id, req=req)


@router.patch("/{item_id}", response_model=ItemResponse)
async def update_item_endpoint(
    item_id: uuid.UUID,
    req: ItemUpdateRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    item = await update_item(db, shop_id=shop_id, item_id=item_id, req=req)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return item


@router.delete("/{item_id}")
async def delete_item_endpoint(
    item_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.items.service import delete_item
    deleted = await delete_item(db, shop_id=shop_id, item_id=item_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return {"success": True, "message": "Item deleted successfully"}



@router.post("/inventory/adjust", response_model=StockLedgerResponse, status_code=status.HTTP_201_CREATED)
async def adjust_stock_endpoint(
    req: StockAdjustmentRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await record_stock_adjustment(db, shop_id=shop_id, req=req)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/inventory/reconcile")
async def reconcile_inventory_endpoint(
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, and_
    from app.models.item import Product
    from app.models.inventory import StockLocation

    loc_q = select(StockLocation).where(
        and_(StockLocation.shop_id == shop_id, StockLocation.is_active == True)
    )
    loc_res = await db.execute(loc_q)
    loc = loc_res.scalars().first()
    if not loc:
        loc = StockLocation(
            shop_id=shop_id,
            name="Main Store",
            is_default=True,
            is_active=True,
        )
        db.add(loc)
        await db.flush()

    p_q = select(Product).where(Product.shop_id == shop_id)
    p_res = await db.execute(p_q)
    prods = p_res.scalars().all()

    reconciled = 0
    for p in prods:
        if p.track_stock and not p.default_location_id:
            p.default_location_id = loc.id
            reconciled += 1

    await db.commit()
    return {
        "success": True,
        "reconciledCount": reconciled,
        "totalTracked": len(prods),
    }



@router.get("/locations")
async def list_stock_locations_endpoint(
    shop_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if not shop_id:
        return []
    from sqlalchemy import select, desc, asc
    from app.models.inventory import StockLocation

    stmt = select(StockLocation).where(
        StockLocation.shop_id == shop_id,
        StockLocation.is_active == True,
    ).order_by(desc(StockLocation.is_default), asc(StockLocation.name))
    res = await db.execute(stmt)
    locs = res.scalars().all()
    return [
        {
            "id": str(loc.id),
            "shopId": str(loc.shop_id),
            "name": loc.name,
            "address": loc.address,
            "isDefault": bool(loc.is_default),
            "isActive": bool(loc.is_active),
            "createdAt": loc.created_at.isoformat() if loc.created_at else None,
        }
        for loc in locs
    ]

