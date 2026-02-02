"""Trading routes"""

import os

from fastapi import APIRouter, HTTPException

from api.dependencies import get_api_for_user, run_sync
from api.models import BidRequest, BidResponse, SellRequest, SellResponse
from rehoboam.bidding_strategy import BiddingStrategy
from rehoboam.config import get_settings

router = APIRouter()


def get_authenticated_api():
    """Get authenticated API using env credentials"""
    email = os.getenv("KICKBASE_EMAIL")
    password = os.getenv("KICKBASE_PASSWORD")
    if not email or not password:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return get_api_for_user(email, password)


@router.post("/bid", response_model=BidResponse)
async def place_bid(request: BidRequest):
    """Place a bid on a market player"""
    try:
        api = await run_sync(get_authenticated_api)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]

        # Get player info
        player_info = await run_sync(api.get_player_info, request.player_id)
        if not player_info:
            raise HTTPException(status_code=404, detail="Player not found")

        player_name = f"{player_info.first_name} {player_info.last_name}".strip()

        # Check budget
        budget_info = await run_sync(api.get_budget, league.id)
        available = budget_info.get("budget", 0)

        if request.amount > available:
            return BidResponse(
                success=False,
                player_id=request.player_id,
                player_name=player_name,
                amount=request.amount,
                message=f"Insufficient budget: {available:,} available, {request.amount:,} needed",
                dry_run=not request.live,
            )

        if request.live:
            # Execute real bid
            try:
                await run_sync(api.buy_player, league.id, request.player_id, request.amount)
                return BidResponse(
                    success=True,
                    player_id=request.player_id,
                    player_name=player_name,
                    amount=request.amount,
                    message="Bid placed successfully",
                    dry_run=False,
                )
            except Exception as e:
                return BidResponse(
                    success=False,
                    player_id=request.player_id,
                    player_name=player_name,
                    amount=request.amount,
                    message=f"Bid failed: {str(e)}",
                    dry_run=False,
                )
        else:
            # Dry run
            return BidResponse(
                success=True,
                player_id=request.player_id,
                player_name=player_name,
                amount=request.amount,
                message="Dry run - bid would be placed",
                dry_run=True,
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to place bid: {str(e)}") from e


@router.post("/sell", response_model=SellResponse)
async def list_for_sale(request: SellRequest):
    """List a player for sale"""
    try:
        api = await run_sync(get_authenticated_api)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]

        # Get player info
        player_info = await run_sync(api.get_player_info, request.player_id)
        if not player_info:
            raise HTTPException(status_code=404, detail="Player not found")

        player_name = f"{player_info.first_name} {player_info.last_name}".strip()

        if request.live:
            # Execute real sale listing
            try:
                await run_sync(api.sell_player, league.id, request.player_id, request.price)
                return SellResponse(
                    success=True,
                    player_id=request.player_id,
                    player_name=player_name,
                    price=request.price,
                    message="Player listed for sale",
                    dry_run=False,
                )
            except Exception as e:
                return SellResponse(
                    success=False,
                    player_id=request.player_id,
                    player_name=player_name,
                    price=request.price,
                    message=f"Sale listing failed: {str(e)}",
                    dry_run=False,
                )
        else:
            # Dry run
            return SellResponse(
                success=True,
                player_id=request.player_id,
                player_name=player_name,
                price=request.price,
                message="Dry run - player would be listed",
                dry_run=True,
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list for sale: {str(e)}") from e


@router.get("/auctions")
async def get_active_auctions():
    """Get active auctions the user is participating in"""
    try:
        api = await run_sync(get_authenticated_api)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]

        # Get market players (auctions)
        market_players = await run_sync(api.get_market, league.id)

        auctions = []
        for player in market_players:
            # Check if this is an auction we're participating in
            if hasattr(player, "my_bid") and player.my_bid:
                auctions.append(
                    {
                        "player_id": player.id,
                        "player_name": f"{player.first_name} {player.last_name}".strip(),
                        "position": player.position,
                        "team_name": player.team_name,
                        "current_price": player.price,
                        "my_bid": player.my_bid,
                        "expiry": (
                            player.expiry.isoformat()
                            if hasattr(player, "expiry") and player.expiry
                            else None
                        ),
                    }
                )

        return {"auctions": auctions}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get auctions: {str(e)}") from e


@router.get("/suggested-bid/{player_id}")
async def get_suggested_bid(player_id: str):
    """Get suggested bid amount for a player"""
    try:
        api = await run_sync(get_authenticated_api)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        _league = leagues[0]  # noqa: F841 - validates league exists

        # Get player info
        player_info = await run_sync(api.get_player_info, player_id)
        if not player_info:
            raise HTTPException(status_code=404, detail="Player not found")

        # Calculate suggested bid
        settings = get_settings()
        strategy = BiddingStrategy(settings)

        base_bid = player_info.price if hasattr(player_info, "price") else player_info.market_value
        suggested = strategy.calculate_bid(base_bid, confidence=0.7)

        return {
            "player_id": player_id,
            "player_name": f"{player_info.first_name} {player_info.last_name}".strip(),
            "current_price": base_bid,
            "market_value": player_info.market_value,
            "suggested_bid": suggested,
            "min_bid": int(base_bid * 1.01),  # 1% above current
            "max_bid": int(base_bid * 1.30),  # 30% above current
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to calculate bid: {str(e)}") from e
