"""Trading routes"""

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import TokenData, get_current_user
from api.dependencies import get_cached_api, run_sync
from api.models import BidRequest, BidResponse, SellRequest, SellResponse
from rehoboam.analyzer import MarketAnalyzer
from rehoboam.bidding_strategy import SmartBidding
from rehoboam.config import get_settings

router = APIRouter()


@router.post("/bid", response_model=BidResponse)
async def place_bid(
    request: BidRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """Place a bid on a market player"""
    try:
        api = get_cached_api(current_user.email)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]

        # Get player info
        player_info = await run_sync(api.get_player_info, league, request.player_id)
        if not player_info:
            raise HTTPException(status_code=404, detail="Player not found")

        player_name = f"{player_info.first_name} {player_info.last_name}".strip()

        # Check budget
        budget_info = await run_sync(api.get_team_info, league)
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
                await run_sync(api.buy_player, league, player_info, request.amount)
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
async def list_for_sale(
    request: SellRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """List a player for sale"""
    try:
        api = get_cached_api(current_user.email)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]

        # Get player info
        player_info = await run_sync(api.get_player_info, league, request.player_id)
        if not player_info:
            raise HTTPException(status_code=404, detail="Player not found")

        player_name = f"{player_info.first_name} {player_info.last_name}".strip()

        if request.live:
            # Execute real sale listing
            try:
                await run_sync(api.sell_player, league, player_info, request.price)
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
async def get_active_auctions(current_user: TokenData = Depends(get_current_user)):
    """Get active auctions the user is participating in"""
    try:
        api = get_cached_api(current_user.email)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]

        # Get market players (auctions)
        market_players = await run_sync(api.get_market, league)

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
async def get_suggested_bid(
    player_id: str,
    current_price: int = Query(None, description="Current market price if known"),
    current_user: TokenData = Depends(get_current_user),
):
    """Get suggested bid amount for a player"""
    try:
        api = get_cached_api(current_user.email)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]

        # Get player info
        player_info = await run_sync(api.get_player_info, league, player_id)
        if not player_info:
            raise HTTPException(status_code=404, detail="Player not found")

        # Set the price from query param if player doesn't have it (get_player_details doesn't return price)
        if current_price and current_price > 0 and player_info.price == 0:
            player_info.price = current_price

        # Analyze player for smart bidding
        settings = get_settings()
        analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=settings.min_buy_value_increase_pct,
            min_sell_profit_pct=settings.min_sell_profit_pct,
            max_loss_pct=settings.max_loss_pct,
            min_value_score_to_buy=settings.min_value_score_to_buy,
        )
        analysis = analyzer.analyze_market_player(player_info)

        # Calculate suggested bid with real values
        strategy = SmartBidding()

        # Use provided price, or player's price attribute, or fall back to market value
        if current_price and current_price > 0:
            base_bid = current_price
        elif hasattr(player_info, "price") and player_info.price > 0:
            base_bid = player_info.price
        else:
            base_bid = player_info.market_value
        recommendation = strategy.calculate_bid(
            asking_price=base_bid,
            market_value=player_info.market_value,
            value_score=analysis.value_score,
            confidence=analysis.confidence,
        )

        return {
            "player_id": player_id,
            "player_name": f"{player_info.first_name} {player_info.last_name}".strip(),
            "current_price": base_bid,
            "market_value": player_info.market_value,
            "value_score": analysis.value_score,
            "recommendation": analysis.recommendation,
            "suggested_bid": recommendation.recommended_bid,
            "min_bid": int(base_bid * 1.01),  # 1% above current
            "max_bid": recommendation.max_profitable_bid,
            "reasoning": recommendation.reasoning,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to calculate bid: {str(e)}") from e
