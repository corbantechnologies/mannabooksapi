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
products_router = APIRouter(prefix="/products", tags=["Products"], dependencies=[Depends(verify_internal_api_key)])


@router.get("", response_model=List[ItemResponse])
@router.get("/products", response_model=List[ItemResponse])
@products_router.get("", response_model=List[ItemResponse])
async def list_items_endpoint(
    shop_id: uuid.UUID = Query(...),
    item_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await list_items(db, shop_id=shop_id, item_type=item_type, search=search, limit=limit, offset=offset)


@router.post("", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
async def create_item_endpoint(
    req: ItemCreateRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    return await create_item(db, shop_id=shop_id, req=req)



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
            "code": loc.code,
            "address": loc.code,
            "isDefault": bool(loc.is_default),
            "isActive": bool(loc.is_active),
            "createdAt": loc.created_at.isoformat() if loc.created_at else None,
        }
        for loc in locs
    ]


@router.post("/locations")
async def create_stock_location_endpoint(
    req: dict,
    db: AsyncSession = Depends(get_db),
):
    from app.models.inventory import StockLocation
    shop_id = uuid.UUID(req["shop_id"]) if isinstance(req.get("shop_id"), str) else req.get("shop_id")
    name = req.get("name") or "New Location"
    code = req.get("code") or req.get("address")
    is_default = bool(req.get("is_default") or req.get("isDefault", False))

    if is_default:
        from sqlalchemy import update
        await db.execute(
            update(StockLocation).where(StockLocation.shop_id == shop_id).values(is_default=False)
        )

    loc = StockLocation(
        shop_id=shop_id,
        name=name,
        code=code,
        is_default=is_default,
        is_active=True,
    )
    db.add(loc)
    await db.commit()
    await db.refresh(loc)
    return {
        "success": True,
        "location": {
            "id": str(loc.id),
            "shopId": str(loc.shop_id),
            "name": loc.name,
            "code": loc.code,
            "isDefault": loc.is_default,
            "isActive": loc.is_active,
            "createdAt": loc.created_at.isoformat(),
        }
    }


@router.patch("/locations/{location_id}")
async def update_stock_location_endpoint(
    location_id: uuid.UUID,
    req: dict,
    db: AsyncSession = Depends(get_db),
):
    from app.models.inventory import StockLocation
    loc = await db.get(StockLocation, location_id)
    if not loc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")

    if "name" in req and req["name"]:
        loc.name = req["name"]
    if "code" in req:
        loc.code = req["code"]
    elif "address" in req:
        loc.code = req["address"]
    if "is_default" in req or "isDefault" in req:
        val = bool(req.get("is_default") if "is_default" in req else req.get("isDefault"))
        if val:
            from sqlalchemy import update
            await db.execute(
                update(StockLocation).where(StockLocation.shop_id == loc.shop_id).values(is_default=False)
            )
        loc.is_default = val
    if "is_active" in req or "isActive" in req:
        loc.is_active = bool(req.get("is_active") if "is_active" in req else req.get("isActive"))

    await db.commit()
    await db.refresh(loc)
    return {
        "success": True,
        "location": {
            "id": str(loc.id),
            "shopId": str(loc.shop_id),
            "name": loc.name,
            "code": loc.code,
            "isDefault": loc.is_default,
            "isActive": loc.is_active,
        }
    }


@router.delete("/locations/{location_id}")
async def delete_stock_location_endpoint(
    location_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.models.inventory import StockLocation
    loc = await db.get(StockLocation, location_id)
    if not loc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    loc.is_active = False
    await db.commit()
    return {"success": True, "message": "Location archived successfully"}


# ==========================================
# STOCK TRANSFERS & MOVEMENTS
# ==========================================

@router.get("/transfers")
async def list_stock_transfers_endpoint(
    shop_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if not shop_id:
        return []
    from sqlalchemy import select, and_, desc
    from app.models.inventory import StockLedger

    stmt = (
        select(StockLedger)
        .where(
            and_(
                StockLedger.shop_id == shop_id,
                StockLedger.movement_type.in_(["TRANSFER_OUT", "TRANSFER_IN"]),
            )
        )
        .order_by(desc(StockLedger.created_at))
        .limit(200)
    )
    res = await db.execute(stmt)
    records = res.scalars().all()

    return [
        {
            "id": str(r.id),
            "shopId": str(r.shop_id),
            "productId": str(r.product_id),
            "productName": r.product.name if r.product else "Item",
            "locationId": str(r.location_id) if r.location_id else None,
            "locationName": r.location.name if r.location else None,
            "quantity": float(r.quantity),
            "unitCost": float(r.unit_cost),
            "type": r.movement_type,
            "notes": r.notes,
            "createdAt": r.created_at.isoformat(),
        }
        for r in records
    ]


@router.post("/transfers")
async def create_stock_transfer_endpoint(
    req: dict,
    db: AsyncSession = Depends(get_db),
):
    from app.models.inventory import StockLedger, StockLocation
    from app.models.item import Product
    from decimal import Decimal

    shop_id = uuid.UUID(req["shop_id"]) if isinstance(req.get("shop_id"), str) else req.get("shop_id")
    src_loc_id = uuid.UUID(req["source_location_id"]) if req.get("source_location_id") else None
    dst_loc_id = uuid.UUID(req["destination_location_id"]) if req.get("destination_location_id") else None
    items = req.get("items", [])
    transfer_uuid = uuid.uuid4()

    for it in items:
        prod_id = uuid.UUID(it["productId"]) if isinstance(it.get("productId"), str) else it.get("productId")
        qty = Decimal(str(it.get("quantity", 1)))
        prod = await db.get(Product, prod_id)
        unit_cost = prod.cost_price if prod else Decimal("0.00")

        # 1. TRANSFER_OUT from source
        out_entry = StockLedger(
            shop_id=shop_id,
            product_id=prod_id,
            location_id=src_loc_id,
            movement_type="TRANSFER_OUT",
            quantity=-abs(qty),
            unit_cost=unit_cost,
            transfer_id=transfer_uuid,
            notes=f"Transfer to destination {dst_loc_id}",
        )
        # 2. TRANSFER_IN to destination
        in_entry = StockLedger(
            shop_id=shop_id,
            product_id=prod_id,
            location_id=dst_loc_id,
            movement_type="TRANSFER_IN",
            quantity=abs(qty),
            unit_cost=unit_cost,
            transfer_id=transfer_uuid,
            notes=f"Transfer from source {src_loc_id}",
        )
        db.add(out_entry)
        db.add(in_entry)

    await db.commit()
    return {
        "success": True,
        "transferId": str(transfer_uuid),
        "status": "COMPLETED",
        "message": f"Transferred {len(items)} items successfully.",
    }


@router.post("/transfers/{transfer_id}/dispatch")
async def dispatch_stock_transfer_endpoint(transfer_id: uuid.UUID):
    # Transfers are posted as COMPLETED when created; a staged dispatch/receive workflow is not implemented.
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Transfer dispatch is not supported yet. Transfers are completed when they are created.",
    )


@router.post("/transfers/{transfer_id}/receive")
async def receive_stock_transfer_endpoint(transfer_id: uuid.UUID):
    # Transfers are posted as COMPLETED when created; a staged dispatch/receive workflow is not implemented.
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Transfer receive is not supported yet. Transfers are completed when they are created.",
    )


@router.post("/transfers/{transfer_id}/cancel")
async def cancel_stock_transfer_endpoint(transfer_id: uuid.UUID):
    # Transfers are posted as COMPLETED when created; a staged dispatch/receive workflow is not implemented.
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Transfer cancel is not supported yet. Transfers are completed when they are created.",
    )


# ==========================================
# ITEM BY ID (WILDCARD MUST BE AT BOTTOM)
# ==========================================

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



