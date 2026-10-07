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
