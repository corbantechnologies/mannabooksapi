import uuid
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc
from app.models.client import Client
from app.modules.clients.schemas import ClientCreateRequest, ClientUpdateRequest


async def list_clients(
    db: AsyncSession,
    shop_id: uuid.UUID,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Client]:
    q = select(Client).where(Client.shop_id == shop_id)
    if search:
        s = f"%{search.strip()}%"
        q = q.where(or_(Client.name.ilike(s), Client.email.ilike(s), Client.tax_pin.ilike(s)))
    q = q.order_by(desc(Client.created_at)).offset(offset).limit(limit)
    res = await db.execute(q)
    return list(res.scalars().all())


async def get_client_by_id(db: AsyncSession, shop_id: uuid.UUID, client_id: uuid.UUID) -> Optional[Client]:
    q = select(Client).where(and_(Client.id == client_id, Client.shop_id == shop_id))
    res = await db.execute(q)
    return res.scalar_one_or_none()


async def create_client(db: AsyncSession, shop_id: uuid.UUID, req: ClientCreateRequest) -> Client:
    client = Client(
        shop_id=shop_id,
        name=req.name.strip(),
        email=req.email.lower().strip() if req.email else "",
        phone=req.phone.strip() if req.phone else None,
        client_type=req.client_type,
        tax_pin=req.tax_pin.upper().strip() if req.tax_pin else None,
        requires_etims=req.requires_etims or bool(req.tax_pin),
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return client


async def update_client(
    db: AsyncSession, shop_id: uuid.UUID, client_id: uuid.UUID, req: ClientUpdateRequest
) -> Optional[Client]:
    client = await get_client_by_id(db, shop_id, client_id)
    if not client:
        return None

    if req.name is not None:
        client.name = req.name.strip()
    if req.email is not None:
        client.email = req.email.lower().strip() if req.email else ""
    if req.phone is not None:
        client.phone = req.phone.strip() if req.phone else None
    if req.client_type is not None:
        client.client_type = req.client_type
    if req.tax_pin is not None:
        client.tax_pin = req.tax_pin.upper().strip() if req.tax_pin else None
    if req.requires_etims is not None:
        client.requires_etims = req.requires_etims

    await db.commit()
    await db.refresh(client)
    return client
