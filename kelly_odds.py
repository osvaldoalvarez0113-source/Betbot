import requests, time, csv, os, json
from datetime import datetime, date
import pytz

# ── config ────────────────────────────────────────────────────────────────────
API_KEY  = os.environ.get("ODDS_API_KEY", "")
BANKROLL = 1000
FRACTION = 0.25      # fractional Kelly
MIN_EDGE = 2.0       # minimum edge % to alert
INTERVAL = 300       # seconds between scans (5 min)
NOTIFY   = "my-bets" # ntfy.sh topic
LOG_CSV  = True      # always log to bets_log.csv

CDT            = pytz.timezone("America/Chicago")
PREV_ODDS_FILE = "previous_odds.json"
BETS_LOG_FILE  = "bets_log.csv"

# ── season-aware leagues: sport_key -> active months ─────────────────────────
SEASON_MONTHS = {
    "soccer_fifa_world_cup": [1,2,3,4,5,6,7,8,9,10,11,12],
    "baseball_mlb":          [3,4,5,6,7,8,9,10],
}
SPORT_KEYS = list(SEASON_MONTHS.keys())

# ── runtime state (reset daily at midnight CDT) ───────────────────────────────
alerted_bets: set   = set()   # "game_id|team" keys — anti-duplicate guard
daily_bets:   list  = []      # all bets found today — for daily summary
last_reset:   date  = datetime.now(CDT).date()

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

def ntfy_post(title, body, priority="default"):
    """Central ntfy.sh POST helper."""
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
        print(f"  📲 ntfy.sh [{title}] → HTTP {resp.status_code}")
    except Exception as e:
        print(f"  ⚠️  ntfy.sh error: {e}")

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

# ── line movement ─────────────────────────────────────────────────────────────

def detect_line_movement(game_id, team, current_best, prev_map):
    """Returns (moved, direction, abs_delta) for bet annotation (threshold: 0.05 abs)."""
    key  = f"{game_id}_{team}"
    prev = prev_map.get(key)
    if prev is None:
        return False, "", 0.0
    delta = current_best - prev
    if abs(delta) >= 0.05:
        return True, ("+" if delta > 0 else "-"), round(abs(delta), 3)
    return False, "", 0.0

def detect_sharp_money(game_id, team, current_best, prev_map):
    """Returns (sharp, direction, pct_change) when odds shift >= 5% relative."""
    key  = f"{game_id}_{team}"
    prev = prev_map.get(key)
    if prev is None or prev == 0:
        return False, "", 0.0
    pct = (current_best - prev) / prev * 100
    if abs(pct) >= 5.0:
        return True, ("up" if pct > 0 else "down"), round(pct, 1)
    return False, "", 0.0

# ── analysis ──────────────────────────────────────────────────────────────────

def analyze(games, prev_map, new_map):
    bets        = []
    sharp_moves = []   # line movements >= 5% regardless of value

    for g in games:
        game_id    = g.get("id", "")
        home, away = g["home_team"], g["away_team"]
        commence   = g.get("commence_time", "")

        if game_starts_soon(commence, 60):
            continue

        bookmakers = g.get("bookmakers", [])
        if len(bookmakers) < 4:
            continue

        # Collect odds per outcome, tracking best bookmaker per team
        odds_h, odds_a = [], []
        best_bk_h = best_bk_a = ""
        for bk in bookmakers:
            for m in bk.get("markets", []):
                if m["key"] == "h2h":
                    for o in m["outcomes"]:
                        if o["name"] == home:
                            odds_h.append(o["price"])
                            if not best_bk_h or o["price"] > max(odds_h[:-1], default=0):
                                best_bk_h = bk["title"]
                        else:
                            odds_a.append(o["price"])
                            if not best_bk_a or o["price"] > max(odds_a[:-1], default=0):
                                best_bk_a = bk["title"]

        if not odds_h or not odds_a:
            continue

        best_h, best_a = max(odds_h), max(odds_a)
        avg_h  = sum(odds_h) / len(odds_h)
        avg_a  = sum(odds_a) / len(odds_a)
        fp_h, fp_a = remove_vig_multiplicative([avg_h, avg_a])

        # Check sharp money on ALL games before updating new_map
        for team, current_best in [(home, best_h), (away, best_a)]:
            sharp, direction, pct = detect_sharp_money(game_id, team, current_best, prev_map)
            if sharp:
                sharp_moves.append({
                    "match":     f"{home} vs {away}",
                    "team":      team,
                    "direction": direction,
                    "pct":       pct,
                    "odds_now":  current_best,
                    "odds_prev": prev_map.get(f"{game_id}_{team}", 0),
                })

        new_map[f"{game_id}_{home}"] = best_h
        new_map[f"{game_id}_{away}"] = best_a

        for team, prob, best_odd, side, bookmaker in [
            (home, fp_h, best_h, "HOME", best_bk_h),
            (away, fp_a, best_a, "AWAY", best_bk_a),
        ]:
            r = kelly_stake(prob, best_odd)
            if not r["has_value"] or r["edge"] < MIN_EDGE:
                continue
            moved, direction, delta = detect_line_movement(game_id, team, best_odd, prev_map)
            bets.append({
                "match":        f"{home} vs {away}",
                "team":         team,
                "side":         side,
                "odds":         best_odd,
                "edge":         r["edge"],
                "stake":        r["stake"],
                "kelly_pct":    r["kelly_pct"],
                "confidence":   confidence_level(r["edge"]),
                "time":         commence[:16],
                "line_moved":   moved,
                "line_dir":     direction,
                "line_delta":   delta,
                "game_id":      game_id,
                "bookmaker":    bookmaker,
                "market_type":  "h2h",
                "closing_edge": "",   # filled in post-game
            })

    return bets, sharp_moves

# ── notifications ─────────────────────────────────────────────────────────────

def notify_bets(new_bets):
    """Send value-bet alert, skipping any already alerted in the last 24h."""
    global alerted_bets
    fresh = [b for b in new_bets if f"{b['game_id']}|{b['team']}" not in alerted_bets]
    if not fresh:
        return

    lines = []
    for b in fresh:
        mv = f" [LINE {b['line_dir']}{b['line_delta']}]" if b["line_moved"] else ""
        lines.append(
            f"[{b['confidence']}]{mv} {b['match']}\n"
            f"  Bet: {b['team']} ({b['side']}) @ {b['odds']}\n"
            f"  Edge: {b['edge']}% | Kelly Stake: ${b['stake']}\n"
            f"  Book: {b['bookmaker']}"
        )
        alerted_bets.add(f"{b['game_id']}|{b['team']}")

    has_high = any(b["edge"] >= 5.0 for b in fresh)
    priority = "urgent" if has_high else ("high" if any(b["edge"] >= 3 for b in fresh) else "default")
    title    = f"BetBot: {len(fresh)} value bet(s)" + (" — HIGH CONFIDENCE" if has_high else "")
    ntfy_post(title, "\n\n".join(lines), priority)

def notify_sharp_money(sharp_moves):
    """Send a separate alert for any sharp money / line movement >= 5%."""
    if not sharp_moves:
        return
    lines = []
    for m in sharp_moves:
        lines.append(
            f"{m['match']} — {m['team']}\n"
            f"  Odds: {m['odds_prev']} -> {m['odds_now']} ({m['direction']} {m['pct']}%)"
        )
    ntfy_post(
        f"Sharp Money Detected: {len(sharp_moves)} line move(s)",
        "\n\n".join(lines),
        priority="high",
    )

def send_daily_summary():
    """Send a midnight CDT summary of all bets found today."""
    if not daily_bets:
        ntfy_post("BetBot Daily Summary", "No value bets found today.", "default")
        return
    total_stake  = sum(b["stake"] for b in daily_bets)
    leagues      = sorted({b.get("sport", "?") for b in daily_bets})
    high_count   = sum(1 for b in daily_bets if b["confidence"] == "HIGH")
    med_count    = sum(1 for b in daily_bets if b["confidence"] == "MEDIUM")
    body = (
        f"Total value bets: {len(daily_bets)}\n"
        f"  HIGH: {high_count}  MEDIUM: {med_count}\n"
        f"Total recommended stakes: ${round(total_stake, 2)}\n"
        f"Leagues: {', '.join(leagues)}"
    )
    ntfy_post("BetBot Daily Summary", body, "default")

# ── CSV logging ───────────────────────────────────────────────────────────────

FIELDNAMES = [
    "date", "sport", "match", "team", "side",
    "odds", "edge", "kelly_pct", "stake", "confidence",
    "bookmaker", "market_type", "closing_edge",
    "line_moved", "line_dir", "line_delta",
    "game_time", "result", "profit_loss",
]

def log_bets(bets, sport_key):
    exists = os.path.exists(BETS_LOG_FILE)
    with open(BETS_LOG_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not exists:
            w.writeheader()
        for b in bets:
            w.writerow({
                "date":          datetime.now(CDT).strftime("%Y-%m-%d %H:%M CDT"),
                "sport":         sport_key,
                "match":         b["match"],
                "team":          b["team"],
                "side":          b["side"],
                "odds":          b["odds"],
                "edge":          b["edge"],
                "kelly_pct":     b["kelly_pct"],
                "stake":         b["stake"],
                "confidence":    b["confidence"],
                "bookmaker":     b.get("bookmaker", ""),
                "market_type":   b.get("market_type", "h2h"),
                "closing_edge":  b.get("closing_edge", ""),
                "line_moved":    b["line_moved"],
                "line_dir":      b["line_dir"],
                "line_delta":    b["line_delta"],
                "game_time":     b["time"],
                "result":        "",
                "profit_loss":   "",
            })

# ── midnight reset ────────────────────────────────────────────────────────────

def check_midnight_reset():
    """At CDT midnight: send daily summary, reset state."""
    global alerted_bets, daily_bets, last_reset
    today = datetime.now(CDT).date()
    if today != last_reset:
        print(f"\n🌙 Midnight reset — sending daily summary...")
        send_daily_summary()
        alerted_bets = set()
        daily_bets   = []
        last_reset   = today

# ── main scan ─────────────────────────────────────────────────────────────────

def run_scan():
    global daily_bets
    prev_map  = load_previous_odds()
    new_map   = {}
    all_bets  = []
    all_sharp = []
    now_month = datetime.now(CDT).month

    for sport_key in SPORT_KEYS:
        if not is_in_season(sport_key):
            print(f"  ⏭  {sport_key} — off-season (month {now_month})")
            continue

        try:
            games = get_odds(sport_key)
            if not games:
                print(f"  ⚠️  {sport_key} — no data")
                continue

            bets, sharp_moves = analyze(games, prev_map, new_map)
            short = sport_key.split("_", 1)[-1].upper()

            # Attach sport label for daily summary
            for b in bets:
                b["sport"] = short

            if bets:
                print(f"\n  ✅ {short} — {len(bets)} value bet(s):")
                for b in bets:
                    mv = f" [LINE {b['line_dir']}{b['line_delta']}]" if b["line_moved"] else ""
                    print(f"    [{b['confidence']}]{mv} {b['match']} → "
                          f"{b['team']} @{b['odds']} | Edge:+{b['edge']}% | "
                          f"Stake:${b['stake']} | Book:{b['bookmaker']}")
                if LOG_CSV:
                    log_bets(bets, short)
                all_bets.extend(bets)
                daily_bets.extend(bets)
            else:
                print(f"  ❌ {short} — no value")

            if sharp_moves:
                print(f"  ⚡ {short} — {len(sharp_moves)} sharp money move(s) detected")
                all_sharp.extend(sharp_moves)

        except Exception as e:
            print(f"  ⚠️  {sport_key} error (skipping): {e}")

    prev_map.update(new_map)
    save_previous_odds(prev_map)

    if all_bets:
        notify_bets(all_bets)
    if all_sharp:
        notify_sharp_money(all_sharp)

# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🤖 BetBot Pro — starting...")
    scan = 1

    while True:
        now_cdt = datetime.now(CDT)
        print(f"\n{'='*50}\n🕐 {now_cdt.strftime('%Y-%m-%d %H:%M CDT')}")

        check_midnight_reset()

        print(f"🔍 Scan #{scan}")
        try:
            run_scan()
        except Exception as e:
            print(f"  ⚠️  Scan error (will retry next interval): {e}")

        print(f"\n⏳ Next scan in {INTERVAL // 60} min...")
        time.sleep(INTERVAL)
        scan += 1
