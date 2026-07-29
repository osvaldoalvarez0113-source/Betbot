"""
racha_detector.py
─────────────────
Compact W/L streak module for MLB teams.
Standalone — does NOT import from kelly_odds to avoid circular dependency.

Public API
----------
team_racha(team_name: str, team_es: str) -> dict
    Returns:
        line      – compact one-liner for display (never raises)
        note      – regression-to-mean warning when streak ≥ 5 contradicts season record
        wins_10   – int
        losses_10 – int
        streak    – int  (current consecutive run)
        streak_type – "W" | "L"

get_racha_line(home, home_es, away, away_es) -> str
    Combined one-liner: "🏃 Racha: {home_es} X | {away_es} Y"
    Empty string on any failure (never raises).
"""

from __future__ import annotations
import os
import datetime
import requests

_ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
_CACHE: dict = {}

_ET = datetime.timezone(datetime.timedelta(hours=-5))   # approx; no pytz needed here


def _today() -> str:
    return datetime.datetime.now(_ET).strftime("%Y-%m-%d")


def _label_strip(s: str) -> str:
    for prefix in ("🔵 ", "🔴 ", "📈 ", "📉 ", "🏃 ", "🔥 ", "❄️ "):
        s = s.replace(prefix, "")
    return s.strip()


# ── Season record (statsapi.mlb.com) ─────────────────────────────────────────

_MLB_TEAM_IDS: dict[str, int] = {
    "Arizona Diamondbacks": 109, "Diamondbacks": 109,
    "Atlanta Braves": 144,       "Braves": 144,
    "Baltimore Orioles": 110,    "Orioles": 110,
    "Boston Red Sox": 111,       "Red Sox": 111,
    "Chicago Cubs": 112,         "Cubs": 112,
    "Chicago White Sox": 145,    "White Sox": 145,
    "Cincinnati Reds": 113,      "Reds": 113,
    "Cleveland Guardians": 114,  "Guardians": 114,
    "Colorado Rockies": 115,     "Rockies": 115,
    "Detroit Tigers": 116,       "Tigers": 116,
    "Houston Astros": 117,       "Astros": 117,
    "Kansas City Royals": 118,   "Royals": 118,
    "Los Angeles Angels": 108,   "Angels": 108,
    "Los Angeles Dodgers": 119,  "Dodgers": 119,
    "Miami Marlins": 146,        "Marlins": 146,
    "Milwaukee Brewers": 158,    "Brewers": 158,
    "Minnesota Twins": 142,      "Twins": 142,
    "New York Mets": 121,        "Mets": 121,
    "New York Yankees": 147,     "Yankees": 147,
    "Oakland Athletics": 133,    "Athletics": 133,
    "Philadelphia Phillies": 143,"Phillies": 143,
    "Pittsburgh Pirates": 134,   "Pirates": 134,
    "San Diego Padres": 135,     "Padres": 135,
    "San Francisco Giants": 137, "Giants": 137,
    "Seattle Mariners": 136,     "Mariners": 136,
    "St. Louis Cardinals": 138,  "Cardinals": 138,
    "Tampa Bay Rays": 139,       "Rays": 139,
    "Texas Rangers": 140,        "Rangers": 140,
    "Toronto Blue Jays": 141,    "Blue Jays": 141,
    "Washington Nationals": 120, "Nationals": 120,
}

_SEASON_RECORD_CACHE: dict = {}
_MLB_YEAR = str(datetime.datetime.now(_ET).year)


def _fetch_season_wpct(team_name: str) -> "float | None":
    """Win% from MLB regular-season standings. Returns None on any error."""
    ck = f"wpct|{team_name}|{_today()}"
    if ck in _SEASON_RECORD_CACHE:
        return _SEASON_RECORD_CACHE[ck]
    try:
        tid = _MLB_TEAM_IDS.get(team_name)
        if not tid:
            tl = team_name.lower()
            for k, v in _MLB_TEAM_IDS.items():
                if k.lower() in tl or tl in k.lower():
                    tid = v
                    break
        if not tid:
            _SEASON_RECORD_CACHE[ck] = None
            return None
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/standings",
            params={
                "leagueId":       "103,104",
                "season":         _MLB_YEAR,
                "standingsTypes": "regularSeason",
                "hydrate":        "team,record",
            },
            timeout=8,
        )
        if r.status_code != 200:
            _SEASON_RECORD_CACHE[ck] = None
            return None
        for rec in r.json().get("records", []):
            for tr in rec.get("teamRecords", []):
                if tr.get("team", {}).get("id") == tid:
                    w = int(tr.get("wins", 0) or 0)
                    l = int(tr.get("losses", 0) or 0)
                    total = w + l
                    wpct  = w / total if total > 0 else 0.500
                    _SEASON_RECORD_CACHE[ck] = wpct
                    return wpct
    except Exception:
        pass
    _SEASON_RECORD_CACHE[ck] = None
    return None


# ── Core streak fetch ─────────────────────────────────────────────────────────

def team_racha(team_name: str, team_es: str) -> dict:
    """
    Fetch last-10 W/L record and current streak via Odds API scores endpoint.
    Returns dict with keys: line, note, wins_10, losses_10, streak, streak_type.
    Never raises; returns empty line/note on failure.
    """
    empty = {"line": "", "note": "", "wins_10": 0, "losses_10": 0, "streak": 0, "streak_type": ""}
    if not _ODDS_API_KEY:
        return empty

    ck = f"racha|{team_name}|{_today()}"
    if ck in _CACHE:
        return _CACHE[ck]

    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/scores/",
            params={"apiKey": _ODDS_API_KEY, "daysFrom": 21, "dateFormat": "iso"},
            timeout=10,
        )
        if r.status_code != 200:
            _CACHE[ck] = empty
            return empty

        all_games = [
            g for g in r.json()
            if (g.get("home_team") == team_name or g.get("away_team") == team_name)
            and g.get("completed") is True
        ]
        all_games.sort(key=lambda g: g.get("commence_time", ""), reverse=True)
        last10 = all_games[:10]

        if len(last10) < 3:          # too few games — skip
            _CACHE[ck] = empty
            return empty

        results = []
        for g in last10:
            opp   = g["away_team"] if g["home_team"] == team_name else g["home_team"]
            sc_map = {s["name"]: int(s["score"])
                      for s in (g.get("scores") or []) if s.get("score") is not None}
            my_sc = sc_map.get(team_name, 0)
            op_sc = sc_map.get(opp, 0)
            results.append("W" if my_sc > op_sc else "L")

        wins_10   = results.count("W")
        losses_10 = results.count("L")
        n         = len(results)

        # Current consecutive streak
        streak_type = results[0]
        streak = 0
        for r_val in results:
            if r_val == streak_type:
                streak += 1
            else:
                break

        # ── Compact display line ──────────────────────────────────────────────
        if streak >= 5:
            word = "ganó" if streak_type == "W" else "perdió"
            line = f"{team_es} {word} {streak} de {streak}"
        else:
            line = f"{team_es} {wins_10}-{losses_10} últ.{n}"

        # ── Regression-to-mean note (informational only) ──────────────────────
        note = ""
        if streak >= 5:
            wpct = _fetch_season_wpct(team_name)
            if wpct is not None:
                if streak_type == "W" and wpct < 0.460:
                    note = (f"⚠️ {team_es}: racha de {streak}G ganados — "
                            f"equipo de temporada < .460, posible regresión")
                elif streak_type == "L" and wpct > 0.540:
                    note = (f"⚠️ {team_es}: racha de {streak}G perdidos — "
                            f"equipo de temporada > .540, posible regresión")

        result = {
            "line":       line,
            "note":       note,
            "wins_10":    wins_10,
            "losses_10":  losses_10,
            "streak":     streak,
            "streak_type": streak_type,
        }
        _CACHE[ck] = result
        return result

    except Exception:
        _CACHE[ck] = empty
        return empty


def get_racha_line(home: str, home_es: str, away: str, away_es: str) -> str:
    """
    Returns a single combined racha line for both teams, e.g.:
      "🏃 Racha: MIA ganó 6 de 6 | PHI 4-6 últ.10"
    Returns "" if both lookups fail.
    """
    try:
        rh = team_racha(home, home_es)
        ra = team_racha(away, away_es)
        parts = [x for x in (rh["line"], ra["line"]) if x]
        if not parts:
            return ""
        return "🏃 Racha: " + " | ".join(parts)
    except Exception:
        return ""


def get_regression_notes(home: str, home_es: str, away: str, away_es: str) -> list[str]:
    """
    Returns list of regression-to-mean warning strings (0-2 items).
    Each non-empty note is included.
    """
    try:
        notes = []
        for tname, tes in ((home, home_es), (away, away_es)):
            n = team_racha(tname, tes).get("note", "")
            if n:
                notes.append(n)
        return notes
    except Exception:
        return []
