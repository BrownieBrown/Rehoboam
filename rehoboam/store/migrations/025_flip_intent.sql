-- Why a player was bought, next to what was paid (2026-09-24).
--
-- The bot knows a purchase is a profit flip at the moment it bids and then
-- dropped that knowledge one step later: the pending bid carried tier=NULL,
-- the purchase record had no column for it, and the sell loop had to infer
-- intent from the squad shape every session — which fails whenever the squad
-- is short (nine players are the whole best eleven, so nothing was ever
-- sellable). The pending bid is what becomes the purchase record when the
-- auction is won, so both rows carry the intent.
--
--   intent         'flip' (bought to resell) | 'points' (bought for the eleven)
--                  | NULL (cost basis recovered from the transfer feed; unknown)
--   target_pct     the flip's expected appreciation at purchase, for reporting
--   max_hold_days  the flip's recommended hold, for reporting — never a trigger

alter table rehoboam.pending_bids
    add column if not exists intent text,
    add column if not exists target_pct double precision,
    add column if not exists max_hold_days integer;

alter table rehoboam.tracked_purchases
    add column if not exists intent text,
    add column if not exists target_pct double precision,
    add column if not exists max_hold_days integer;

create index if not exists idx_tracked_purchases_intent
    on rehoboam.tracked_purchases (intent);
