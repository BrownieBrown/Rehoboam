# Cold-start data for newly transferred players (REH-41)

**Status**: Design approved, ready for implementation plan
**Author**: Marco Braun (with Claude)
**Date**: 2026-05-17
**Linear**: REH-41

## Problem

At MD1 of every season, the Kickbase player pool includes players the bot can't evaluate:

1. **Returning Bundesliga players** (e.g. Harry Kane) — Kickbase has their full multi-season per-match history, but the current scorer only looks at `performance["it"][0]` (most recent season). At MD1 of 2026/27, `it[0]` is the empty new season; the rich 2025/26 history sits in `it[N-1]`. Result: Kane gets grade F at MD1 and is score-halved, even though we have all the data we need.
1. **Fresh foreign arrivals** — e.g. a striker signed from La Liga. Zero Kickbase history. External data (Understat, OpenLigaDB, ClubElo) can fill the gap.
1. **Genuine unknowables** — e.g. 19-year-old Brazilian signing from a league we don't track. No free data anywhere; honest grade F is correct.

This blocks REH-42 (pre-season elite acquisition) because the bot can't identify which players are Kane-class without evaluating them.

## Goals

- At MD1 of next season (August 2026), every player in the market with prior Bundesliga or Big-5 history gets a non-empty `PlayerScore` graded B or C (not F-halved).
- Cold-start origins (which season, which league) appear in `PlayerScore.notes` for auditability.
- Bot's existing pipeline is unchanged when full current-season Kickbase data exists.
- Failure modes are silent and safe: any cold-start error falls back to grade F, never throws.

## Non-goals

- Predicting Bundesliga performance for players in untracked leagues (Eredivisie, Liga Portugal, Brazilian Série A, etc.). Out of scope per "tier by data availability" decision.
- Forecasting performance for true unknowns (e.g. uncapped 19-year-olds). Grade F is the correct behavior.
- Real-time form tracking from external sources. Refresh cadence is weekly, not session-by-session.

## Acceptance criteria

- [ ] **Phase 1 (small PR)**: Returning Bundesliga player at MD1 of 2026/27 gets a non-empty `PlayerScore` with grade A or B. `PlayerScore.notes` clearly states which season was used as the source. Players genuinely without played-match history still grade F (existing behavior unchanged).
- [ ] **Phase 2 (substantial PR)**: For a fresh foreign arrival (e.g. a Big-5 striker), the bot produces a non-empty `PlayerScore` with grade B or C, derived from Understat aggregates scaled by ClubElo league factor.
- [ ] Both phases: cold-start metadata visible in `PlayerScore.notes` (which source, which season, which league factor applied).
- [ ] Coverage check: `rehoboam check-cold-start-coverage` reports zero ungraded players (grade F count = 0) for any player in the Kickbase market whose prior club was in a covered league (Big-5 or German tier 1/2) in the previous season.
- [ ] All existing scorer tests pass without modification.

## Decisions

| Decision                 | Choice                                 | Rationale                                                                                                                                              |
| ------------------------ | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Unknowable-player policy | Tier by data availability              | Honest grading > fabricated confidence. F-halving is correct response to genuine unknowns.                                                             |
| League coverage          | Big 5 + German tiers                   | Understat covers Big 5 (EPL, La Liga, Serie A, Bundesliga, Ligue 1); OpenLigaDB covers Bundesliga + 2.Bundesliga. ~85% of Bundesliga signings covered. |
| Cache refresh            | Weekly Azure Function                  | Fully automatic, matches existing Azure Function patterns. Sunday 06:00 UTC during the quiet window.                                                   |
| Architecture             | Approach B: parallel cold-start scorer | Honest separation between Kickbase and external data paths. `PlayerScore.notes` audit trail. ~150 LOC of new code.                                     |
| Shipping                 | Two PRs                                | PR1 (Phase 1 scorer fallback): small, safe, immediate value. PR2 (Phase 2 external data): substantial, isolated to `/external/` + new Azure Function.  |

## Architecture

### Module layout

```
rehoboam/
├── scoring/
│   ├── scorer.py          # Phase 1: extend _extract_* helpers to fall back to prior season
│   ├── cold_start.py      # Phase 2 NEW: cold_start_score() parallel scoring path
│   ├── collector.py       # Phase 2: dispatches between regular/cold-start
│   └── models.py          # Phase 2: add ColdStartData dataclass
├── external/              # Phase 2 NEW: external data layer
│   ├── __init__.py
│   ├── models.py          # ExternalPlayerStats, LeagueStrength
│   ├── understat.py       # Big-5 prior-season goal/assist/minutes
│   ├── club_elo.py        # Team-strength → league-strength factors
│   ├── openligadb.py      # Bundesliga + 2.Bundesliga
│   └── cache.py           # JSON cache read/write
└── cli.py                 # Phase 2: new refresh-external-data command

deploy/azure_function_external_refresh/   # Phase 2 NEW
├── function_app.py        # Weekly cron Sunday 06:00 UTC
├── host.json
└── requirements.txt
```

### Boundaries

- `external/*` modules are I/O-only: fetch from web, normalize into dataclasses, cache to JSON. Zero scoring logic.
- `scoring/cold_start.py` is pure: takes `MarketPlayer + ExternalPlayerStats + team_profiles`, returns `PlayerScore`. No I/O.
- `scoring/collector.py` is the only place that chooses between regular and cold-start paths.

## Phase 1 — Prior-season fallback (small PR)

### Change scope

Three helper functions in `rehoboam/scoring/scorer.py` get the same fallback logic: `_extract_consistency`, `_extract_minutes_trend`, `_extract_recent_form`. Each currently picks `seasons_sorted[0]` (most recent season) and treats it as authoritative. The fix iterates seasons in recency order and picks the first one with `played > 0` matches.

Pattern for `_extract_consistency`:

```python
def _extract_consistency(performance: dict) -> tuple[int, float | None, str | None]:
    """Returns (games_played, consistency_score, season_used).

    If the most-recent season has zero played matches, falls back to
    the most recent season with played > 0.
    """
    seasons = performance.get("it", [])
    if not seasons:
        return 0, None, None

    seasons_sorted = sorted(seasons, key=lambda s: s.get("ti", ""), reverse=True)

    chosen = None
    for s in seasons_sorted:
        matches_played = [
            m
            for m in s.get("ph", [])
            if m.get("p", 0) != 0 or _parse_minutes(m.get("mp")) > 0
        ]
        if matches_played:
            chosen = (s, matches_played)
            break

    if not chosen:
        return 0, None, None

    season, matches_played = chosen
    season_title = season.get("ti")
    # ... rest of existing consistency math unchanged ...
    return games_played, consistency_score, season_title
```

The other two helpers get the same fallback. `score_player()` adds one note when fallback is used:

```python
if season_used and season_used != _most_recent_season_title(performance):
    notes.append(f"Using prior season {season_used} (current season empty)")
```

### Risk profile

Very low. Pure addition to existing extractors. Behavior unchanged when current season has played-match data. All existing scorer tests pass without modification.

### Tests

- Empty current season + populated prior season → uses prior season, grade A or B
- Multiple empty seasons (youth gap years like Nübel's) → finds first season with played > 0
- Empty all seasons → grade F (no regression)
- Note text appears in `PlayerScore.notes` when fallback is used

## Phase 2 — External-data cold-start

### `ExternalPlayerStats` dataclass

```python
@dataclass
class ExternalPlayerStats:
    """Per-player aggregates from a single source-league season."""

    player_name: str  # normalized "lastname firstname"
    source: str  # "understat:laliga" | "openligadb:bl2" | ...
    season: str  # "2025/26"
    league: str  # "La Liga"
    team: str  # source-league team name
    position: str  # "Defender" | "Midfielder" | "Forward" | "Goalkeeper"
    games_played: int
    minutes_played: int
    goals: int
    assists: int
    xg: float | None  # Understat only
    xa: float | None  # Understat only
    yellows: int = 0
    reds: int = 0

    @property
    def goals_per_90(self) -> float:
        return (self.goals * 90.0 / self.minutes_played) if self.minutes_played else 0.0

    @property
    def assists_per_90(self) -> float:
        return (
            (self.assists * 90.0 / self.minutes_played) if self.minutes_played else 0.0
        )

    @property
    def minutes_per_match(self) -> float:
        return (self.minutes_played / self.games_played) if self.games_played else 0.0
```

### `cold_start_score()` formula

In `rehoboam/scoring/cold_start.py`. Pure function. Position-specific Kickbase-points conversion:

```
Forward:
  expected_per_match = (goals_per_90 * 120 + assists_per_90 * 40) * league_factor + 30_base

Midfielder:
  expected_per_match = (goals_per_90 * 100 + assists_per_90 * 50) * league_factor + 35_base

Defender:
  expected_per_match = (goals_per_90 * 80 + assists_per_90 * 35) * league_factor + 45_base_with_cs

Goalkeeper:
  expected_per_match = 50_base + league_factor * 15
```

Weights chosen from observed Kickbase point structure:

- Goal scored ≈ 120 pts (forward weight)
- Assist ≈ 40 pts
- Clean sheet (defender/GK) ≈ 30 pts baked into base
- Baseline actions per match (passes, tackles, fouls): 30-50 pts depending on position

### League factor

From `external/club_elo.py`:

```
league_factor = source_league_avg_team_elo / bundesliga_avg_team_elo
clamped to [0.5, 1.2]
```

Expected values:

| League         | Factor                                            |
| -------------- | ------------------------------------------------- |
| Premier League | ~1.05                                             |
| La Liga        | ~1.00                                             |
| Bundesliga     | 1.00 (baseline)                                   |
| Serie A        | ~0.95                                             |
| Ligue 1        | ~0.85                                             |
| 2.Bundesliga   | ~0.60                                             |
| Eredivisie     | ~0.70 (out of scope, factor preserved for future) |

### Cold-start `PlayerScore` output

- `expected_points` = formula output, capped 0-180
- `data_quality.grade` = "B" (Big-5 source, ≥20 games) | "C" (lower-tier or \<20 games) | never F (F is left to the regular scorer when neither path has data)
- `notes` = `["Cold-start: Understat La Liga 2025/26, 28 games, 0.50 g/90, 0.30 a/90, league factor 1.00"]`

### `DataCollector` dispatch

```python
def collect(
    self, player, performance, player_details, team_profiles, external_stats_lookup
):  # NEW param

    data = self._collect_kickbase(player, performance, player_details, team_profiles)

    if self._has_usable_performance(data):
        return data

    external = external_stats_lookup.get(player) if external_stats_lookup else None
    if external:
        data.cold_start_data = external  # New field on PlayerData

    return data
```

Caller in `trader.py`:

```python
data = collector.collect(...)
if data.cold_start_data:
    score = cold_start_score(
        data.player, data.cold_start_data, league_strength, team_profiles
    )
else:
    score = score_player(data, calibration_multiplier)
```

### External-source clients

**`external/understat.py`**: uses the `understatapi` Python package (free, scrapes understat.com embedded JSON). Fetches per-league per-season player aggregates once per refresh, saves to `logs/external/understat_{league}_{season}.json`.

**`external/openligadb.py`**: REST API at `api.openligadb.de`. Fetches per-team rosters and per-player season aggregates for Bundesliga + 2.Bundesliga.

**`external/club_elo.py`**: CSV at `api.clubelo.com/{club_name}`. Fetches current ratings for top-100 clubs across covered leagues. Computes league-strength factors.

**`external/cache.py`**:

- Cache files live in `logs/external/{source}_{league}_{season}.json`
- Same blob-storage round-trip as SQLite DBs (download at session start)
- No upload needed (external data is read-only from bot's perspective)
- TTL: cache lives until weekly Azure Function rewrites it

### Weekly Azure Function

`deploy/azure_function_external_refresh/function_app.py`:

- Timer: `0 0 6 * * 0` (Sunday 06:00 UTC)
- Runs each source's `refresh()` in sequence
- Writes JSON files to blob `rehoboam-data/external/*.json`
- Auth pattern (AZURE_STORAGE_CONNECTION_STRING, BLOB_CONTAINER) identical to main function
- Logs to App Insights

### Player matching across sources

Kickbase: `Harry Kane, team_id=2 (Bayern)`. Understat: `Harry Kane, team=Bayern Munich`. Matching strategy:

1. Normalize names: lowercase, strip diacritics (`Müller` → `muller`), strip suffixes (`Jr.`, `II`)
1. Match `(normalized_lastname, normalized_firstname)` exactly first
1. Fall back to `normalized_lastname + position` when firstname differs
1. Manual override map at `logs/external/manual_player_map.json` for known mismatches (empty by default, populated as collisions are observed)

## Testing strategy

### Phase 1

- Unit tests for the 3 modified extractors covering: empty current + populated prior → uses prior, multiple empty seasons → first populated, empty all seasons → grade F preserved
- Integration test: `score_player` with Kane-shaped `PlayerData` (current empty, prior populated) verifies non-zero EP
- All existing scorer tests pass without modification

### Phase 2

**Unit tests** (no network):

- Each `external/*.py` client tested with mocked HTTP and golden-file JSON fixtures under `tests/fixtures/external/`
- `cold_start_score()` unit tests: per-position formulas, league factor application, grade B vs C assignment
- Player matching: exact, diacritics, suffixes, position fallback, manual override
- `DataCollector` dispatch: regular path with Kickbase data, cold-start path with only external, grade F when neither

**Integration tests** (`@pytest.mark.integration`, skipped in CI by default):

- `refresh-external-data` CLI against live sources, verifies cache files written

**Live smoke before merge**:

- `rehoboam status --dry-run` against prod squad
- Verifies no regressions for players with full Kickbase data
- Verifies cold-start dispatches show up in logs with cold-start note

## Error handling

External data is a *fallback*. Failure to fetch/parse must never break the main pipeline.

| Failure                                                  | Behavior                                                                      |
| -------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Azure Function refresh fails entirely                    | Bot uses last successful cache. Logs WARNING. No bidding impact.              |
| Understat scraping returns malformed JSON for one player | That player ends up grade F. Other players unaffected.                        |
| Player matching ambiguous (two Müllers)                  | Both get grade F. Suggested override emitted as WARNING log.                  |
| Cache file missing entirely (first run)                  | All cold-start players grade F. Bot operates normally on Kickbase-only data.  |
| ClubElo data missing for a league                        | League factor defaults to 0.85 (conservative). INFO log.                      |
| External cache files corrupt                             | Bot uses Phase 1 (Kickbase prior-season) only. Auto-recovers on next refresh. |

**Logging**: every cold-start scoring decision writes a structured INFO log:

```
cold-start player=X source=understat:laliga g/90=0.5 a/90=0.3 league_factor=1.0 EP=72.5
```

**Hard rule**: cold-start logic never throws into the calling pipeline. All exceptions caught with `logger.exception`, player falls back to grade F.

## Implementation order

PR1 (Phase 1):

1. Modify `_extract_consistency`, `_extract_minutes_trend`, `_extract_recent_form` in `rehoboam/scoring/scorer.py` to return `(games_played, value, season_used)` with prior-season fallback.
1. Update `score_player` to read `season_used` and add a note if it's not the most recent season.
1. Update callers (only `score_player` itself uses these helpers internally).
1. Add 4 tests in `tests/scoring/test_scorer.py`.
1. Live smoke (`--dry-run` against prod squad). Verify no regressions.
1. Open PR1.

PR2 (Phase 2):

1. Add `external/` module skeleton with `models.py`, `cache.py`.
1. Implement `external/understat.py` with golden-file tests.
1. Implement `external/club_elo.py` with golden-file tests.
1. Implement `external/openligadb.py` with golden-file tests.
1. Implement `scoring/cold_start.py` with full unit tests.
1. Update `scoring/collector.py` to dispatch on `cold_start_data`.
1. Update `trader.py` caller to load external lookup and choose path.
1. Add `rehoboam refresh-external-data` CLI command.
1. Add `rehoboam check-cold-start-coverage` CLI command.
1. Implement weekly Azure Function in `deploy/azure_function_external_refresh/`.
1. Update `function_app.py` (main) to download external/\*.json alongside DBs.
1. Live smoke. Verify regular Kickbase scoring unchanged. Verify cold-start paths fire for a synthetic fresh-arrival test.
1. Open PR2.

## Open questions

None at this point. All design decisions are locked.
