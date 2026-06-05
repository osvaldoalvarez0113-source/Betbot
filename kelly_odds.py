"""
BetBot Pro — Professional Multi-Module Sports Betting System
Modules: Morning Report | Lineup Monitor | Math Models | Sharp Radar | Arb Scanner
"""
import requests, time, csv, os, json, math
from datetime import datetime, date
import pytz

try:
    import statsapi
    HAS_STATSAPI = True
except ImportError:
    HAS_STATSAPI = False

# ── CONFIG ────────────────────────────────────────────────────────────────────
API_KEY  = os.environ.get("ODDS_API_KEY", "")
BANKROLL = 1000
FRACTION = 0.25
MIN_EDGE = 2.0
INTERVAL = 600        # 10-minute main scan (API limit-friendly)
NOTIFY   = "my-bets"
LOG_CSV  = True

CDT            = pytz.timezone("America/Chicago")
PREV_ODDS_FILE = "previous_odds.json"
BETS_LOG_FILE  = "bets_log.csv"
ELO_FILE       = "elo_ratings.json"
LINEUPS_FILE   = "morning_lineups.json"
MLB_YEAR       = datetime.now().year

SEASON_MONTHS = {
    "soccer_fifa_world_cup": [1,2,3,4,5,6,7,8,9,10,11,12],
    "baseball_mlb":          [3,4,5,6,7,8,9,10],
}
SPORT_KEYS = list(SEASON_MONTHS.keys())

# Bovada operates under both names depending on region
PREFERRED_BOOKS = {"bovada", "bodog"}
# Target arb pair — both US-accessible from same regions
ARB_BOOK_PAIR   = {"bovada", "bodog", "betonline.ag"}

# ── RUNTIME STATE ─────────────────────────────────────────────────────────────
alerted_bets:        set  = set()
daily_bets:          list = []
last_reset:          date = datetime.now(CDT).date()
last_morning_report: date = date(2000, 1, 1)   # force first run at 8 AM
lineup_scan_counter: int  = 0                  # increments each main scan

# ── CORE HELPERS ──────────────────────────────────────────────────────────────

def is_in_season(sport_key):
    return datetime.now(CDT).month in SEASON_MONTHS.get(sport_key, [])

def game_starts_soon(commence_str, minutes=60):
    try:
        ct   = datetime.strptime(commence_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.utc)
        diff = (ct - datetime.now(pytz.utc)).total_seconds() / 60
        return diff < minutes
    except Exception:
        return False

def remove_vig(odds_list):
    implied = [1.0 / o for o in odds_list]
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
    try:
        resp = requests.post(
            f"https://ntfy.sh/{NOTIFY}",
            data=body.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Content-Type": "text/plain"},
            timeout=10,
        )
        print(f"  📲 ntfy [{title[:40]}] → HTTP {resp.status_code}")
    except Exception as e:
        print(f"  ⚠️  ntfy error: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 3 — MATH MODELS (declared first; used by other modules)
# ═══════════════════════════════════════════════════════════════════════════════

def load_elo_ratings():
    if os.path.exists(ELO_FILE):
        try:
            with open(ELO_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_elo_ratings(ratings):
    with open(ELO_FILE, "w") as f:
        json.dump(ratings, f, indent=2)

_elo_ratings = load_elo_ratings()

def elo_win_prob(team_a, team_b):
    """Expected win probability for team_a vs team_b using ELO ratings."""
    ea = _elo_ratings.get(team_a, 1500)
    eb = _elo_ratings.get(team_b, 1500)
    return 1.0 / (1.0 + 10 ** ((eb - ea) / 400.0))

def update_elo(winner, loser, draw=False, k=32):
    """Update ELO ratings after a game result."""
    global _elo_ratings
    ea = _elo_ratings.get(winner, 1500)
    eb = _elo_ratings.get(loser,  1500)
    expected_a = 1.0 / (1.0 + 10 ** ((eb - ea) / 400.0))
    actual_a   = 0.5 if draw else 1.0
    _elo_ratings[winner] = round(ea + k * (actual_a - expected_a), 1)
    _elo_ratings[loser]  = round(eb + k * ((1 - actual_a) - (1 - expected_a)), 1)
    save_elo_ratings(_elo_ratings)

def implied_probability(odds, vig_odds_list):
    """True probability after vig removal for a given outcome."""
    probs = remove_vig(vig_odds_list)
    idx   = vig_odds_list.index(odds) if odds in vig_odds_list else 0
    return probs[idx] if idx < len(probs) else 1.0 / odds

def value_percentage(true_prob, market_odds):
    """How much value this bet has vs the market implied probability."""
    market_prob = 1.0 / market_odds
    return round((true_prob - market_prob) / market_prob * 100, 2)

def roi_projection(edge_pct, kelly_stake_amt):
    """Expected return in $ and ROI % for a single bet."""
    ev  = kelly_stake_amt * edge_pct / 100
    roi = ev / BANKROLL * 100
    return round(ev, 2), round(roi, 3)

def poisson_prob(lam, k):
    """P(Poisson(λ) = k)"""
    if lam <= 0: return 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)

def poisson_match_probs(avg_goals_home, avg_goals_away, max_goals=8):
    """Win/draw/loss probabilities via Poisson model."""
    p_win = p_draw = p_loss = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = poisson_prob(avg_goals_home, i) * poisson_prob(avg_goals_away, j)
            if i > j:    p_win  += p
            elif i == j: p_draw += p
            else:        p_loss += p
    total = p_win + p_draw + p_loss or 1
    return p_win/total, p_draw/total, p_loss/total

def pythagorean_win_prob(rs, ra, exp=1.83):
    """MLB Pythagorean expectation."""
    if rs + ra == 0: return 0.5
    return (rs ** exp) / ((rs ** exp) + (ra ** exp))

def poisson_ou_prob(expected_total, book_line, bet_over):
    """P(total > book_line) or P(total <= book_line) given Poisson mean = expected_total."""
    floor = int(book_line)
    p_under = sum(poisson_prob(expected_total, k) for k in range(floor + 1))
    p_over  = 1.0 - p_under
    # .5 lines have no push — p_under + p_over = 1 already; whole-number lines may push
    if book_line == floor:   # whole number: push possible, split push evenly
        push = poisson_prob(expected_total, floor)
        p_under -= push / 2
        p_over  -= push / 2
    return max(0.01, p_over if bet_over else p_under)

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 1 — DAILY MORNING REPORT (8 AM CDT)
# ═══════════════════════════════════════════════════════════════════════════════

def _mlb_rest(path, params=None):
    try:
        r = requests.get(f"https://statsapi.mlb.com/api/v1{path}",
                         params=params, timeout=10)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}

def fetch_mlb_games_today():
    today = datetime.now(CDT).strftime("%Y-%m-%d")
    if HAS_STATSAPI:
        try:
            return statsapi.schedule(date=today, sportId=1) or []
        except Exception:
            pass
    data = _mlb_rest("/schedule", {"sportId": 1, "date": today,
                                   "hydrate": "probablePitcher,team"})
    games = []
    for date_entry in data.get("dates", []):
        for g in date_entry.get("games", []):
            games.append(g)
    return games

def fetch_pitcher_stats(name):
    """Return dict with ERA, WHIP, K9 for a pitcher name."""
    empty = {"era": "N/A", "whip": "N/A", "k9": "N/A"}
    if not name or name == "TBD":
        return empty
    try:
        if HAS_STATSAPI:
            players = statsapi.lookup_player(name, sportId=1)
            if not players:
                return empty
            pid = players[0]["id"]
            data = statsapi.player_stat_data(pid, group="pitching", type="season")
            stats = data.get("stats", [{}])[0].get("stats", {})
        else:
            # Direct API fallback: search player
            search = _mlb_rest("/people/search", {"names": name, "sportId": 1})
            people = search.get("people", [])
            if not people: return empty
            pid   = people[0]["id"]
            data  = _mlb_rest(f"/people/{pid}/stats",
                              {"stats": "season", "group": "pitching", "season": MLB_YEAR})
            splits = data.get("stats", [{}])
            stats  = splits[0].get("splits", [{}])[-1].get("stat", {}) if splits else {}

        era  = stats.get("era",  stats.get("earnedRunAverage", "N/A"))
        whip = stats.get("whip", "N/A")
        so9  = stats.get("strikeoutsPer9Inn", stats.get("strikeoutPer9Inn", "N/A"))
        return {"era": era, "whip": whip, "k9": so9}
    except Exception:
        return empty

def fetch_team_batting(team_id):
    """Return dict with AVG and OPS for a team."""
    empty = {"avg": "N/A", "ops": "N/A", "rs_pg": 4.5, "ra_pg": 4.5}
    try:
        data = _mlb_rest(f"/teams/{team_id}/stats",
                         {"stats": "season", "group": "hitting", "season": MLB_YEAR})
        splits = data.get("stats", [{}])
        if not splits: return empty
        stat = splits[0].get("splits", [{}])[-1].get("stat", {}) if splits else {}
        return {
            "avg": stat.get("avg",  "N/A"),
            "ops": stat.get("ops",  "N/A"),
            "rs_pg": float(stat.get("runsPerGame", 4.5)),
            "ra_pg": 4.5,  # need pitching stats for this
        }
    except Exception:
        return empty

def fetch_team_pitching_ra(team_id):
    """Return runs allowed per game from team pitching stats."""
    try:
        data = _mlb_rest(f"/teams/{team_id}/stats",
                         {"stats": "season", "group": "pitching", "season": MLB_YEAR})
        splits = data.get("stats", [{}])
        stat   = splits[0].get("splits", [{}])[-1].get("stat", {}) if splits else {}
        return float(stat.get("runsAllowed", 0)) / max(float(stat.get("gamesPlayed", 1)), 1)
    except Exception:
        return 4.5

def morning_report_mlb():
    print("\n📋 MLB Morning Report...")
    games = fetch_mlb_games_today()
    if not games:
        print("  No MLB games today or statsapi unavailable.")
        return []

    lineups = {}
    for g in games:
        try:
            # Handle both statsapi dict format and raw REST format
            if "home_name" in g:
                home = g["home_name"]; away = g["away_name"]
                home_id = g.get("home_id"); away_id = g.get("away_id")
                hp_name = g.get("home_probable_pitcher", "TBD")
                ap_name = g.get("away_probable_pitcher", "TBD")
                gtime   = g.get("game_datetime", "TBD")
            else:
                home = g.get("teams", {}).get("home", {}).get("team", {}).get("name", "?")
                away = g.get("teams", {}).get("away", {}).get("team", {}).get("name", "?")
                home_id = g.get("teams", {}).get("home", {}).get("team", {}).get("id")
                away_id = g.get("teams", {}).get("away", {}).get("team", {}).get("id")
                hp      = g.get("teams", {}).get("home", {}).get("probablePitcher", {})
                ap      = g.get("teams", {}).get("away", {}).get("probablePitcher", {})
                hp_name = hp.get("fullName", "TBD")
                ap_name = ap.get("fullName", "TBD")
                gtime   = g.get("gameDate", "TBD")

            hp_stats = fetch_pitcher_stats(hp_name)
            ap_stats = fetch_pitcher_stats(ap_name)

            # Team batting
            home_bat = fetch_team_batting(home_id) if home_id else {"avg":"N/A","ops":"N/A","rs_pg":4.5,"ra_pg":4.5}
            away_bat = fetch_team_batting(away_id) if away_id else {"avg":"N/A","ops":"N/A","rs_pg":4.5,"ra_pg":4.5}

            # Runs allowed per game (from pitching stats)
            if home_id: home_bat["ra_pg"] = fetch_team_pitching_ra(home_id)
            if away_id: away_bat["ra_pg"] = fetch_team_pitching_ra(away_id)

            # Pythagorean win probability (home team)
            py_home = pythagorean_win_prob(home_bat["rs_pg"], home_bat["ra_pg"])
            py_away = pythagorean_win_prob(away_bat["rs_pg"], away_bat["ra_pg"])
            # Normalize
            total = py_home + py_away or 1
            py_home /= total; py_away /= total

            # ELO win probability
            elo_home = elo_win_prob(home, away)

            # Blend: 60% Pythagorean + 40% ELO
            win_prob_home = 0.6 * py_home + 0.4 * elo_home
            win_prob_away = 1 - win_prob_home

            # Recommendation
            if win_prob_home >= 0.60:
                rec = f"BET HOME {home} ML (conf: HIGH)"
            elif win_prob_away >= 0.60:
                rec = f"BET AWAY {away} ML (conf: HIGH)"
            elif win_prob_home >= 0.55:
                rec = f"LEAN HOME {home} ML (conf: MEDIUM)"
            elif win_prob_away >= 0.55:
                rec = f"LEAN AWAY {away} ML (conf: MEDIUM)"
            else:
                rec = "NO BET (too close)"

            body = (
                f"{away} @ {home}  |  {gtime[:16] if gtime != 'TBD' else 'TBD'}\n"
                f"Pitchers:\n"
                f"  {hp_name} (H): ERA {hp_stats['era']} | WHIP {hp_stats['whip']} | K/9 {hp_stats['k9']}\n"
                f"  {ap_name} (A): ERA {ap_stats['era']} | WHIP {ap_stats['whip']} | K/9 {ap_stats['k9']}\n"
                f"Batting:\n"
                f"  {home}: AVG {home_bat['avg']} | OPS {home_bat['ops']}\n"
                f"  {away}: AVG {away_bat['avg']} | OPS {away_bat['ops']}\n"
                f"Win Prob: {home} {win_prob_home:.1%} | {away} {win_prob_away:.1%}\n"
                f"Pythagorean: {home} {py_home:.1%}\n"
                f">>> {rec}"
            )
            ntfy_post(f"MLB Preview: {away} @ {home}", body, "default")
            print(f"  ✉️  Sent MLB preview: {away} @ {home}")

            game_key = f"{away}@{home}"
            lineups[game_key] = {"home_pitcher": hp_name, "away_pitcher": ap_name}

        except Exception as e:
            print(f"  ⚠️  MLB report error for game: {e}")

    # Save lineups for change monitoring
    with open(LINEUPS_FILE, "w") as f:
        json.dump({"date": str(datetime.now(CDT).date()), "lineups": lineups}, f)

    return lineups

def fetch_world_cup_games():
    try:
        r = requests.get(
            "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard",
            timeout=10
        )
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}

def morning_report_world_cup():
    print("\n🌍 World Cup Morning Report...")
    data   = fetch_world_cup_games()
    events = data.get("events", [])
    if not events:
        print("  No World Cup games today or API unavailable.")
        return

    for event in events:
        try:
            comp = event.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                continue

            home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
            away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

            home_name = home.get("team", {}).get("displayName", "?")
            away_name = away.get("team", {}).get("displayName", "?")
            game_time = event.get("date", "TBD")[:16]
            status    = comp.get("status", {}).get("type", {}).get("description", "")

            # Skip completed games
            if status in ("Final", "Full Time"):
                continue

            # ELO-based win probability
            elo_home = elo_win_prob(home_name, away_name)
            elo_away = 1.0 - elo_home

            # Poisson model (WC average: ~1.3 goals per team per game, slight home boost)
            avg_h = 1.35; avg_a = 1.25
            p_win, p_draw, p_loss = poisson_match_probs(avg_h, avg_a)

            # Blend ELO + Poisson
            win_h = 0.5 * elo_home + 0.5 * p_win
            win_a = 0.5 * elo_away + 0.5 * p_loss

            # Recommendation
            if win_h >= 0.55:
                rec = f"LEAN HOME {home_name} (conf: MEDIUM)"
            elif win_a >= 0.55:
                rec = f"LEAN AWAY {away_name} (conf: MEDIUM)"
            else:
                rec = "DRAW possible — consider DNB or no bet"

            body = (
                f"{away_name} vs {home_name}  |  {game_time} UTC\n"
                f"Poisson model: Home win {p_win:.1%} | Draw {p_draw:.1%} | Away {p_loss:.1%}\n"
                f"ELO: {home_name} {elo_home:.1%} | {away_name} {elo_away:.1%}\n"
                f"Blended: {home_name} {win_h:.1%} | {away_name} {win_a:.1%}\n"
                f">>> {rec}"
            )
            ntfy_post(f"WC Preview: {away_name} vs {home_name}", body, "default")
            print(f"  ✉️  Sent WC preview: {away_name} vs {home_name}")

        except Exception as e:
            print(f"  ⚠️  WC report error: {e}")

def morning_report():
    global last_morning_report
    print("\n" + "="*50)
    print(f"🌅 MORNING REPORT — {datetime.now(CDT).strftime('%Y-%m-%d %H:%M CDT')}")
    morning_report_mlb()
    morning_report_world_cup()
    last_morning_report = datetime.now(CDT).date()

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 2 — REAL-TIME LINEUP CHANGE MONITOR (every 15 min)
# ═══════════════════════════════════════════════════════════════════════════════

def load_morning_lineups():
    if not os.path.exists(LINEUPS_FILE):
        return {}
    try:
        with open(LINEUPS_FILE) as f:
            data = json.load(f)
        if data.get("date") == str(datetime.now(CDT).date()):
            return data.get("lineups", {})
    except Exception:
        pass
    return {}

def check_lineup_changes():
    print("  🔄 Checking lineup changes...")
    morning = load_morning_lineups()
    if not morning:
        return

    games = fetch_mlb_games_today()
    for g in games:
        try:
            if "home_name" in g:
                home = g["home_name"]; away = g["away_name"]
                hp_now = g.get("home_probable_pitcher", "TBD")
                ap_now = g.get("away_probable_pitcher", "TBD")
            else:
                home = g.get("teams", {}).get("home", {}).get("team", {}).get("name", "?")
                away = g.get("teams", {}).get("away", {}).get("team", {}).get("name", "?")
                hp   = g.get("teams", {}).get("home", {}).get("probablePitcher", {})
                ap   = g.get("teams", {}).get("away", {}).get("probablePitcher", {})
                hp_now = hp.get("fullName", "TBD")
                ap_now = ap.get("fullName", "TBD")

            game_key = f"{away}@{home}"
            prev = morning.get(game_key, {})

            for side, prev_p, now_p, label in [
                ("HOME", prev.get("home_pitcher","TBD"), hp_now, home),
                ("AWAY", prev.get("away_pitcher","TBD"), ap_now, away),
            ]:
                if prev_p != now_p and prev_p != "TBD" and now_p != "TBD":
                    msg = (
                        f"Game: {away} @ {home}\n"
                        f"{side} ({label}): {prev_p} → {now_p}\n"
                        f"Reassess your morning projection."
                    )
                    ntfy_post(f"PITCHER CHANGE: {away} @ {home}", msg, "urgent")
                    print(f"  🚨 Pitcher change: {away} @ {home} — {prev_p} → {now_p}")
                    # Update saved lineup
                    if game_key in morning:
                        key = "home_pitcher" if side == "HOME" else "away_pitcher"
                        morning[game_key][key] = now_p

        except Exception as e:
            print(f"  ⚠️  Lineup check error: {e}")

    # Save updated lineups
    with open(LINEUPS_FILE, "w") as f:
        json.dump({"date": str(datetime.now(CDT).date()), "lineups": morning}, f)

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 5 — ARBITRAGE SCANNER
# ═══════════════════════════════════════════════════════════════════════════════

def _check_arb(home, away, team_a, odds_a, book_a, team_b, odds_b, book_b):
    """Return an arb dict if the two odds form a valid opportunity, else None."""
    margin = (1.0 / odds_a) + (1.0 / odds_b)
    if margin >= 1.0:
        return None
    profit_pct = (1.0 - margin) / margin * 100
    if profit_pct < 0.3 or profit_pct > 8.0:
        return None
    stake_a = BANKROLL / (odds_a * margin)
    stake_b = BANKROLL / (odds_b * margin)
    return {
        "match":      f"{home} vs {away}",
        "team_a":     team_a, "odds_a": odds_a, "book_a": book_a,
        "team_b":     team_b, "odds_b": odds_b, "book_b": book_b,
        "stake_a":    round(stake_a, 2),
        "stake_b":    round(stake_b, 2),
        "profit":     round(BANKROLL * (1.0 - margin) / margin, 2),
        "profit_pct": round(profit_pct, 2),
    }

def scan_arbitrage(games):
    arbs = []
    seen = set()   # avoid duplicate alerts for same game

    for g in games:
        home, away = g["home_team"], g["away_team"]
        game_key   = f"{home}|{away}"

        # Collect best odds per team for every bookmaker in scope
        # Structure: {team: {book_name_lower: (price, display_name)}}
        book_odds: dict = {}
        for bk in g.get("bookmakers", []):
            bk_lower = bk["title"].lower()
            for m in bk.get("markets", []):
                if m["key"] == "h2h":
                    for o in m["outcomes"]:
                        team  = o["name"]
                        price = o["price"]
                        book_odds.setdefault(team, {})
                        prev = book_odds[team].get(bk_lower)
                        if prev is None or price > prev[0]:
                            book_odds[team][bk_lower] = (price, bk["title"])

        teams = list(book_odds.keys())
        if len(teams) < 2:
            continue

        team_a, team_b = teams[0], teams[1]
        books_a = book_odds[team_a]
        books_b = book_odds[team_b]

        # ── Priority: Bovada vs BetOnline.ag specifically ─────────────────────
        bov_key = next((k for k in books_a if k in PREFERRED_BOOKS), None)
        bol_key = "betonline.ag"

        if bov_key and bol_key in books_b and game_key not in seen:
            bov_price_a, bov_name = books_a[bov_key]
            bol_price_b, bol_name = books_b[bol_key]
            arb = _check_arb(home, away,
                             team_a, bov_price_a, bov_name,
                             team_b, bol_price_b, bol_name)
            if arb:
                arbs.append(arb)
                seen.add(game_key)

        if bov_key and bol_key in books_a and game_key not in seen:
            bol_price_a, bol_name = books_a.get(bol_key, (None, None))
            bov_price_b, bov_name = books_b.get(bov_key, (None, None))
            if bol_price_a and bov_price_b:
                arb = _check_arb(home, away,
                                 team_a, bol_price_a, bol_name,
                                 team_b, bov_price_b, bov_name)
                if arb:
                    arbs.append(arb)
                    seen.add(game_key)

        # ── Fallback: best available odds across all books ────────────────────
        if game_key not in seen:
            best_a = max(books_a.values(), key=lambda x: x[0])
            best_b = max(books_b.values(), key=lambda x: x[0])
            arb = _check_arb(home, away,
                             team_a, best_a[0], best_a[1],
                             team_b, best_b[0], best_b[1])
            if arb:
                arbs.append(arb)
                seen.add(game_key)

    return arbs

def notify_arbitrage(arbs):
    for i, arb in enumerate(arbs):
        if i > 0:
            time.sleep(2)   # avoid ntfy.sh HTTP 429 rate limiting
        body = (
            f"Bet ${arb['stake_a']} on {arb['team_a']} @ {arb['odds_a']} ({arb['book_a']})\n"
            f"Bet ${arb['stake_b']} on {arb['team_b']} @ {arb['odds_b']} ({arb['book_b']})\n"
            f"Guaranteed profit: ${arb['profit']} ({arb['profit_pct']}%)"
        )
        ntfy_post(f"ARB FOUND: {arb['match']}", body, "urgent")
        print(f"  💰 ARB: {arb['match']} — ${arb['profit']} profit ({arb['profit_pct']}%)")

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 6 — TOTALS (OVER/UNDER) ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

# Park factors for known MLB parks (home team name → multiplier)
MLB_PARK_FACTORS = {
    "Colorado Rockies":     1.15,
    "Cincinnati Reds":      1.08,
    "Boston Red Sox":       1.05,
    "New York Yankees":     1.04,
    "Chicago Cubs":         1.02,
    "Texas Rangers":        1.02,
    "Philadelphia Phillies": 1.01,
    "Baltimore Orioles":    1.01,
    "San Francisco Giants": 0.90,
    "Los Angeles Dodgers":  0.93,
    "Oakland Athletics":    0.95,
    "Seattle Mariners":     0.96,
    "Miami Marlins":        0.97,
}

_team_run_cache: dict = {}   # team_name -> {"rs_pg": float, "ra_pg": float}

def fetch_team_run_stats(team_name):
    """RS/RA per game for an MLB team. Cached for session."""
    if team_name in _team_run_cache:
        return _team_run_cache[team_name]
    try:
        if HAS_STATSAPI:
            teams = statsapi.lookup_team(team_name)
            if not teams:
                return None
            tid = teams[0]["id"]
        else:
            data = _mlb_rest("/teams", {"sportId": 1, "season": MLB_YEAR})
            match = next(
                (t for t in data.get("teams", [])
                 if team_name.lower() in t.get("name", "").lower()),
                None,
            )
            if not match:
                return None
            tid = match["id"]

        hit = _mlb_rest(f"/teams/{tid}/stats",
                        {"stats": "season", "group": "hitting", "season": MLB_YEAR})
        pit = _mlb_rest(f"/teams/{tid}/stats",
                        {"stats": "season", "group": "pitching", "season": MLB_YEAR})

        h_stat = (hit.get("stats", [{}])[0].get("splits", [{}]) or [{}])[-1].get("stat", {})
        p_stat = (pit.get("stats", [{}])[0].get("splits", [{}]) or [{}])[-1].get("stat", {})

        rs_pg = float(h_stat.get("runsPerGame", 4.5))
        games_p = max(float(p_stat.get("gamesPlayed", 162)), 1)
        ra_pg   = float(p_stat.get("runsAllowed", 4.5 * games_p)) / games_p

        result = {"rs_pg": round(rs_pg, 2), "ra_pg": round(ra_pg, 2)}
        _team_run_cache[team_name] = result
        return result
    except Exception:
        return None

def get_book_total(game):
    """
    Extract totals line + odds from a game's bookmaker list.
    Prefers Bovada/Bodog; falls back to first available.
    Returns (line, over_odds, under_odds, bookmaker_name) or None.
    """
    preferred, fallback = None, None
    for bk in game.get("bookmakers", []):
        is_pref = bk["title"].lower() in PREFERRED_BOOKS
        for m in bk.get("markets", []):
            if m["key"] == "totals":
                by_name = {o["name"]: o for o in m.get("outcomes", [])}
                if "Over" not in by_name or "Under" not in by_name:
                    continue
                entry = (
                    by_name["Over"]["point"],
                    by_name["Over"]["price"],
                    by_name["Under"]["price"],
                    bk["title"],
                )
                if is_pref:
                    preferred = entry
                elif fallback is None:
                    fallback = entry
    return preferred or fallback

def analyze_totals(games, sport_key):
    """Compare projected totals vs bookmaker lines; return alert dicts."""
    is_mlb    = "mlb" in sport_key
    threshold = 0.8 if is_mlb else 0.4
    edge_unit = "runs" if is_mlb else "goals"
    total_bets = []

    for g in games:
        game_id  = g.get("id", "")
        home, away = g["home_team"], g["away_team"]
        commence   = g.get("commence_time", "")

        if game_starts_soon(commence, 60):
            continue

        book_data = get_book_total(g)
        if not book_data:
            continue
        book_line, over_odds, under_odds, bookmaker = book_data

        # ── Project our total ──────────────────────────────────────────────────
        if is_mlb:
            h = fetch_team_run_stats(home)
            a = fetch_team_run_stats(away)
            if h is None or a is None:
                continue
            LEAGUE_AVG = 4.5
            home_exp  = h["rs_pg"] * (a["ra_pg"] / LEAGUE_AVG)
            away_exp  = a["rs_pg"] * (h["ra_pg"] / LEAGUE_AVG)
            park      = MLB_PARK_FACTORS.get(home, 1.0)
            our_line  = round((home_exp + away_exp) * park, 1)
        else:
            # Soccer: Poisson with ELO-adjusted averages
            elo_h  = _elo_ratings.get(home, 1500)
            elo_a  = _elo_ratings.get(away, 1500)
            adj_h  = 1.35 * (1 + (elo_h - 1500) / 4000)
            adj_a  = 1.25 * (1 + (elo_a - 1500) / 4000)
            our_line = round(adj_h + adj_a, 2)

        diff = our_line - book_line
        if abs(diff) < threshold:
            continue

        bet_over = diff > 0
        bet_side = "OVER" if bet_over else "UNDER"
        bet_odds = over_odds if bet_over else under_odds
        edge_val = round(abs(diff), 2)

        # True probability via Poisson model
        true_prob = poisson_ou_prob(our_line, book_line, bet_over)
        r = kelly_stake(true_prob, bet_odds)
        if not r["has_value"] or r["stake"] <= 0:
            continue

        conf = "HIGH" if edge_val >= threshold * 2 else "MEDIUM"
        total_bets.append({
            "match":       f"{home} vs {away}",
            "team":        bet_side,
            "side":        str(book_line),
            "odds":        bet_odds,
            "edge":        edge_val,
            "stake":       r["stake"],
            "kelly_pct":   r["kelly_pct"],
            "confidence":  conf,
            "time":        commence[:16],
            "line_moved":  False,
            "line_dir":    "",
            "line_delta":  0.0,
            "game_id":     game_id,
            "bookmaker":   bookmaker,
            "market_type": "totals",
            "closing_edge": "",
            "ev":          0,
            "roi":         0,
            "value_pct":   0,
            "elo_prob":    0,
            "bovada_odds": None,
            "book_line":   book_line,
            "our_line":    our_line,
            "edge_unit":   edge_unit,
            "sport":       sport_key.split("_", 1)[-1].upper(),
        })

    return total_bets

def notify_totals(total_bets):
    global alerted_bets
    for b in total_bets:
        key = f"{b['game_id']}|totals|{b['team']}"
        if key in alerted_bets:
            continue
        body = (
            f"{b['team']} {b['side']} | {b['match']}\n"
            f"Our Line:  {b['our_line']} {b['edge_unit']}\n"
            f"Book Line: {b['side']} {b['edge_unit']}\n"
            f"Edge:      {b['edge']} {b['edge_unit']}\n"
            f"Odds:      {b['odds']} | Stake: ${b['stake']}\n"
            f"Confidence:{b['confidence']} | Book: {b['bookmaker']}"
        )
        priority = "high" if b["confidence"] == "HIGH" else "default"
        ntfy_post(f"{b['team']} {b['side']} | {b['match']}", body, priority)
        alerted_bets.add(key)
        print(f"    🎯 {b['team']} {b['side']} {b['match']} | "
              f"Our:{b['our_line']} | Edge:{b['edge']} {b['edge_unit']}")

# ═══════════════════════════════════════════════════════════════════════════════
# CORE — ODDS FETCHING & LINE MOVEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def get_odds(sport_key):
    try:
        r = requests.get(
            f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
            params={"apiKey": API_KEY, "regions": "us,uk,eu",
                    "markets": "h2h,totals", "oddsFormat": "decimal"},
            timeout=10,
        )
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []

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

def detect_line_movement(game_id, team, current, prev_map):
    key  = f"{game_id}_{team}"
    prev = prev_map.get(key)
    if prev is None:
        return False, "", 0.0
    delta = current - prev
    if abs(delta) >= 0.05:
        return True, ("+" if delta > 0 else "-"), round(abs(delta), 3)
    return False, "", 0.0

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 4 — SHARP MONEY RADAR (enhanced)
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_sharp_money(game_id, home, away, best_h, best_a, prev_map):
    """
    Estimate sharp action by comparing line movement direction vs expected public side.
    Public typically bets favorites. If underdog line shortens (odds fall), that's sharp.
    Also flags any 5%+ relative move as sharp.
    """
    sharps = []
    for team, current, opponent_odds in [(home, best_h, best_a), (away, best_a, best_h)]:
        key  = f"{game_id}_{team}"
        prev = prev_map.get(key)
        if prev is None or prev == 0:
            continue
        pct_change = (current - prev) / prev * 100

        if abs(pct_change) < 5.0:
            continue

        is_underdog    = current > opponent_odds
        line_shortened = pct_change < 0   # odds fell = more likely to win now

        if is_underdog and line_shortened:
            public_pct = 35   # estimated: public rarely backs heavy underdogs
            sharps.append({
                "match":      f"{home} vs {away}",
                "team":       team,
                "direction":  "shortened",
                "pct":        round(abs(pct_change), 1),
                "odds_prev":  prev,
                "odds_now":   current,
                "public_pct": public_pct,
            })
        elif not is_underdog and not line_shortened:
            # Favorite drifting — sharp money going opposite
            public_pct = 65
            sharps.append({
                "match":      f"{home} vs {away}",
                "team":       team,
                "direction":  "drifted",
                "pct":        round(abs(pct_change), 1),
                "odds_prev":  prev,
                "odds_now":   current,
                "public_pct": public_pct,
            })
        else:
            # General large move
            sharps.append({
                "match":      f"{home} vs {away}",
                "team":       team,
                "direction":  "moved",
                "pct":        round(abs(pct_change), 1),
                "odds_prev":  prev,
                "odds_now":   current,
                "public_pct": None,
            })
    return sharps

def notify_sharp_money(sharp_moves):
    for m in sharp_moves:
        pub_line = (f"Public ~{m['public_pct']}% on opponent but line {m['direction']}."
                    if m["public_pct"] else f"Line {m['direction']} {m['pct']}%.")
        body = (
            f"{m['match']}\n"
            f"Team: {m['team']}\n"
            f"Odds: {m['odds_prev']} → {m['odds_now']} ({m['direction']} {m['pct']}%)\n"
            f"{pub_line}\nSharp money detected."
        )
        ntfy_post(f"SHARP ALERT: {m['team']}", body, "high")
        print(f"  ⚡ Sharp: {m['team']} in {m['match']} ({m['pct']}% move)")

# ═══════════════════════════════════════════════════════════════════════════════
# CORE — ANALYSIS & NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def analyze(games, prev_map, new_map):
    bets        = []
    sharp_moves = []

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
        best_bk_h = best_bk_a = ""
        bov_odds_h = bov_odds_a = None   # Bovada/Bodog specific odds
        for bk in bookmakers:
            is_preferred = bk["title"].lower() in PREFERRED_BOOKS
            for m in bk.get("markets", []):
                if m["key"] == "h2h":
                    for o in m["outcomes"]:
                        price = o["price"]
                        if o["name"] == home:
                            if not odds_h or price > max(odds_h):
                                best_bk_h = bk["title"]
                            odds_h.append(price)
                            if is_preferred and (bov_odds_h is None or price > bov_odds_h):
                                bov_odds_h = price
                        else:
                            if not odds_a or price > max(odds_a):
                                best_bk_a = bk["title"]
                            odds_a.append(price)
                            if is_preferred and (bov_odds_a is None or price > bov_odds_a):
                                bov_odds_a = price

        if not odds_h or not odds_a:
            continue

        best_h, best_a = max(odds_h), max(odds_a)
        avg_h  = sum(odds_h) / len(odds_h)
        avg_a  = sum(odds_a) / len(odds_a)
        fp_h, fp_a = remove_vig([avg_h, avg_a])

        # If Bovada has odds, prefer it as the displayed bookmaker
        if bov_odds_h is not None:
            best_bk_h = "Bovada"
        if bov_odds_a is not None:
            best_bk_a = "Bovada"

        # Sharp money radar check
        sharp_moves.extend(
            analyze_sharp_money(game_id, home, away, best_h, best_a, prev_map)
        )

        new_map[f"{game_id}_{home}"] = best_h
        new_map[f"{game_id}_{away}"] = best_a

        for team, prob, best_odd, side, bookmaker, bov_odds in [
            (home, fp_h, best_h, "HOME", best_bk_h, bov_odds_h),
            (away, fp_a, best_a, "AWAY", best_bk_a, bov_odds_a),
        ]:
            r = kelly_stake(prob, best_odd)
            if not r["has_value"] or r["edge"] < MIN_EDGE:
                continue

            moved, direction, delta = detect_line_movement(game_id, team, best_odd, prev_map)
            ev, roi = roi_projection(r["edge"], r["stake"])
            val_pct = value_percentage(prob, best_odd)
            elo_p   = elo_win_prob(team, away if team == home else home)

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
                "bovada_odds":  bov_odds,
                "market_type":  "h2h",
                "closing_edge": "",
                "ev":           ev,
                "roi":          roi,
                "value_pct":    val_pct,
                "elo_prob":     round(elo_p * 100, 1),
            })

    return bets, sharp_moves

def notify_bets(new_bets):
    global alerted_bets
    fresh = [b for b in new_bets if f"{b['game_id']}|{b['team']}" not in alerted_bets]
    if not fresh:
        return

    for b in fresh:
        mv   = f" [LINE {b['line_dir']}{b['line_delta']}]" if b["line_moved"] else ""
        bov  = b.get("bovada_odds")
        bov_line = (f"Bovada:     {bov}" if bov else "Bovada:     not available")
        body = (
            f"[{b['confidence']}]{mv}\n"
            f"Match:      {b['match']}\n"
            f"Bet:        {b['team']} ({b['side']}) @ {b['odds']}\n"
            f"{bov_line}\n"
            f"Best Book:  {b['bookmaker']}\n"
            f"Edge:       {b['edge']}%  (Value: {b['value_pct']}%)\n"
            f"Kelly Stake: ${b['stake']} ({b['kelly_pct']}%)\n"
            f"ELO Win Prob: {b['elo_prob']}%\n"
            f"EV: ${b['ev']}  |  Proj ROI: {b['roi']}%"
        )
        has_high = b["edge"] >= 5.0
        priority = "urgent" if has_high else ("high" if b["edge"] >= 3 else "default")
        conf_tag = " — HIGH CONFIDENCE" if has_high else ""
        ntfy_post(f"BetBot: {b['team']}{conf_tag}", body, priority)
        alerted_bets.add(f"{b['game_id']}|{b['team']}")

# ═══════════════════════════════════════════════════════════════════════════════
# CORE — CSV LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

FIELDNAMES = [
    "date", "sport", "match", "team", "side",
    "odds", "edge", "kelly_pct", "stake", "confidence",
    "bookmaker", "market_type", "closing_edge",
    "value_pct", "ev", "roi", "elo_prob",
    "book_line", "our_line", "edge_unit",
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
                "date":         datetime.now(CDT).strftime("%Y-%m-%d %H:%M CDT"),
                "sport":        sport_key,
                "match":        b["match"],
                "team":         b["team"],
                "side":         b["side"],
                "odds":         b["odds"],
                "edge":         b["edge"],
                "kelly_pct":    b["kelly_pct"],
                "stake":        b["stake"],
                "confidence":   b["confidence"],
                "bookmaker":    b.get("bookmaker", ""),
                "market_type":  b.get("market_type", "h2h"),
                "closing_edge": b.get("closing_edge", ""),
                "value_pct":    b.get("value_pct", ""),
                "ev":           b.get("ev", ""),
                "roi":          b.get("roi", ""),
                "elo_prob":     b.get("elo_prob", ""),
                "book_line":    b.get("book_line", ""),
                "our_line":     b.get("our_line", ""),
                "edge_unit":    b.get("edge_unit", ""),
                "line_moved":   b["line_moved"],
                "line_dir":     b["line_dir"],
                "line_delta":   b["line_delta"],
                "game_time":    b["time"],
                "result":       "",
                "profit_loss":  "",
            })

# ═══════════════════════════════════════════════════════════════════════════════
# CORE — DAILY SUMMARY & MIDNIGHT RESET
# ═══════════════════════════════════════════════════════════════════════════════

def send_daily_summary():
    if not daily_bets:
        ntfy_post("BetBot Daily Summary", "No value bets found today.", "default")
        return
    total_stake = sum(b["stake"] for b in daily_bets)
    total_ev    = sum(b.get("ev", 0) for b in daily_bets)
    leagues     = sorted({b.get("sport", "?") for b in daily_bets})
    high        = sum(1 for b in daily_bets if b["confidence"] == "HIGH")
    med         = sum(1 for b in daily_bets if b["confidence"] == "MEDIUM")
    body = (
        f"Total value bets: {len(daily_bets)}\n"
        f"  HIGH: {high}  MEDIUM: {med}\n"
        f"Total stakes: ${round(total_stake, 2)}\n"
        f"Total expected value: ${round(total_ev, 2)}\n"
        f"Leagues: {', '.join(leagues)}"
    )
    ntfy_post("BetBot Daily Summary", body, "default")

def check_midnight_reset():
    global alerted_bets, daily_bets, last_reset
    today = datetime.now(CDT).date()
    if today != last_reset:
        print(f"\n🌙 Midnight reset — sending daily summary...")
        send_daily_summary()
        alerted_bets = set()
        daily_bets   = []
        last_reset   = today

# ═══════════════════════════════════════════════════════════════════════════════
# CORE — MAIN SCAN
# ═══════════════════════════════════════════════════════════════════════════════

def run_scan():
    global daily_bets, lineup_scan_counter
    prev_map  = load_previous_odds()
    new_map   = {}
    all_bets   = []
    all_sharp  = []
    all_arbs   = []
    all_totals = []
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
            total_bets = analyze_totals(games, sport_key)
            arbs = scan_arbitrage(games)
            short = sport_key.split("_", 1)[-1].upper()

            for b in bets:
                b["sport"] = short

            if bets:
                print(f"\n  ✅ {short} — {len(bets)} value bet(s):")
                for b in bets:
                    mv = f" [LINE {b['line_dir']}{b['line_delta']}]" if b["line_moved"] else ""
                    print(f"    [{b['confidence']}]{mv} {b['match']} → "
                          f"{b['team']} @{b['odds']} | Edge:{b['edge']}% | "
                          f"EV:${b['ev']} | Book:{b['bookmaker']}")
                if LOG_CSV:
                    log_bets(bets, short)
                all_bets.extend(bets)
                daily_bets.extend(bets)
            else:
                print(f"  ❌ {short} — no ML value")

            if total_bets:
                print(f"  🎯 {short} — {len(total_bets)} totals bet(s):")
                if LOG_CSV:
                    log_bets(total_bets, short)
                all_totals.extend(total_bets)
                daily_bets.extend(total_bets)
            else:
                print(f"  ❌ {short} — no totals value")

            if sharp_moves:
                print(f"  ⚡ {short} — {len(sharp_moves)} sharp move(s)")
                all_sharp.extend(sharp_moves)

            if arbs:
                print(f"  💰 {short} — {len(arbs)} arb opportunity(ies)")
                all_arbs.extend(arbs)

        except Exception as e:
            print(f"  ⚠️  {sport_key} error (skipping): {e}")

    prev_map.update(new_map)
    save_previous_odds(prev_map)

    if all_bets:
        notify_bets(all_bets)
    if all_totals:
        notify_totals(all_totals)
    if all_sharp:
        notify_sharp_money(all_sharp)
    if all_arbs:
        notify_arbitrage(all_arbs)

    # Lineup check every 15 min (every 3rd 5-min scan)
    lineup_scan_counter += 1
    if lineup_scan_counter >= 3:
        check_lineup_changes()
        lineup_scan_counter = 0

# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if not HAS_STATSAPI:
        print("⚠️  MLB-statsapi not found — install via: pip install MLB-statsapi")
    print("🤖 BetBot Pro — starting...")
    scan = 1

    while True:
        now_cdt = datetime.now(CDT)
        print(f"\n{'='*50}\n🕐 {now_cdt.strftime('%Y-%m-%d %H:%M CDT')}")

        check_midnight_reset()

        # Morning report at 8 AM CDT (once per day)
        if now_cdt.hour == 8 and last_morning_report < now_cdt.date():
            try:
                morning_report()
            except Exception as e:
                print(f"  ⚠️  Morning report error: {e}")

        print(f"🔍 Scan #{scan}")
        try:
            run_scan()
        except Exception as e:
            print(f"  ⚠️  Scan error (will retry): {e}")

        print(f"\n⏳ Next scan in {INTERVAL // 60} min...")
        time.sleep(INTERVAL)
        scan += 1
