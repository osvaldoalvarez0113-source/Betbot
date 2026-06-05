import requests, argparse, time, csv, os, json
from datetime import datetime, timedelta
import pytz

API_KEY = os.environ.get("ODDS_API_KEY", "")
CDT = pytz.timezone("America/Chicago")

# Season-aware leagues: sport_key -> list of active months (1-indexed)
SEASON_MONTHS = {
    "baseball_mlb":                    [4,5,6,7,8,9,10],
    "basketball_nba":                  [10,11,12,1,2,3,4,5,6],
    "americanfootball_nfl":            [9,10,11,12,1,2],
    "soccer_fifa_world_cup":           [6,7,11,12],
    "soccer_epl":                      [8,9,10,11,12,1,2,3,4,5],
    "soccer_uefa_champions_league":    [9,10,11,12,1,2,3,4,5],
    "soccer_usa_mls":                  [3,4,5,6,7,8,9,10,11],
    "soccer_spain_la_liga":            [8,9,10,11,12,1,2,3,4,5],
    "soccer_germany_bundesliga":       [8,9,10,11,12,1,2,3,4,5],
    "soccer_italy_serie_a":            [8,9,10,11,12,1,2,3,4,5],
    "soccer_france_ligue_1":           [8,9,10,11,12,1,2,3,4,5],
    "icehockey_nhl":                   [10,11,12,1,2,3,4,5,6],
}

SPORT_KEYS = list(SEASON_MONTHS.keys())

PREV_ODDS_FILE = "previous_odds.json"
BETS_LOG_FILE  = "bets_log.csv"

# ── helpers ──────────────────────────────────────────────────────────────────

def in_scan_window():
    """Return True if current CDT time is between 8:00 AM and 3:00 AM (next day)."""
    now_cdt = datetime.now(CDT)
    hour = now_cdt.hour
    # Active: 8 <= hour < 24  OR  0 <= hour < 3
    return hour >= 8 or hour < 3

def is_in_season(sport_key):
    month = datetime.now(CDT).month
    return month in SEASON_MONTHS.get(sport_key, [])

def game_starts_soon(commence_time_str, minutes=60):
    """Return True if game starts in less than `minutes` from now (skip it)."""
    try:
        # ISO 8601 from the API: "2025-09-14T18:00:00Z"
        ct = datetime.strptime(commence_time_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.utc)
        diff = (ct - datetime.now(pytz.utc)).total_seconds() / 60
        return diff < minutes
    except Exception:
        return False

def remove_vig_multiplicative(raw_odds_list):
    """
    Given a list of raw decimal odds for all outcomes, return fair probabilities
    using the multiplicative (proportional) vig-removal method.
    """
    implied = [1.0 / o for o in raw_odds_list]
    total   = sum(implied)
    return [p / total for p in implied]

def kelly_stake(prob, fair_odd, fraction=0.25, bankroll=1000, max_pct=0.05):
    """Fractional Kelly. Returns stake, edge %, kelly %, and whether there's value."""
    b    = fair_odd - 1
    if b <= 0:
        return {"stake": 0, "edge": 0, "has_value": False, "kelly_pct": 0}
    k     = max(0.0, (b * prob - (1 - prob)) / b)
    edge  = prob - 1.0 / fair_odd
    stake = bankroll * min(k * fraction, max_pct)
    return {
        "stake":     round(stake, 2),
        "edge":      round(edge * 100, 2),
        "has_value": edge > 0.02,
        "kelly_pct": round(k * fraction * 100, 2),
    }

def confidence_level(edge_pct):
    if edge_pct >= 5.0:
        return "HIGH"
    if edge_pct >= 3.0:
        return "MEDIUM"
    return "LOW"

# ── API ───────────────────────────────────────────────────────────────────────

def get_odds(sport_key):
    try:
        r = requests.get(
            f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
            params={
                "apiKey":     API_KEY,
                "regions":    "us,uk,eu",
                "markets":    "h2h",
                "oddsFormat": "decimal",
            },
            timeout=10,
        )
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []

# ── previous-odds persistence ─────────────────────────────────────────────────

def load_previous_odds():
    if os.path.exists(PREV_ODDS_FILE):
        try:
            with open(PREV_ODDS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_previous_odds(data):
    with open(PREV_ODDS_FILE, "w") as f:
        json.dump(data, f)

def detect_line_movement(game_id, team, current_best, prev_odds_map):
    """Return (moved: bool, direction: str, delta: float)."""
    key = f"{game_id}_{team}"
    prev = prev_odds_map.get(key)
    if prev is None:
        return False, "", 0.0
    delta = current_best - prev
    if abs(delta) >= 0.05:
        direction = "↑" if delta > 0 else "↓"
        return True, direction, round(delta, 3)
    return False, "", 0.0

def update_prev_odds_map(prev_map, game_id, team, best_odd):
    prev_map[f"{game_id}_{team}"] = best_odd

# ── analysis ──────────────────────────────────────────────────────────────────

def analyze(games, bankroll, min_edge, fraction, prev_odds_map, new_odds_map):
    bets = []
    for g in games:
        game_id     = g.get("id", "")
        home, away  = g["home_team"], g["away_team"]
        commence    = g.get("commence_time", "")

        # Skip games starting in < 60 minutes
        if game_starts_soon(commence, 60):
            continue

        bookmakers = g.get("bookmakers", [])
        # Require at least 4 bookmakers
        if len(bookmakers) < 4:
            continue

        odds_h, odds_a = [], []
        for bk in bookmakers:
            for m in bk.get("markets", []):
                if m["key"] == "h2h":
                    for o in m["outcomes"]:
                        if o["name"] == home:
                            odds_h.append(o["price"])
                        else:
                            odds_a.append(o["price"])

        if not odds_h or not odds_a:
            continue

        best_h = max(odds_h)
        best_a = max(odds_a)

        # Vig removal: use average odds across books as the "market" estimate
        avg_h = sum(odds_h) / len(odds_h)
        avg_a = sum(odds_a) / len(odds_a)
        fair_prob_h, fair_prob_a = remove_vig_multiplicative([avg_h, avg_a])

        # Update new odds snapshot
        update_prev_odds_map(new_odds_map, game_id, home, best_h)
        update_prev_odds_map(new_odds_map, game_id, away, best_a)

        for team, prob, best_odd, side in [
            (home, fair_prob_h, best_h, "HOME"),
            (away, fair_prob_a, best_a, "AWAY"),
        ]:
            r = kelly_stake(prob, best_odd, fraction, bankroll)
            if not r["has_value"] or r["edge"] < min_edge:
                continue

            moved, direction, delta = detect_line_movement(
                game_id, team, best_odd, prev_odds_map
            )

            bets.append({
                "match":       f"{home} vs {away}",
                "team":        team,
                "side":        side,
                "odds":        best_odd,
                "edge":        r["edge"],
                "stake":       r["stake"],
                "kelly_pct":   r["kelly_pct"],
                "confidence":  confidence_level(r["edge"]),
                "time":        commence[:16],
                "line_moved":  moved,
                "line_dir":    direction,
                "line_delta":  delta,
                "game_id":     game_id,
            })

    return bets

# ── notifications ─────────────────────────────────────────────────────────────

def notify(topic, bets):
    if not bets or not topic:
        return
    lines = []
    for b in bets:
        move_tag = f" [LINE {b['line_dir']}{b['line_delta']}]" if b["line_moved"] else ""
        lines.append(
            f"[{b['confidence']}]{move_tag} {b['match']}\n"
            f"  {b['team']} @{b['odds']} | Edge:{b['edge']}% | Stake:${b['stake']}"
        )
    body     = "\n".join(lines)
    has_high = any(b["edge"] >= 5.0 for b in bets)
    priority = "urgent" if has_high else "high" if any(b["edge"] >= 3 for b in bets) else "default"
    title    = f"BetBot: {len(bets)} value bets"
    if has_high:
        title += " 🔥 HIGH CONFIDENCE"
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=f"{title}\n\n{body}".encode(),
            headers={"Priority": priority, "Title": title},
            timeout=5,
        )
    except Exception:
        pass

# ── CSV logging ───────────────────────────────────────────────────────────────

def log_bets(bets, sport_key):
    fieldnames = [
        "date", "sport", "match", "team", "side",
        "odds", "edge", "kelly_pct", "stake",
        "confidence", "line_moved", "line_dir", "line_delta",
        "game_time", "result", "pnl",
    ]
    exists = os.path.exists(BETS_LOG_FILE)
    with open(BETS_LOG_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            w.writeheader()
        for b in bets:
            w.writerow({
                "date":       datetime.now(CDT).strftime("%Y-%m-%d %H:%M CDT"),
                "sport":      sport_key,
                "match":      b["match"],
                "team":       b["team"],
                "side":       b["side"],
                "odds":       b["odds"],
                "edge":       b["edge"],
                "kelly_pct":  b["kelly_pct"],
                "stake":      b["stake"],
                "confidence": b["confidence"],
                "line_moved": b["line_moved"],
                "line_dir":   b["line_dir"],
                "line_delta": b["line_delta"],
                "game_time":  b["time"],
                "result":     "",
                "pnl":        "",
            })

# ── main scan ─────────────────────────────────────────────────────────────────

def run_scan(args):
    prev_odds_map = load_previous_odds()
    new_odds_map  = {}
    all_bets      = []
    now_month     = datetime.now(CDT).month

    for sport_key in SPORT_KEYS:
        # Season gate
        if not is_in_season(sport_key):
            short = sport_key.split("_")[-1].upper()
            print(f"  ⏭  {short} — off-season (month {now_month})")
            continue

        games = get_odds(sport_key)
        if not games:
            print(f"  ⚠️  {sport_key} — no data returned")
            continue

        bets = analyze(
            games, args.bankroll, args.min_edge,
            args.fraction, prev_odds_map, new_odds_map,
        )

        short = sport_key.split("_", 1)[-1].upper()
        if bets:
            print(f"\n  ✅ {short} — {len(bets)} value bet(s):")
            for b in bets:
                move_str = f" [LINE {b['line_dir']}{b['line_delta']}]" if b["line_moved"] else ""
                print(
                    f"    [{b['confidence']}]{move_str} {b['match']} → "
                    f"{b['team']} @{b['odds']} | Edge:+{b['edge']}% | Stake:${b['stake']}"
                )
            if args.csv:
                log_bets(bets, short)
            all_bets.extend(bets)
        else:
            print(f"  ❌ {short} — no value")

    # Persist updated odds for next scan's line-movement comparison
    prev_odds_map.update(new_odds_map)
    save_previous_odds(prev_odds_map)

    if all_bets and args.notify:
        notify(args.notify, all_bets)

    return all_bets


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--bankroll",  type=float, default=1000)
    p.add_argument("--fraction",  type=float, default=0.25)   # fractional Kelly
    p.add_argument("--min-edge",  type=float, default=2.0)    # percent
    p.add_argument("--sports",    nargs="+")
    p.add_argument("--watch",     action="store_true")
    p.add_argument("--interval",  type=int,   default=900)    # 15 min default
    p.add_argument("--notify")
    p.add_argument("--csv",       action="store_true")
    args = p.parse_args()

    print("🤖 BetBot Pro — starting...")
    scan = 1

    while True:
        now_cdt = datetime.now(CDT)
        print(f"\n{'='*50}")
        print(f"🕐 {now_cdt.strftime('%Y-%m-%d %H:%M CDT')}")

        if not in_scan_window():
            next_wake = now_cdt.replace(hour=8, minute=0, second=0, microsecond=0)
            if now_cdt.hour >= 3:
                next_wake += timedelta(days=1)
            wait_sec = (next_wake - now_cdt).total_seconds()
            print(f"😴 Outside scan window (8AM–3AM CDT). Sleeping until 8AM...")
            time.sleep(max(wait_sec, 60))
            continue

        print(f"🔍 Scan #{scan}")
        run_scan(args)

        if not args.watch:
            break

        print(f"\n⏳ Next scan in {args.interval // 60} min...")
        time.sleep(args.interval)
        scan += 1
