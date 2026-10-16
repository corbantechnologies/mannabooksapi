from datetime import datetime
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.modules.clients.schemas import ClientCreateRequest, ClientUpdateRequest, ClientResponse
from app.modules.clients.service import list_clients, get_client_by_id, create_client, update_client

router = APIRouter(prefix="/clients", tags=["Clients Master Data"], dependencies=[Depends(verify_internal_api_key)])


@router.get("", response_model=List[ClientResponse])
async def list_clients_endpoint(
    shop_id: uuid.UUID = Query(...),
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await list_clients(db, shop_id=shop_id, search=search, limit=limit, offset=offset)


@router.get("/{client_id}", response_model=ClientResponse)
async def get_client_endpoint(
    client_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    client = await get_client_by_id(db, shop_id=shop_id, client_id=client_id)
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    return client


@router.post("", response_model=ClientResponse, status_code=status.HTTP_201_CREATED)
async def create_client_endpoint(
    req: ClientCreateRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    return await create_client(db, shop_id=shop_id, req=req)


@router.patch("/{client_id}", response_model=ClientResponse)
async def update_client_endpoint(
    client_id: uuid.UUID,
    req: ClientUpdateRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    client = await update_client(db, shop_id=shop_id, client_id=client_id, req=req)
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    return client


@router.delete("/{client_id}")
async def delete_client_endpoint(
    client_id: uuid.UUID,
    shop_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from app.models.client import Client
    client = await db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    await db.delete(client)
    await db.commit()
    return {"success": True, "message": "Client deleted successfully"}


@router.get("/{client_id}/statement")
async def get_client_statement_endpoint(
    client_id: uuid.UUID,
    shop_id: Optional[uuid.UUID] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from app.core.datetime_utils import to_naive_utc
    from app.models.client import Client
    from app.modules.clients.service import get_client_statement

    client = await db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    result = await get_client_statement(
        db, shop_id=shop_id or client.shop_id, client_id=client_id,
        start_date=to_naive_utc(start_date), end_date=to_naive_utc(end_date),
    )
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "Client not found"))
    return result

