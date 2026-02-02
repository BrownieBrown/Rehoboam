"""Authentication routes"""

import os

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from api.auth import create_access_token
from api.dependencies import get_api_for_user, run_sync
from api.models import Token, UserInfo

router = APIRouter()


class LoginRequest(BaseModel):
    """Login credentials"""

    email: str
    password: str


@router.post("/login", response_model=Token)
async def login(credentials: LoginRequest):
    """Login with Kickbase credentials and get JWT token"""
    try:
        # Try to authenticate with Kickbase
        api = await run_sync(get_api_for_user, credentials.email, credentials.password)

        # Get league info
        leagues = await run_sync(api.get_leagues)
        if not leagues:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No leagues found for this account",
            )

        # Use first league (could be extended to allow selection)
        league = leagues[0]

        # Create JWT token
        token, expires_at = create_access_token(
            email=credentials.email,
            league_id=league.id,
        )

        return Token(
            access_token=token,
            expires_at=expires_at,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {str(e)}",
        ) from e


@router.get("/me", response_model=UserInfo)
async def get_current_user_info(
    email: str = None,
    league_id: str = None,
):
    """Get current user info - requires valid session"""
    # For now, use env credentials
    # In production, this would use the JWT token
    email = email or os.getenv("KICKBASE_EMAIL")
    password = os.getenv("KICKBASE_PASSWORD")

    if not email or not password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        api = await run_sync(get_api_for_user, email, password)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No leagues found",
            )

        league = leagues[0]
        budget_info = await run_sync(api.get_budget, league.id)
        squad = await run_sync(api.get_squad, league.id)

        team_value = sum(p.market_value for p in squad)

        return UserInfo(
            email=email,
            league_id=league.id,
            league_name=league.name,
            team_name=budget_info.get("teamName", "My Team"),
            budget=budget_info.get("budget", 0),
            team_value=team_value,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get user info: {str(e)}",
        ) from e
