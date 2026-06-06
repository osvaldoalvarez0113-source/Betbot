#!/usr/bin/env python3
"""
BetBot Pro — Backtesting Engine
Tests the MLB totals model against actual 2026 regular-season results.

Usage:
  python3 backtest.py              # backtest April 1 → today, save CSV
  python3 backtest.py 2026-04-01 2026-05-31   # custom date range

Results saved to backtest_log.csv and printed to stdout.
Also callable from kelly_odds.py for automated Sunday reports.
"""
import os, sys, csv, json, requests, time
from datetime import datetime, date, timedelta
from collections import defaultdict

# ── Constants (mirrors kelly_odds.py) ─────────────────────────────────────────
LEAGUE_AVG = 4.5
MLB_YEAR   = 2026
MLB_API    = "https://statsapi.mlb.com/api/v1"
NTFY_URL   = os.environ.get("NTFY_URL", "")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "betbot")
BACKTEST_CSV = "backtest_log.csv"

# Assumed book line for MLB totals (no historical book data in MLB Stats API)
DEFAULT_BOOK_LINE = 8.5

# Standard American -110 odds → implied prob 52.38%, payout factor 0.9091
ODDS_PAYOUT = 0.9091   # win $0.91 per $1 bet
STAKE       = 1.0       # $1 flat stake per bet (for ROI calculation)

MLB_PARK_FACTORS = {
    "Colorado Rockies":    1.28,
    "Cincinnati Reds":     1.12,
    "Philadelphia Phillies": 1.10,
    "Texas Rangers":       1.08,
    "Boston Red Sox":      1.07,
    "Milwaukee Brewers":   1.06,
    "Toronto Blue Jays":   1.05,
    "New York Yankees":    1.04,
    "Chicago Cubs":        1.04,
    "Baltimore Orioles":   1.03,
    "Tampa Bay Rays":      1.02,
    "Kansas City Royals":  1.02,
    "Arizona Diamondbacks":1.01,
    "Houston Astros":      1.00,
    "Atlanta Braves":      1.00,
    "Seattle Mariners":    1.00,
    "Minnesota Twins":     1.00,
    "Los Angeles Angels":  0.99,
    "Washington Nationals":0.98,
    "Detroit Tigers":      0.98,
    "Miami Marlins":       0.97,
    "New York Mets":       0.97,
    "Chicago White Sox":   0.96,
    "Cleveland Guardians": 0.96,
    "Pittsburgh Pirates":  0.96,
    "Oakland Athletics":   0.95,
    "San Diego Padres":    0.95,
    "Los Angeles Dodgers": 0.95,
    "San Francisco Giants":0.93,
    "St. Louis Cardinals": 0.93,
}

# ── MLB Stats API helpers ──────────────────────────────────────────────────────

def _mlb_get(path: str, params: dict = None) -> dict:
    url = MLB_API + path
    try:
        r = requests.get(url, params=params or {}, timeout=12)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  ⚠️  MLB API [{path}]: {e}")
        return {}

_team_stats_cache: dict = {}   # team_id → {rs_pg, ra_pg}
_team_id_cache:    dict = {}   # team_name_lower → team_id

def _lookup_team_id(team_name: str) -> "int | None":
    key = team_name.lower()
    if key in _team_id_cache:
        return _team_id_cache[key]
    data = _mlb_get("/teams", {"sportId": 1, "season": MLB_YEAR})
    for t in data.get("teams", []):
        n = t.get("name", "").lower()
        _team_id_cache[n] = t["id"]
        for word in n.split():
            if word not in _team_id_cache:
                _team_id_cache[word] = t["id"]
    return _team_id_cache.get(key)

def _fetch_team_run_stats(team_name: str) -> "dict | None":
    """Return {rs_pg, ra_pg} for a team using current season aggregate stats."""
    if team_name in _team_stats_cache:
        return _team_stats_cache[team_name]
    tid = _lookup_team_id(team_name)
    if not tid:
        # Try partial match
        for k, v in _team_id_cache.items():
            if any(w in team_name.lower() for w in k.split() if len(w) > 3):
                tid = v
                break
    if not tid:
        return None
    try:
        data  = _mlb_get(f"/teams/{tid}/stats",
                         {"stats": "season", "group": "hitting", "season": MLB_YEAR})
        splits = (data.get("stats", [{}]) or [{}])[0].get("splits", [])
        h_stat = splits[0].get("stat", {}) if splits else {}
        gp     = int(h_stat.get("gamesPlayed", 0) or 0)
        rs     = int(h_stat.get("runs", 0) or 0)

        data2  = _mlb_get(f"/teams/{tid}/stats",
                          {"stats": "season", "group": "pitching", "season": MLB_YEAR})
        sp2    = (data2.get("stats", [{}]) or [{}])[0].get("splits", [])
        p_stat = sp2[0].get("stat", {}) if sp2 else {}
        gp2    = int(p_stat.get("gamesPlayed", 0) or 0)
        ra     = int(p_stat.get("runsAllowed", 0) or 0)

        games  = max(gp, gp2, 1)
        result = {
            "rs_pg": round(rs / games, 3) if rs > 0 else LEAGUE_AVG,
            "ra_pg": round(ra / games, 3) if ra > 0 else LEAGUE_AVG,
            "games": games,
        }
        _team_stats_cache[team_name] = result
        return result
    except Exception as e:
        print(f"  ⚠️  Stats [{team_name}]: {e}")
        return None

def project_total(home: str, away: str) -> float:
    """
    Project the expected total runs for home vs away using our model formula.
    Uses current-season RS/RA stats (same as live bot).
    Returns float projection, or DEFAULT_BOOK_LINE if data unavailable.
    """
    h = _fetch_team_run_stats(home)
    a = _fetch_team_run_stats(away)
    if not h or not a:
        return DEFAULT_BOOK_LINE
    park = MLB_PARK_FACTORS.get(home, 1.0)
    home_exp = h["rs_pg"] * (a["ra_pg"] / LEAGUE_AVG) * park
    away_exp = a["rs_pg"] * (h["ra_pg"] / LEAGUE_AVG) * park
    return round(home_exp + away_exp, 2)

# ── Completed games fetcher ────────────────────────────────────────────────────

def fetch_completed_games(start_date: str, end_date: str) -> list:
    """
    Fetch all completed regular-season MLB games in the date range.
    Returns list of dicts:
      {date, home, away, home_runs, away_runs, total_runs}
    """
    print(f"  📅 Fetching completed games {start_date} → {end_date}...")
    data = _mlb_get("/schedule", {
        "sportId":   1,
        "season":    MLB_YEAR,
        "gameType":  "R",
        "startDate": start_date,
        "endDate":   end_date,
        "hydrate":   "linescore",
    })
    games = []
    for date_entry in data.get("dates", []):
        gdate = date_entry.get("date", "")
        for g in date_entry.get("games", []):
            status = g.get("status", {}).get("codedGameState", "")
            if status != "F":   # F = Final
                continue
            teams  = g.get("teams", {})
            home_t = teams.get("home", {})
            away_t = teams.get("away", {})
            home_n = home_t.get("team", {}).get("name", "")
            away_n = away_t.get("team", {}).get("name", "")
            ls     = g.get("linescore", {})
            home_r = int(ls.get("teams", {}).get("home", {}).get("runs", -1))
            away_r = int(ls.get("teams", {}).get("away", {}).get("runs", -1))
            if home_r < 0 or away_r < 0 or not home_n or not away_n:
                continue
            games.append({
                "date":       gdate,
                "home":       home_n,
                "away":       away_n,
                "home_runs":  home_r,
                "away_runs":  away_r,
                "total_runs": home_r + away_r,
            })
    print(f"  ✅ {len(games)} juegos completados encontrados")
    return games

# ── Core backtesting logic ─────────────────────────────────────────────────────

def run_backtest(
    start_date: str = None,
    end_date:   str = None,
    book_line:  float = DEFAULT_BOOK_LINE,
    save_csv_flag: bool = True,
) -> dict:
    """
    Full backtest: project totals → compare to actual results → compute metrics.

    Returns metrics dict with hit rates, ROI, best/worst breakdowns.
    Also saves results to BACKTEST_CSV and sends ntfy report.
    """
    today = date.today().isoformat()
    if not start_date:
        start_date = f"{MLB_YEAR}-04-01"
    if not end_date:
        end_date = today

    print(f"\n{'='*55}")
    print(f"📊 BACKTESTING ENGINE — {start_date} → {end_date}")
    print(f"{'='*55}")

    games = fetch_completed_games(start_date, end_date)
    if not games:
        print("  ❌ Sin juegos completados para analizar")
        return {}

    # Pre-fetch all team stats (batch for speed)
    all_teams = {g["home"] for g in games} | {g["away"] for g in games}
    print(f"  🔄 Pre-cargando stats de {len(all_teams)} equipos...")
    for tm in sorted(all_teams):
        _fetch_team_run_stats(tm)
        time.sleep(0.05)

    results  = []
    over_w   = over_l  = 0
    under_w  = under_l = 0
    by_park  = defaultdict(lambda: {"w": 0, "l": 0})
    by_month = defaultdict(lambda: {"w": 0, "l": 0})
    by_home  = defaultdict(lambda: {"w": 0, "l": 0})
    proj_errors = []

    print(f"\n  ⚙️  Analizando {len(games)} juegos...")
    for g in games:
        proj     = project_total(g["home"], g["away"])
        actual   = g["total_runs"]
        our_side = "OVER" if proj > book_line else "UNDER"
        actual_side = "OVER" if actual > book_line else ("UNDER" if actual < book_line else "PUSH")

        correct  = (our_side == actual_side)
        win      = correct and actual_side != "PUSH"
        push     = actual_side == "PUSH"
        pnl      = (STAKE * ODDS_PAYOUT) if win else (-STAKE if not push else 0.0)

        proj_err = proj - actual
        proj_errors.append(abs(proj_err))

        row = {
            "date":       g["date"],
            "home":       g["home"],
            "away":       g["away"],
            "projection": proj,
            "book_line":  book_line,
            "our_pick":   our_side,
            "actual":     actual,
            "result":     ("WIN" if win else "PUSH" if push else "LOSS"),
            "pnl":        round(pnl, 3),
            "proj_error": round(proj_err, 2),
        }
        results.append(row)

        if push:
            continue
        if our_side == "OVER":
            if win: over_w  += 1
            else:   over_l  += 1
        else:
            if win: under_w += 1
            else:   under_l += 1

        month = g["date"][:7]
        by_month[month]["w" if win else "l"] += 1
        by_park[g["home"]]["w" if win else "l"] += 1
        by_home[g["home"]]["w" if win else "l"] += 1

    # ── Aggregate metrics ─────────────────────────────────────────────────────
    total_bets = over_w + over_l + under_w + under_l
    total_wins = over_w + under_w
    total_pnl  = sum(r["pnl"] for r in results)
    hit_rate   = round(total_wins / total_bets * 100, 1) if total_bets else 0
    roi        = round(total_pnl / (total_bets * STAKE) * 100, 1) if total_bets else 0
    over_rate  = round(over_w / (over_w + over_l) * 100, 1) if (over_w + over_l) else 0
    under_rate = round(under_w / (under_w + under_l) * 100, 1) if (under_w + under_l) else 0
    mae        = round(sum(proj_errors) / len(proj_errors), 2) if proj_errors else 0

    # Best and worst parks
    park_stats = {
        k: {"w": v["w"], "l": v["l"],
            "rate": round(v["w"] / max(v["w"]+v["l"],1)*100,1)}
        for k, v in by_park.items() if v["w"]+v["l"] >= 5
    }
    best_parks  = sorted(park_stats.items(), key=lambda x: x[1]["rate"], reverse=True)[:3]
    worst_parks = sorted(park_stats.items(), key=lambda x: x[1]["rate"])[:3]

    # Best and worst months
    month_stats = {
        k: {"w": v["w"], "l": v["l"],
            "rate": round(v["w"] / max(v["w"]+v["l"],1)*100,1)}
        for k, v in by_month.items() if v["w"]+v["l"] >= 3
    }

    metrics = {
        "period":        f"{start_date} → {end_date}",
        "games_total":   len(games),
        "bets_made":     total_bets,
        "wins":          total_wins,
        "losses":        total_bets - total_wins,
        "hit_rate":      hit_rate,
        "roi":           roi,
        "total_pnl":     round(total_pnl, 2),
        "over_rate":     over_rate,
        "under_rate":    under_rate,
        "over_record":   f"{over_w}-{over_l}",
        "under_record":  f"{under_w}-{under_l}",
        "mae":           mae,
        "best_parks":    best_parks,
        "worst_parks":   worst_parks,
        "month_stats":   month_stats,
        "book_line":     book_line,
    }

    # ── Save CSV ──────────────────────────────────────────────────────────────
    if save_csv_flag and results:
        _save_csv(results)

    report = _format_report(metrics)
    print(report)
    _send_ntfy(report)
    return metrics

# ── CSV output ────────────────────────────────────────────────────────────────

def _save_csv(results: list):
    """Append results to backtest_log.csv (creates file if missing)."""
    fieldnames = ["date","home","away","projection","book_line",
                  "our_pick","actual","result","pnl","proj_error"]
    file_exists = os.path.isfile(BACKTEST_CSV)
    # Avoid duplicates: load existing dates
    existing = set()
    if file_exists:
        try:
            with open(BACKTEST_CSV, "r", newline="") as f:
                for row in csv.DictReader(f):
                    existing.add(f"{row['date']}|{row['home']}|{row['away']}")
        except Exception:
            pass
    new_rows = [r for r in results
                if f"{r['date']}|{r['home']}|{r['away']}" not in existing]
    if not new_rows:
        print(f"  ℹ️  Sin filas nuevas para agregar a {BACKTEST_CSV}")
        return
    with open(BACKTEST_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists or not existing:
            writer.writeheader()
        writer.writerows(new_rows)
    print(f"  💾 {len(new_rows)} filas guardadas en {BACKTEST_CSV}")

# ── Report formatter ──────────────────────────────────────────────────────────

def _format_report(m: dict) -> str:
    div  = "━" * 38
    div2 = "─" * 38

    profitable  = m["roi"] > 0
    verdict     = "El modelo es rentable ✅" if profitable else "El modelo necesita ajustes ⚠️"
    roi_emoji   = "🟢" if m["roi"] > 3 else "🟡" if m["roi"] >= 0 else "🔴"
    over_arrow  = "⬆️" if m["over_rate"] > 53 else "⬇️"
    under_arrow = "⬆️" if m["under_rate"] > 53 else "⬇️"

    best_park_lines = "\n".join(
        f"   {'🥇' if i==0 else '🥈' if i==1 else '🥉'} "
        f"{name.split()[-1]}: {s['rate']}% ({s['w']}-{s['l']})"
        for i, (name, s) in enumerate(m["best_parks"])
    ) if m["best_parks"] else "   N/D (≥5 juegos necesarios)"

    worst_park_lines = "\n".join(
        f"   ⚠️  {name.split()[-1]}: {s['rate']}% ({s['w']}-{s['l']})"
        for name, s in m["worst_parks"]
    ) if m["worst_parks"] else "   N/D"

    month_lines = "\n".join(
        f"   {mo}: {s['rate']}% ({s['w']}-{s['l']})"
        for mo, s in sorted(m["month_stats"].items())
    ) if m["month_stats"] else "   N/D"

    lines = [
        f"📊 BACKTESTING REPORT",
        f"{div}",
        f"Período:           {m['period']}",
        f"Juegos analizados: {m['games_total']}",
        f"Línea usada:       {m['book_line']} total",
        f"Apuestas:          {m['bets_made']} ({m['wins']}W-{m['losses']}L)",
        f"{div}",
        f"TOTALS MLB:",
        f"  Hit rate:  {m['hit_rate']}%  {'✅' if m['hit_rate'] >= 52.4 else '⚠️'}",
        f"  {roi_emoji} ROI:      {m['roi']:+.1f}%  (PnL: ${m['total_pnl']:+.2f})",
        f"  MAE proyección: {m['mae']} carreras",
        f"{div2}",
        f"DESGLOSE POR LADO:",
        f"  {over_arrow} OVER:  {m['over_record']} = {m['over_rate']}%",
        f"  {under_arrow} UNDER: {m['under_record']} = {m['under_rate']}%",
        f"  → {'OVER más confiable' if m['over_rate'] > m['under_rate'] else 'UNDER más confiable'}",
        f"{div2}",
        f"MEJOR ESTADIO (local):",
        best_park_lines,
        f"PEOR ESTADIO:",
        worst_park_lines,
        f"{div2}",
        f"POR MES:",
        month_lines,
        f"{div}",
        f"💡 CONCLUSIÓN:",
        f"   {verdict}",
        f"   Necesitas ≥52.4% para ganar con -110",
        f"   Precisión actual: {m['hit_rate']}%",
    ]
    return "\n".join(lines)

# ── ntfy notification ─────────────────────────────────────────────────────────

def _send_ntfy(report: str):
    if not NTFY_URL and not NTFY_TOPIC:
        return
    url = NTFY_URL or f"https://ntfy.sh/{NTFY_TOPIC}"
    try:
        requests.post(
            url,
            data=report.encode("utf-8"),
            headers={
                "Title":    "📊 Backtest Semanal",
                "Priority": "default",
                "Tags":     "chart_with_upwards_trend",
            },
            timeout=8,
        )
    except Exception:
        pass

# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    start = args[0] if len(args) >= 1 else None
    end   = args[1] if len(args) >= 2 else None
    run_backtest(start_date=start, end_date=end)
