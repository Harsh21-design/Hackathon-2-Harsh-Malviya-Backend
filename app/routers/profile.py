from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import verify_jwt, get_current_user_id
from app.models import BusinessProfile
from app.schemas import BusinessProfileCreate, BusinessProfileUpdate, BusinessProfileOut
import uuid

router = APIRouter(prefix="/api/v1/profile", tags=["Profile"])


@router.get("/me", response_model=BusinessProfileOut)
async def get_profile(
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    user_id = get_current_user_id(payload)
    result = await db.execute(
        select(BusinessProfile).where(BusinessProfile.id == uuid.UUID(user_id))
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found. Please complete onboarding.")
    return profile


@router.post("/me", response_model=BusinessProfileOut, status_code=201)
async def create_profile(
    data: BusinessProfileCreate,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    user_id = get_current_user_id(payload)
    existing = await db.execute(
        select(BusinessProfile).where(BusinessProfile.id == uuid.UUID(user_id))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Profile already exists. Use PATCH to update.")

    profile = BusinessProfile(
        id=uuid.UUID(user_id),
        name=data.name,
        email=data.email,
        company=data.company,
        address=data.address,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


@router.patch("/me", response_model=BusinessProfileOut)
async def update_profile(
    data: BusinessProfileUpdate,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    user_id = get_current_user_id(payload)
    result = await db.execute(
        select(BusinessProfile).where(BusinessProfile.id == uuid.UUID(user_id))
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found.")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)

    await db.commit()
    await db.refresh(profile)
    return profile
