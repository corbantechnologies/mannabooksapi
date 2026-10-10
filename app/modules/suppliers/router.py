import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.modules.suppliers.schemas import SupplierCreateRequest, SupplierUpdateRequest, SupplierResponse
from app.modules.suppliers.service import list_suppliers, get_supplier_by_id, create_supplier, update_supplier

router = APIRouter(prefix="/suppliers", tags=["Suppliers Master Data"], dependencies=[Depends(verify_internal_api_key)])


@router.get("", response_model=List[SupplierResponse])
async def list_suppliers_endpoint(
    shop_id: uuid.UUID = Query(...),
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await list_suppliers(db, shop_id=shop_id, search=search, limit=limit, offset=offset)


@router.get("/{supplier_id}", response_model=SupplierResponse)
async def get_supplier_endpoint(
    supplier_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    supplier = await get_supplier_by_id(db, shop_id=shop_id, supplier_id=supplier_id)
    if not supplier:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supplier not found")
    return supplier


@router.post("", response_model=SupplierResponse, status_code=status.HTTP_201_CREATED)
async def create_supplier_endpoint(
    req: SupplierCreateRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    return await create_supplier(db, shop_id=shop_id, req=req)


@router.patch("/{supplier_id}", response_model=SupplierResponse)
async def update_supplier_endpoint(
    supplier_id: uuid.UUID,
    req: SupplierUpdateRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    supplier = await update_supplier(db, shop_id=shop_id, supplier_id=supplier_id, req=req)
    if not supplier:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supplier not found")
    return supplier


@router.delete("/{supplier_id}")
async def delete_supplier_endpoint(
    supplier_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.suppliers.service import delete_supplier
    deleted = await delete_supplier(db, shop_id=shop_id, supplier_id=supplier_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supplier not found")
    return {"success": True, "message": "Supplier deleted successfully"}


@router.get("/{supplier_id}/statement")
async def get_supplier_statement_endpoint(
    supplier_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime
    from app.modules.suppliers.service import get_supplier_statement
    s_dt = datetime.fromisoformat(start_date) if start_date else None
    e_dt = datetime.fromisoformat(end_date) if end_date else None
    res = await get_supplier_statement(db, shop_id=shop_id, supplier_id=supplier_id, start_date=s_dt, end_date=e_dt)
    if not res:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supplier not found")
    return res


