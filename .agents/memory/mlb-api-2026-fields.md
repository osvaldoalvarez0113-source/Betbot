---
name: MLB Stats API 2026 field changes
description: Field names that changed/disappeared in the MLB Stats API for season=2026
---

## Rule
For season=2026 (and possibly later), these fields are absent from `/teams/{id}/stats`:
- `runsPerGame` — NOT present in hitting group. Compute `runs / gamesPlayed` instead.
- `runsAllowed` — NOT present in pitching group. Use `runs` (same meaning in pitching context).

The `homeAndAway` stats type returns **HTTP 404** for all teams in 2026.

## Affected functions (fixed)
- `fetch_team_pitching_ra()` — now uses `stat.get("runsAllowed") or stat.get("runs")`
- `fetch_team_batting()` — same fix for ra_pg; rs_pg uses `runs/gamesPlayed`; returns `None` not `4.5`
- `fetch_team_run_stats()` — `ra_raw` now uses `p_stat.get("runsAllowed") or p_stat.get("runs")`
- `fetch_mlb_home_away_splits()` — now calls `_splits_from_schedule()` as primary (schedule endpoint works); homeAndAway kept as secondary in case API is restored

**Why:** `pythagorean_win_prob` was receiving None and now handles it (returns 0.5). Notifications show "N/D" instead of 4.5 when data is truly unavailable.

## Verified working (June 2026)
- `/teams/{id}/stats?stats=season&group=hitting` → `runs` + `gamesPlayed` present ✅
- `/teams/{id}/stats?stats=season&group=pitching` → `runs` (=RA) + `gamesPlayed` + `era` present ✅
- `/schedule?teamId={id}&season=2026&hydrate=linescore` → full linescore data ✅
- Yankees 2026: RS/g=5.05, RA/g=3.60; Home RS=5.37 RA=4.03, Away RS=4.76 RA=3.21
