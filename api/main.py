"""FastAPI application for Rehoboam Web Dashboard"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import analytics, auth, market, portfolio, settings, trading

# Get CORS origins from environment
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")

app = FastAPI(
    title="Rehoboam API",
    description="KICKBASE Trading Bot API",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(market.router, prefix="/api/market", tags=["Market"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["Portfolio"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["Analytics"])
app.include_router(trading.router, prefix="/api/trading", tags=["Trading"])
app.include_router(settings.router, prefix="/api/settings", tags=["Settings"])


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "rehoboam-api"}


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "Rehoboam API",
        "version": "1.0.0",
        "docs": "/api/docs",
    }
