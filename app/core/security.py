from fastapi import HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from app.core.config import get_settings

settings = get_settings()
security = HTTPBearer(auto_error=False)

# Fixed UUID for local development / demo mode
DEMO_USER_ID = "00000000-0000-0000-0000-000000000001"


def verify_jwt(credentials: HTTPAuthorizationCredentials | None = Security(security)) -> dict:
    """
    Validates the Supabase JWT from the Authorization: Bearer <token> header.
    In DEMO_MODE or when no JWT secret is configured, gracefully falls back
    to a default demo user ID so the application can run out-of-the-box.
    """
    if not credentials or not credentials.credentials:
        if settings.DEMO_MODE or not settings.SUPABASE_JWT_SECRET:
            return {"sub": DEMO_USER_ID, "email": "demo@example.com", "role": "authenticated"}
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # If secret is not configured, still permit demo execution
    if not settings.SUPABASE_JWT_SECRET:
        return {"sub": DEMO_USER_ID, "email": "demo@example.com", "role": "authenticated"}

    try:
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        return payload
    except JWTError as exc:
        if settings.DEMO_MODE:
            return {"sub": DEMO_USER_ID, "email": "demo@example.com", "role": "authenticated"}
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_current_user_id(payload: dict) -> str:
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim.",
        )
    return user_id
