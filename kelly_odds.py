import requests, time, csv, os, json
from datetime import datetime
import pytz

# ── config (hardcoded — no CLI args) ─────────────────────────────────────────
API_KEY  = os.environ.get("ODDS_API_KEY", "")
BANKROLL = 1000
FRACTION = 0.25      # fractional Kelly
MIN_EDGE = 2.0       # minimum edge % to alert
INTERVAL = 600       # seconds between scans (10 min)
NOTIFY   = "my-bets" # ntfy.sh topic
LOG_CSV  = True      # always log to bets_log.csv

CDT            = pytz.timezone("America/Chicago")
PREV_ODDS_FILE = "previous_odds.json"
BETS_LOG_FILE  = "bets_log.csv"

# ── season-aware leagues: sport_key -> active months ─────────────────────────
SEASON_MONTHS = {
    "soccer_fifa_world_cup":        [1,2,3,4,5,6,7,8,9,10,11,12],
    "baseball_mlb":                 [3,4,5,6,7,8,9,10],
}

SPORT_KEYS = list(SEASON_MONTHS.keys())

# ── helpers ───────────────────────────────────────────────────────────────────

def is_in_season(sport_key):
    return datetime.now(CDT).month in SEASON_MONTHS.get(sport_key, [])

def game_starts_soon(commence_time_str, minutes=60):
    try:
        ct   = datetime.strptime(commence_time_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.utc)
        diff = (ct - datetime.now(pytz.utc)).total_seconds() / 60
        return diff < minutes
    except Exception:
        return False

def remove_vig_multiplicative(raw_odds_list):
    implied = [1.0 / o for o in raw_odds_list]
    total   = sum(implied)
    return [p / total for p in implied]

def kelly_stake(prob, fair_odd):
    b = fair_odd - 1
    if b <= 0:
        return {"stake": 0, "edge": 0, "has_value": False, "kelly_pct": 0}
    k     = max(0.0, (b * prob - (1 - prob)) / b)
    edge  = prob - 1.0 / fair_odd
    stake = BANKROLL * min(k * FRACTION, 0.05)
    return {
        "stake":     round(stake, 2),
        "edge":      round(edge * 100, 2),
        "has_value": edge > 0.02,
        "kelly_pct": round(k * FRACTION * 100, 2),
    }

def confidence_level(edge_pct):
    if edge_pct >= 5.0: return "HIGH"
    if edge_pct >= 3.0: return "MEDIUM"
    return "LOW"

# ── API ───────────────────────────────────────────────────────────────────────

def get_odds(sport_key):
    try:
        r = requests.get(
            f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
            params={"apiKey": API_KEY, "regions": "us,uk,eu",
                    "markets": "h2h", "oddsFormat": "decimal"},
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

def detect_line_movement(game_id, team, current_best, prev_map):
    key  = f"{game_id}_{team}"
    prev = prev_map.get(key)
    if prev is None:
        return False, "", 0.0
    delta = current_best - prev
    if abs(delta) >= 0.05:
        return True, ("↑" if delta > 0 else "↓"), round(delta, 3)
    return False, "", 0.0

# ── analysis ──────────────────────────────────────────────────────────────────

def analyze(games, prev_map, new_map):
    bets = []
    for g in games:
        game_id    = g.get("id", "")
        home, away = g["home_team"], g["away_team"]
        commence   = g.get("commence_time", "")

        if game_starts_soon(commence, 60):
            continue

        bookmakers = g.get("bookmakers", [])
        if len(bookmakers) < 4:
            continue

        odds_h, odds_a = [], []
        for bk in bookmakers:
            for m in bk.get("markets", []):
                if m["key"] == "h2h":
                    for o in m["outcomes"]:
                        (odds_h if o["name"] == home else odds_a).append(o["price"])

        if not odds_h or not odds_a:
            continue

        best_h, best_a = max(odds_h), max(odds_a)
        avg_h  = sum(odds_h) / len(odds_h)
        avg_a  = sum(odds_a) / len(odds_a)
        fp_h, fp_a = remove_vig_multiplicative([avg_h, avg_a])

        new_map[f"{game_id}_{home}"] = best_h
        new_map[f"{game_id}_{away}"] = best_a

        for team, prob, best_odd, side in [
            (home, fp_h, best_h, "HOME"),
            (away, fp_a, best_a, "AWAY"),
        ]:
            r = kelly_stake(prob, best_odd)
            if not r["has_value"] or r["edge"] < MIN_EDGE:
                continue
            moved, direction, delta = detect_line_movement(game_id, team, best_odd, prev_map)
            bets.append({
                "match":      f"{home} vs {away}",
                "team":       team,
                "side":       side,
                "odds":       best_odd,
                "edge":       r["edge"],
                "stake":      r["stake"],
                "kelly_pct":  r["kelly_pct"],
                "confidence": confidence_level(r["edge"]),
                "time":       commence[:16],
                "line_moved": moved,
                "line_dir":   direction,
                "line_delta": delta,
                "game_id":    game_id,
            })
    return bets

# ── notifications ─────────────────────────────────────────────────────────────

def notify(bets):
    if not bets:
        return
    lines = []
    for b in bets:
        move_tag = f" [LINE {b['line_dir']}{b['line_delta']}]" if b["line_moved"] else ""
        lines.append(
            f"[{b['confidence']}]{move_tag} {b['match']}\n"
            f"  {b['team']} @{b['odds']} | Edge:{b['edge']}% | Stake:${b['stake']}"
        )
    has_high = any(b["edge"] >= 5.0 for b in bets)
    priority = "urgent" if has_high else ("high" if any(b["edge"] >= 3 for b in bets) else "default")
    title    = f"BetBot: {len(bets)} value bets" + (" HIGH CONFIDENCE" if has_high else "")
    body     = "\n\n".join(lines)

    try:
        resp = requests.post(
            f"https://ntfy.sh/{NOTIFY}",
            data=body.encode("utf-8"),
            headers={
                "Title":        title,
                "Priority":     priority,
                "Content-Type": "text/plain",
            },
            timeout=10,
        )
        print(f"  📲 ntfy.sh → HTTP {resp.status_code}")
    except Exception as e:
        print(f"  ⚠️  ntfy.sh error: {e}")

# ── CSV logging ───────────────────────────────────────────────────────────────

def log_bets(bets, sport_key):
    fieldnames = [
        "date", "sport", "match", "team", "side",
        "odds", "edge", "kelly_pct", "stake", "confidence",
        "line_moved", "line_dir", "line_delta", "game_time", "result", "pnl",
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

def run_scan():
    prev_map  = load_previous_odds()
    new_map   = {}
    all_bets  = []
    now_month = datetime.now(CDT).month

    for sport_key in SPORT_KEYS:
        if not is_in_season(sport_key):
            print(f"  ⏭  {sport_key} — off-season (month {now_month})")
            continue

        games = get_odds(sport_key)
        if not games:
            print(f"  ⚠️  {sport_key} — no data")
            continue

        bets  = analyze(games, prev_map, new_map)
        short = sport_key.split("_", 1)[-1].upper()

        if bets:
            print(f"\n  ✅ {short} — {len(bets)} value bet(s):")
            for b in bets:
                mv = f" [LINE {b['line_dir']}{b['line_delta']}]" if b["line_moved"] else ""
                print(f"    [{b['confidence']}]{mv} {b['match']} → "
                      f"{b['team']} @{b['odds']} | Edge:+{b['edge']}% | Stake:${b['stake']}")
            if LOG_CSV:
                log_bets(bets, short)
            all_bets.extend(bets)
        else:
            print(f"  ❌ {short} — no value")

    prev_map.update(new_map)
    save_previous_odds(prev_map)

    if all_bets:
        notify(all_bets)

# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🤖 BetBot Pro — starting...")
    scan = 1

    while True:
        now_cdt = datetime.now(CDT)
        print(f"\n{'='*50}\n🕐 {now_cdt.strftime('%Y-%m-%d %H:%M CDT')}")

        print(f"🔍 Scan #{scan}")
        run_scan()
        print(f"\n⏳ Next scan in {INTERVAL // 60} min...")
        time.sleep(INTERVAL)
        scan += 1
