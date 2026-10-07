import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.modules.documents.schemas import (
    DocumentCreateRequest,
    CreditNoteCreateRequest,
    DocumentResponse,
    DocumentStatusUpdateRequest,
)
from app.modules.documents.service import (
    list_documents,
    get_document_by_id,
    create_document,
    issue_credit_note,
    update_document_status,
)

router = APIRouter(prefix="/documents", tags=["Documents & Credit Notes"], dependencies=[Depends(verify_internal_api_key)])


@router.get("", response_model=List[DocumentResponse])
async def list_documents_endpoint(
    shop_id: uuid.UUID = Query(...),
    doc_type: Optional[str] = Query(None),
    doc_status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await list_documents(db, shop_id=shop_id, doc_type=doc_type, doc_status=doc_status, limit=limit, offset=offset)


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document_endpoint(
    document_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    doc = await get_document_by_id(db, shop_id=shop_id, document_id=document_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def create_document_endpoint(
    req: DocumentCreateRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    return await create_document(db, shop_id=shop_id, req=req)


@router.post("/{document_id}/credit-note", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def issue_credit_note_endpoint(
    document_id: uuid.UUID,
    req: CreditNoteCreateRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await issue_credit_note(db, shop_id=shop_id, parent_doc_id=document_id, req=req)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.patch("/{document_id}/status", response_model=DocumentResponse)
async def update_status_endpoint(
    document_id: uuid.UUID,
    req: DocumentStatusUpdateRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    doc = await update_document_status(db, shop_id=shop_id, document_id=document_id, new_status=req.status)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc
