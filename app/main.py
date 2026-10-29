"""
Main FastAPI Application Entry Point for AI Payment Recovery Agent
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import engine, Base
import app.models  # Ensure all models are registered with Base.metadata

from app.routers import invoices, clients, reminders, profile, activities, webhooks

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ai_payment_recovery")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables on startup
    logger.info("Initializing database schema...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema ready.")
    yield
    # Cleanup
    await engine.dispose()
    logger.info("Database engine disposed.")


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="Intelligent payment recovery agent with automated drafts, eligibility tracking, and multi-channel workflows.",
    lifespan=lifespan,
)

# CORS Middleware to allow Next.js frontend calls
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.FRONTEND_URL,
        "https://hackathon-2-harsh-malviya-frontend.vercel.app",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register Routers
app.include_router(invoices.router)
app.include_router(clients.router)
app.include_router(reminders.router)
app.include_router(profile.router)
app.include_router(activities.router)
app.include_router(webhooks.router)


@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "demo_mode": settings.DEMO_MODE,
        "ai_model": settings.GEMINI_MODEL,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
