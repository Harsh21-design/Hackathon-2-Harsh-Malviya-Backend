from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from typing import Optional
import uuid

from app.core.database import get_db
from app.core.security import verify_jwt, get_current_user_id
from app.models import Client, Invoice
from app.schemas import ClientCreate, ClientUpdate, ClientOut, ClientWithStats
from app.services.activity_logger import log_event

router = APIRouter(prefix="/api/v1/clients", tags=["Clients"])


@router.get("", response_model=list[ClientWithStats])
async def list_clients(
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    user_id = get_current_user_id(payload)
    result = await db.execute(
        select(Client)
        .where(Client.owner_id == uuid.UUID(user_id))
        .order_by(Client.name)
    )
    clients = result.scalars().all()

    # Enrich with invoice stats
    out = []
    for c in clients:
        inv_result = await db.execute(
            select(Invoice).where(
                Invoice.client_id == c.id,
                Invoice.owner_id == uuid.UUID(user_id),
            )
        )
        invoices = inv_result.scalars().all()
        total = sum(i.amount for i in invoices)
        outstanding = sum(
            i.amount for i in invoices if i.status not in ("PAID", "CANCELLED")
        )
        out.append(
            ClientWithStats(
                **ClientOut.model_validate(c).model_dump(),
                invoice_count=len(invoices),
                total_invoiced=total,
                outstanding_amount=outstanding,
            )
        )
    return out


@router.get("/{client_id}", response_model=ClientWithStats)
async def get_client(
    client_id: str,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    user_id = get_current_user_id(payload)
    client = await _get_client_or_404(client_id, user_id, db)

    inv_result = await db.execute(
        select(Invoice).where(
            Invoice.client_id == client.id,
            Invoice.owner_id == uuid.UUID(user_id),
        )
    )
    invoices = inv_result.scalars().all()
    total = sum(i.amount for i in invoices)
    outstanding = sum(
        i.amount for i in invoices if i.status not in ("PAID", "CANCELLED")
    )
    return ClientWithStats(
        **ClientOut.model_validate(client).model_dump(),
        invoice_count=len(invoices),
        total_invoiced=total,
        outstanding_amount=outstanding,
    )


@router.post("", response_model=ClientOut, status_code=201)
async def create_client(
    data: ClientCreate,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    user_id = get_current_user_id(payload)
    client = Client(
        owner_id=uuid.UUID(user_id),
        name=data.name,
        email=data.email,
        company=data.company,
        phone=data.phone,
        notes=data.notes,
    )
    db.add(client)
    await db.flush()
    await log_event(db, user_id, "CLIENT_CREATED", f"Client '{client.name}' created.", client_id=str(client.id))
    await db.commit()
    await db.refresh(client)
    return client


@router.patch("/{client_id}", response_model=ClientOut)
async def update_client(
    client_id: str,
    data: ClientUpdate,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    user_id = get_current_user_id(payload)
    client = await _get_client_or_404(client_id, user_id, db)

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(client, field, value)

    await db.commit()
    await db.refresh(client)
    return client


@router.delete("/{client_id}", status_code=204)
async def delete_client(
    client_id: str,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    user_id = get_current_user_id(payload)
    client = await _get_client_or_404(client_id, user_id, db)

    # Check no active invoices
    inv_result = await db.execute(
        select(Invoice).where(
            Invoice.client_id == client.id,
            Invoice.status.notin_(["PAID", "CANCELLED"]),
        )
    )
    if inv_result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="Cannot delete client with active invoices. Cancel or resolve invoices first.",
        )

    await db.delete(client)
    await db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_client_or_404(client_id: str, user_id: str, db: AsyncSession) -> Client:
    try:
        cid = uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid client ID format.")

    result = await db.execute(
        select(Client).where(
            Client.id == cid,
            Client.owner_id == uuid.UUID(user_id),
        )
    )
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")
    return client
