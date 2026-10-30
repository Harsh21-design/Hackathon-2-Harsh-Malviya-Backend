from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import BusinessProfile

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


# ── Password Hashing Helpers ───────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with 100,000 rounds and 16-byte random salt."""
    salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 100000).hex()
    return f"{salt}${dk}"


def verify_password(stored_hash: Optional[str], password: str) -> bool:
    """Verify password against stored salt$digest."""
    if not stored_hash or "$" not in stored_hash:
        return False
    try:
        salt, expected_dk = stored_hash.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 100000).hex()
        return hmac.compare_digest(dk, expected_dk)
    except Exception:
        return False


# ── Request / Response Schemas ─────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SignupRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    company: Optional[str] = None


class AuthUserResponse(BaseModel):
    id: str
    name: str
    email: str
    company: str
    address: Optional[str] = None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/login", response_model=AuthUserResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate user with database-stored hashed password. Minimum 8 characters required."""
    normalized_email = req.email.strip().lower()
    clean_password = req.password.strip()

    if len(clean_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters.",
        )

    result = await db.execute(
        select(BusinessProfile).where(func.lower(BusinessProfile.email) == normalized_email)
    )
    profile = result.scalar_one_or_none()

    if not profile or not profile.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if not verify_password(profile.password_hash, clean_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    return AuthUserResponse(
        id=str(profile.id),
        name=profile.name,
        email=profile.email,
        company=profile.company or "Independent Studio",
        address=profile.address,
    )


@router.post("/signup", response_model=AuthUserResponse, status_code=status.HTTP_201_CREATED)
async def signup(req: SignupRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user profile with cryptographically hashed password."""
    clean_name = req.name.strip()
    normalized_email = req.email.strip().lower()
    clean_password = req.password.strip()
    clean_company = req.company.strip() if req.company else "Independent Studio"

    if not clean_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name is required.",
        )

    if len(clean_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters.",
        )

    # Check existing user
    result = await db.execute(
        select(BusinessProfile).where(func.lower(BusinessProfile.email) == normalized_email)
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists. Please sign in.",
        )

    new_profile = BusinessProfile(
        id=uuid.uuid4(),
        name=clean_name,
        email=normalized_email,
        company=clean_company,
        password_hash=hash_password(clean_password),
    )
    db.add(new_profile)
    await db.commit()
    await db.refresh(new_profile)

    return AuthUserResponse(
        id=str(new_profile.id),
        name=new_profile.name,
        email=new_profile.email,
        company=new_profile.company or clean_company,
        address=new_profile.address,
    )
