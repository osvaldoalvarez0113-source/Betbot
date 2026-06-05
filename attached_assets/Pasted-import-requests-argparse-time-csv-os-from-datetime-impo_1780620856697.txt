import requests, argparse, time, csv, os
from datetime import datetime, timedelta

API_KEY = os.environ.get("ODDS_API_KEY", "")

SPORTS = {
    "mlb":"baseball_mlb","nba":"basketball_nba",
    "nfl":"americanfootball_nfl","wc":"soccer_fifa_world_cup",
    "epl":"soccer_epl","ucl":"soccer_uefa_champions_league",
    "mls":"soccer_usa_mls","laliga":"soccer_spain_la_liga",
    "bundesliga":"soccer_germany_bundesliga",
    "seriea":"soccer_italy_serie_a","ligue1":"soccer_france_ligue_1",
    "nhl":"icehockey_nhl"
}

def kelly(p, odd, fraction=0.25, bankroll=1000, max_pct=0.05):
    b = odd - 1
    k = max(0, (b*p - (1-p)) / b)
    stake = bankroll * min(k * fraction, max_pct)
    edge = p - 1/odd
    return {"stake":round(stake,2),"edge":round(edge*100,2),"has_value":edge>0.02,"kelly_pct":round(k*fraction*100,2)}

def get_odds(sport_key):
    r = requests.get(f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
        params={"apiKey":API_KEY,"regions":"us,uk,eu","markets":"h2h","oddsFormat":"decimal"},timeout=10)
    return r.json() if r.status_code==200 else []

def analyze(games, bankroll, min_edge, fraction):
    bets = []
    for g in games:
        books = g.get("bookmakers",[])
        if not books: continue
        home, away = g["home_team"], g["away_team"]
        odds_h, odds_a = [], []
        for b in books:
            for m in b.get("markets",[]):
                if m["key"]=="h2h":
                    for o in m["outcomes"]:
                        if o["name"]==home: odds_h.append(o["price"])
                        else: odds_a.append(o["price"])
        if not odds_h or not odds_a: continue
        best_h, best_a = max(odds_h), max(odds_a)
        avg_h, avg_a = sum(odds_h)/len(odds_h), sum(odds_a)/len(odds_a)
        total = 1/avg_h + 1/avg_a
        prob_h, prob_a = (1/avg_h)/total, (1/avg_a)/total
        for team, prob, odd, side in [(home,prob_h,best_h,"HOME"),(away,prob_a,best_a,"AWAY")]:
            r = kelly(prob, odd, fraction, bankroll)
            if r["has_value"] and r["edge"] >= min_edge:
                bets.append({"match":f"{home} vs {away}","team":team,"side":side,
                    "odds":odd,"edge":r["edge"],"stake":r["stake"],"time":g.get("commence_time","")[:16]})
    return bets

def notify(topic, bets):
    if not bets or not topic: return
    body = "\n".join([f"{b['match']}\n  {b['team']} @{b['odds']} | Edge:{b['edge']}% Stake:${b['stake']}" for b in bets])
    priority = "urgent" if any(b["edge"]>=6 for b in bets) else "high" if any(b["edge"]>=3 for b in bets) else "default"
    requests.post(f"https://ntfy.sh/{topic}",data=f"{len(bets)} value bets found\n\n{body}".encode(),
        headers={"Priority":priority,"Title":f"BetBot: {len(bets)} value bets"},timeout=5)

def save_csv(file, bets, sport):
    exists = os.path.exists(file)
    with open(file,"a",newline="") as f:
        w = csv.DictWriter(f,fieldnames=["date","sport","match","team","side","odds","edge","stake","result","pnl"])
        if not exists: w.writeheader()
        for b in bets:
            w.writerow({"date":datetime.now().strftime("%Y-%m-%d %H:%M"),"sport":sport,
                "match":b["match"],"team":b["team"],"side":b["side"],
                "odds":b["odds"],"edge":b["edge"],"stake":b["stake"],"result":"","pnl":""})

def run_scan(args):
    sports = [SPORTS[s] for s in args.sports if s in SPORTS] if args.sports else list(SPORTS.values())
    all_bets = []
    for key,sport in SPORTS.items():
        if sport not in sports: continue
        games = get_odds(sport)
        bets = analyze(games, args.bankroll, args.min_edge, args.fraction)
        if bets:
            print(f"\n✅ {key.upper()} — {len(bets)} value bets:")
            for b in bets:
                print(f"  {b['match']} → {b['team']} @{b['odds']} | Edge:+{b['edge']}% | Stake:${b['stake']}")
            if args.csv: save_csv(args.csv, bets, key)
            all_bets.extend(bets)
        else:
            print(f"  ❌ {key.upper()} — no value")
    if all_bets and args.notify:
        notify(args.notify, all_bets)
    return all_bets

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--bankroll", type=float, default=1000)
    p.add_argument("--fraction", type=float, default=0.25)
    p.add_argument("--min-edge", type=float, default=0.02)
    p.add_argument("--sports", nargs="+")
    p.add_argument("--watch", action="store_true")
    p.add_argument("--interval", type=int, default=300)
    p.add_argument("--notify")
    p.add_argument("--csv")
    args = p.parse_args()

    print("🤖 BetBot Pro — iniciando...")
    scan = 1
    while True:
        print(f"\n{'='*40}\n🔍 Scan #{scan} — {datetime.now().strftime('%H:%M:%S')}")
        run_scan(args)
        if not args.watch: break
        print(f"\n⏳ Próximo scan en {args.interval//60} minutos...")
        time.sleep(args.interval)
        scan += 1
