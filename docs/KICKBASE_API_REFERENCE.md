# Kickbase API v4 Reference

Unofficial API documentation based on [kevinskyba/kickbase-api-doc](https://github.com/kevinskyba/kickbase-api-doc).

**Base URL**: `https://api.kickbase.com`
**Auth**: Bearer token via `Authorization` header (obtained from `/v4/user/login`)

Legend: **\[USED\]** = implemented in our client, **\[NEW\]** = not yet implemented

______________________________________________________________________

## Authentication

| Status   | Method | Path                     | Description              | Body         |
| -------- | ------ | ------------------------ | ------------------------ | ------------ |
| \[USED\] | POST   | `/v4/user/login`         | Login, get token         | `{em, pass}` |
| \[NEW\]  | POST   | `/v4/user/refreshtokens` | Refresh expired token    | `{rtkn}`     |
| \[USED\] | GET    | `/v4/user/me`            | Get current user profile |              |

______________________________________________________________________

## Lineup

| Status   | Method | Path                                         | Description                 | Body/Query                              |
| -------- | ------ | -------------------------------------------- | --------------------------- | --------------------------------------- |
| \[USED\] | GET    | `/v4/leagues/{leagueId}/lineup`              | Get current lineup          |                                         |
| \[USED\] | POST   | `/v4/leagues/{leagueId}/lineup`              | **Set lineup**              | `{type: "4-3-3", players: ["id1",...]}` |
| \[NEW\]  | POST   | `/v4/leagues/{leagueId}/lineup/clear`        | Clear entire lineup         |                                         |
| \[NEW\]  | POST   | `/v4/leagues/{leagueId}/lineup/fill`         | Auto-fill lineup            | `{lud, pls[]}`                          |
| \[NEW\]  | GET    | `/v4/leagues/{leagueId}/lineup/overview`     | Lineup overview with points |                                         |
| \[NEW\]  | GET    | `/v4/leagues/{leagueId}/lineup/selection`    | Browse players for lineup   | `?position, sorting, query, start, max` |
| \[NEW\]  | GET    | `/v4/leagues/{leagueId}/lineup/teams`        | Teams in lineup context     |                                         |
| \[USED\] | GET    | `/v4/leagues/{leagueId}/teamcenter/myeleven` | Get starting eleven         |                                         |

______________________________________________________________________

## Market & Trading

| Status   | Method | Path                                                                | Description                     | Body/Query                   |
| -------- | ------ | ------------------------------------------------------------------- | ------------------------------- | ---------------------------- |
| \[USED\] | GET    | `/v4/leagues/{leagueId}/market`                                     | Get all players on market       |                              |
| \[USED\] | POST   | `/v4/leagues/{leagueId}/market`                                     | List player for sale            | `{pi: playerId, prc: price}` |
| \[USED\] | DELETE | `/v4/leagues/{leagueId}/market/{playerId}`                          | Remove player from market       |                              |
| \[USED\] | POST   | `/v4/leagues/{leagueId}/market/{playerId}/offers`                   | Place bid on player             | `{price}`                    |
| \[USED\] | DELETE | `/v4/leagues/{leagueId}/market/{playerId}/offers/{offerId}`         | Cancel/delete bid               |                              |
| \[NEW\]  | POST   | `/v4/leagues/{leagueId}/market/{playerId}/offers/{offerId}/accept`  | Accept offer on your player     |                              |
| \[NEW\]  | DELETE | `/v4/leagues/{leagueId}/market/{playerId}/offers/{offerId}/accept`  | Accept manager offer (variant)  |                              |
| \[NEW\]  | POST   | `/v4/leagues/{leagueId}/market/{playerId}/offers/{offerId}/decline` | Decline offer on your player    |                              |
| \[NEW\]  | DELETE | `/v4/leagues/{leagueId}/market/{playerId}/offers/{offerId}/decline` | Decline manager offer (variant) |                              |
| \[USED\] | POST   | `/v4/leagues/{leagueId}/market/{playerId}/sell`                     | List player for sale (alt)      |                              |
| \[NEW\]  | DELETE | `/v4/leagues/{leagueId}/market/{playerId}/sell`                     | Sell to Kickbase (instant)      |                              |

______________________________________________________________________

## Squad & Players

| Status   | Method | Path                                                                | Description                 | Body/Query |
| -------- | ------ | ------------------------------------------------------------------- | --------------------------- | ---------- |
| \[USED\] | GET    | `/v4/leagues/{leagueId}/squad`                                      | Get your squad              |            |
| \[USED\] | GET    | `/v4/leagues/{leagueId}/players/{playerId}`                         | Get player details          |            |
| \[USED\] | GET    | `/v4/leagues/{leagueId}/players/{playerId}/performance`             | Player performance history  |            |
| \[USED\] | GET    | `/v4/leagues/{leagueId}/players/{playerId}/marketvalue/{timeframe}` | Player market value history |            |
| \[NEW\]  | GET    | `/v4/leagues/{leagueId}/players/{playerId}/transferHistory`         | Player transfer history     | `?start`   |
| \[NEW\]  | GET    | `/v4/leagues/{leagueId}/players/{playerId}/transfers`               | Player transfers            |            |

______________________________________________________________________

## Scouted Players (Watchlist)

| Status  | Method | Path                                               | Description                   |
| ------- | ------ | -------------------------------------------------- | ----------------------------- |
| \[NEW\] | GET    | `/v4/leagues/{leagueId}/scoutedplayers`            | Get scouted/watchlist players |
| \[NEW\] | POST   | `/v4/leagues/{leagueId}/scoutedplayers/{playerId}` | Add player to watchlist       |
| \[NEW\] | DELETE | `/v4/leagues/{leagueId}/scoutedplayers/{playerId}` | Remove from watchlist         |
| \[NEW\] | DELETE | `/v4/leagues/{leagueId}/scoutedplayers`            | Clear entire watchlist        |

______________________________________________________________________

## League Info & Rankings

| Status   | Method | Path                                          | Description                                     | Body/Query                                        |
| -------- | ------ | --------------------------------------------- | ----------------------------------------------- | ------------------------------------------------- |
| \[USED\] | GET    | `/v4/leagues`                                 | Get user's leagues (via login response)         |                                                   |
| \[NEW\]  | GET    | `/v4/leagues/list`                            | Search/browse leagues                           | `?query, competitionId, gameplayMode, start, max` |
| \[USED\] | GET    | `/v4/leagues/{leagueId}/me`                   | Current user's league data (budget, team value) |                                                   |
| \[NEW\]  | GET    | `/v4/leagues/{leagueId}/me/budget`            | Get budget only                                 |                                                   |
| \[NEW\]  | GET    | `/v4/leagues/{leagueId}/overview`             | League overview                                 | `?includeManagersAndBattles`                      |
| \[NEW\]  | GET    | `/v4/leagues/{leagueId}/ranking`              | League ranking/standings                        | `?dayNumber`                                      |
| \[NEW\]  | GET    | `/v4/leagues/{leagueId}/settings`             | League settings                                 |                                                   |
| \[NEW\]  | GET    | `/v4/leagues/{leagueId}/settings/managers`    | Manager settings                                |                                                   |
| \[NEW\]  | GET    | `/v4/leagues/{leagueId}/battles/{type}/users` | Battle mode users                               |                                                   |

______________________________________________________________________

## Manager Intelligence (Competitors)

| Status  | Method | Path                                                      | Description                 | Body/Query   |
| ------- | ------ | --------------------------------------------------------- | --------------------------- | ------------ |
| \[NEW\] | GET    | `/v4/leagues/{leagueId}/managers/{managerId}/dashboard`   | Manager dashboard           |              |
| \[NEW\] | GET    | `/v4/leagues/{leagueId}/managers/{managerId}/performance` | Manager performance history |              |
| \[NEW\] | GET    | `/v4/leagues/{leagueId}/managers/{managerId}/squad`       | **View competitor's squad** |              |
| \[NEW\] | GET    | `/v4/leagues/{leagueId}/managers/{managerId}/transfer`    | Manager transfer history    | `?start`     |
| \[NEW\] | GET    | `/v4/leagues/{leagueId}/users/{userId}/teamcenter`        | User's team center          | `?dayNumber` |

______________________________________________________________________

## Activity Feed

| Status   | Method | Path                                                          | Description          | Body/Query            |
| -------- | ------ | ------------------------------------------------------------- | -------------------- | --------------------- |
| \[USED\] | GET    | `/v4/leagues/{leagueId}/activitiesFeed`                       | League activity feed | `?start, max, filter` |
| \[NEW\]  | GET    | `/v4/leagues/{leagueId}/activitiesFeed/{activityId}`          | Single activity item |                       |
| \[NEW\]  | GET    | `/v4/leagues/{leagueId}/activitiesFeed/{activityId}/comments` | Activity comments    | `?start, max`         |
| \[NEW\]  | POST   | `/v4/leagues/{leagueId}/activitiesFeed/{activityId}/comments` | Post comment         | `{comm}`              |

______________________________________________________________________

## Competitions (Bundesliga data)

| Status   | Method | Path                                                                          | Description                    | Body/Query                     |
| -------- | ------ | ----------------------------------------------------------------------------- | ------------------------------ | ------------------------------ |
| \[NEW\]  | GET    | `/v4/competitions`                                                            | List all competitions          |                                |
| \[NEW\]  | GET    | `/v4/competitions/{competitionId}/matchdays`                                  | **Get matchday schedule**      |                                |
| \[NEW\]  | GET    | `/v4/competitions/{competitionId}/table`                                      | **Bundesliga table/standings** |                                |
| \[NEW\]  | GET    | `/v4/competitions/{competitionId}/ranking`                                    | Competition ranking            |                                |
| \[NEW\]  | GET    | `/v4/competitions/{competitionId}/players`                                    | **All players in competition** | `?position, sorting`           |
| \[NEW\]  | GET    | `/v4/competitions/{competitionId}/players/search`                             | Search players                 | `?leagueId, query, start, max` |
| \[NEW\]  | GET    | `/v4/competitions/{competitionId}/players/{playerId}`                         | Player details (competition)   |                                |
| \[NEW\]  | GET    | `/v4/competitions/{competitionId}/players/{playerId}/performance`             | Player performance             |                                |
| \[NEW\]  | GET    | `/v4/competitions/{competitionId}/players/{playerId}/marketValue/{timeframe}` | Market value history           |                                |
| \[NEW\]  | GET    | `/v4/competitions/{competitionId}/playercenter/{playerId}`                    | Player center data             | `?dayNumber, leagueId`         |
| \[USED\] | GET    | `/v4/competitions/{competitionId}/teams/{teamId}/teamprofile`                 | Team profile/strength          |                                |
| \[NEW\]  | GET    | `/v4/competitions/{competitionId}/teams/{teamId}/teamcenter`                  | Team center                    | `?dayNumber`                   |

______________________________________________________________________

## Matches (Live Data)

| Status  | Method | Path                            | Description                             | Body/Query |
| ------- | ------ | ------------------------------- | --------------------------------------- | ---------- |
| \[NEW\] | GET    | `/v4/matches/{matchId}/details` | **Match details (live scores, events)** |            |
| \[NEW\] | GET    | `/v4/live/eventtypes`           | All possible live event types           |            |

______________________________________________________________________

## Invitations

| Status  | Method | Path                              | Description            |
| ------- | ------ | --------------------------------- | ---------------------- |
| \[NEW\] | GET    | `/v4/invitations/{leagueId}/code` | Get league invite code |
| \[NEW\] | GET    | `/v4/invitations/{code}/validate` | Validate invite code   |

______________________________________________________________________

## User Settings

| Status  | Method | Path                      | Description          | Body            |
| ------- | ------ | ------------------------- | -------------------- | --------------- |
| \[NEW\] | GET    | `/v4/user/settings`       | Get user settings    |                 |
| \[NEW\] | PUT    | `/v4/user/settings`       | Update user settings | `{em, unm}`     |
| \[NEW\] | POST   | `/v4/user/settings/image` | Update profile image |                 |
| \[NEW\] | POST   | `/v4/user/password`       | Change password      | `{npass, pass}` |
| \[NEW\] | DELETE | `/v4/user`                | Delete account       |                 |

______________________________________________________________________

## High-Value Endpoints for Future Features

### Double Gameweek Detection

- `GET /v4/competitions/{competitionId}/matchdays` — Get the matchday schedule to detect teams playing twice

### Competitor Scouting

- `GET /v4/leagues/{leagueId}/managers/{managerId}/squad` — See what players competitors own
- `GET /v4/leagues/{leagueId}/managers/{managerId}/transfer` — See competitor buy/sell history
- `GET /v4/leagues/{leagueId}/ranking` — League standings with `?dayNumber` for historical

### Player Discovery

- `GET /v4/competitions/{competitionId}/players` — Browse ALL players, not just market. With `?sorting` to find top scorers
- `GET /v4/competitions/{competitionId}/players/search` — Search players by name

### Live Match Tracking

- `GET /v4/matches/{matchId}/details` — Real-time match events and scores

### Token Refresh

- `POST /v4/user/refreshtokens` — Refresh auth token without re-login. Body: `{rtkn: refreshToken}`. Currently we re-login each session.
