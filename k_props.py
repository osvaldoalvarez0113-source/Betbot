"""
k_props.py — Módulo de análisis de props de ponches (K) para abridores MLB
"""

import requests
from datetime import datetime
import csv
import os

SEASON = 2026
LEAGUE_K_PCT = 22.0
K_PROPS_EDGE_THRESHOLD = 0.05
KELLY_FRACTION = 0.25
LOG_PATH = "k_props_log.csv"

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"


def get_pitcher_season_stats(player_id: int, season: int = SEASON) -> dict:
    url = f"{MLB_STATS_BASE}/people/{player_id}/stats"
    params = {"stats": "season", "group": "pitching", "season": season}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()

    splits = data.get("stats", [{}])[0].get("splits", [])
    if not splits:
        raise ValueError(f"Sin stats de temporada para player_id {player_id}")

    stat = splits[0]["stat"]
    ip_str = stat.get("inningsPitched", "0.0")
    ip = _parse_innings(ip_str)
    games_started = stat.get("gamesStarted", 0) or 1
    strikeouts = stat.get("strikeOuts", 0)

    k9 = (strikeouts / ip) * 9 if ip > 0 else 0
    avg_ip_per_start = ip / games_started

    return {
        "k9": round(k9, 2),
        "ip": ip,
        "games_started": games_started,
        "avg_ip_per_start": round(avg_ip_per_start, 2),
        "strikeouts": strikeouts,
    }


def get_team_k_pct(team_id: int, season: int = SEASON) -> float:
    url = f"{MLB_STATS_BASE}/teams/{team_id}/stats"
    params = {"stats": "season", "group": "hitting", "season": season}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()

    splits = data.get("stats", [{}])[0].get("splits", [])
    if not splits:
        raise ValueError(f"Sin stats de bateo para team_id {team_id}")

    stat = splits[0]["stat"]
    strikeouts = stat.get("strikeOuts", 0)
    plate_appearances = stat.get("plateAppearances", 0)

    if plate_appearances == 0:
        return LEAGUE_K_PCT

    return round((strikeouts / plate_appearances) * 100, 1)


def _parse_innings(ip_str: str) -> float:
    whole, _, frac = ip_str.partition(".")
    whole = float(whole)
    frac_map = {"": 0, "0": 0, "1": 1 / 3, "2": 2 / 3}
    return whole + frac_map.get(frac, 0)


def calculate_expected_k(k9: float, avg_ip: float, rival_k_pct: float,
                          league_k_pct: float = LEAGUE_K_PCT) -> float:
    k_base = (k9 / 9) * avg_ip
    factor = 1 + (rival_k_pct - league_k_pct) * 0.015
    return round(k_base * factor, 2)


def implied_prob(odds: float) -> float:
    if abs(odds) < 100:
        raise ValueError("Cuota americana inválida")
    return -odds / (-odds + 100) if odds < 0 else 100 / (odds + 100)


def decimal_odds(odds: float) -> float:
    return 1 + 100 / -odds if odds < 0 else 1 + odds / 100


def kelly_stake(prob: float, odds: float, bankroll: float,
                fraction: float = KELLY_FRACTION) -> float:
    d = decimal_odds(odds)
    b = d - 1
    f = (b * prob - (1 - prob)) / b
    return round(max(f, 0) * fraction * bankroll, 2)


def prob_over_from_diff(diff: float) -> float:
    p = 50 + diff * 20
    return min(75, max(25, p))


def analyze_k_prop(pitcher_name: str, pitcher_id: int, rival_team_id: int,
                    line: float, side: str, odds_side: float,
                    odds_other: float = None, bankroll: float = 1000) -> dict:
    p_stats = get_pitcher_season_stats(pitcher_id)
    rival_k_pct = get_team_k_pct(rival_team_id)

    k_esperado = calculate_expected_k(p_stats["k9"], p_stats["avg_ip_per_start"], rival_k_pct)
    diff = round(k_esperado - line, 2)
    prob_over = prob_over_from_diff(diff)
    mi_prob = prob_over if side == "Over" else 100 - prob_over

    p1 = implied_prob(odds_side)
    if odds_other:
        p2 = implied_prob(odds_other)
        mercado = p1 / (p1 + p2)
        vig_quitado = True
    else:
        mercado = p1 * 0.955
        vig_quitado = False

    edge = round(mi_prob - mercado * 100, 1)
    hay_valor = edge >= (K_PROPS_EDGE_THRESHOLD * 100)
    stake = kelly_stake(mi_prob / 100, odds_side, bankroll) if hay_valor else 0

    resultado = {
        "pitcher": pitcher_name,
        "k9": p_stats["k9"],
        "avg_ip": p_stats["avg_ip_per_start"],
        "rival_k_pct": rival_k_pct,
        "k_esperado": k_esperado,
        "linea": line,
        "lado": side,
        "diferencia": diff,
        "mi_prob": round(mi_prob, 1),
        "prob_mercado": round(mercado * 100, 1),
        "vig_quitado": vig_quitado,
        "edge": edge,
        "hay_valor": hay_valor,
        "stake_sugerido": stake,
        "fecha": datetime.now().strftime("%Y-%m-%d"),
    }
    return resultado


def log_k_prop(resultado: dict):
    existe = os.path.isfile(LOG_PATH)
    with open(LOG_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=resultado.keys())
        if not existe:
            writer.writeheader()
        writer.writerow(resultado)


def format_notification(r: dict) -> str:
    veredicto = "🟢 HAY VALOR" if r["hay_valor"] else ("🟡 AL BORDE" if r["edge"] >= 0 else "🔴 SIN VALOR")
    texto = (
        f"{veredicto}\n"
        f"⚾ {r['pitcher']} — {r['lado']} {r['linea']} K\n"
        f"K esperados: {r['k_esperado']} (dif {r['diferencia']:+.2f})\n"
        f"Tu prob: {r['mi_prob']}% · Mercado: {r['prob_mercado']}% · Edge: {r['edge']:+.1f}\n"
    )
    if r["hay_valor"]:
        texto += f"💰 Stake sugerido (¼ Kelly): ${r['stake_sugerido']}\n"
    if not r["vig_quitado"]:
        texto += "⚠️ Vig estimado (sin cuota contraria)\n"
    return texto


def search_player_by_name(name: str) -> dict:
    url = f"{MLB_STATS_BASE}/people/search"
    params = {"names": name}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    people = r.json().get("people", [])

    if not people:
        raise ValueError(f"No se encontró a '{name}' en MLB Stats API")

    activos = [p for p in people if p.get("active")]
    persona = activos[0] if activos else people[0]

    return {
        "id": persona["id"],
        "full_name": persona["fullName"],
        "team_id": persona.get("currentTeam", {}).get("id"),
        "team_name": persona.get("currentTeam", {}).get("name"),
    }


def search_team_by_name(name: str) -> dict:
    url = f"{MLB_STATS_BASE}/teams"
    params = {"sportId": 1, "season": SEASON}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    teams = r.json().get("teams", [])

    coincidencias = [t for t in teams if name.lower() in t["name"].lower()]
    if not coincidencias:
        raise ValueError(f"No se encontró equipo que coincida con '{name}'")

    equipo = coincidencias[0]
    return {"id": equipo["id"], "name": equipo["name"]}


def analyze_k_prop_by_name(pitcher_name: str, rival_team_name: str,
                            line: float, side: str, odds_side: float,
                            odds_other: float = None, bankroll: float = 1000) -> dict:
    pitcher = search_player_by_name(pitcher_name)
    rival = search_team_by_name(rival_team_name)

    return analyze_k_prop(
        pitcher_name=pitcher["full_name"],
        pitcher_id=pitcher["id"],
        rival_team_id=rival["id"],
        line=line,
        side=side,
        odds_side=odds_side,
        odds_other=odds_other,
        bankroll=bankroll,
    )


def check_odds_api_pitcher_props(odds_api_key: str) -> dict:
    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events"
    r = requests.get(url, params={"apiKey": odds_api_key}, timeout=10)
    r.raise_for_status()
    events = r.json()

    if not events:
        return {"disponible": False, "razon": "Sin eventos MLB activos ahora mismo"}

    event_id = events[0]["id"]
    url_props = f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds"
    params = {
        "apiKey": odds_api_key,
        "regions": "us",
        "markets": "pitcher_strikeouts",
    }
    r2 = requests.get(url_props, params=params, timeout=10)

    if r2.status_code in (401, 422):
        return {"disponible": False, "razon": f"Plan actual no incluye pitcher_strikeouts (HTTP {r2.status_code})"}

    r2.raise_for_status()
    data = r2.json()
    tiene_mercado = any(
        m["key"] == "pitcher_strikeouts"
        for bm in data.get("bookmakers", [])
        for m in bm.get("markets", [])
    )
    return {
        "disponible": tiene_mercado,
        "razon": "Mercado encontrado" if tiene_mercado else "Plan permite la llamada pero ningún bookmaker devolvió el mercado",
    }


def get_probable_pitchers_today() -> list:
    """
    Trae los juegos de MLB de hoy con abridores confirmados via MLB Stats API.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"{MLB_STATS_BASE}/schedule"
    params = {
        "sportId": 1,
        "date": today,
        "hydrate": "probablePitcher,team",
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()

    juegos = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            game_pk = game["gamePk"]
            teams = game["teams"]
            for lado in ("home", "away"):
                equipo = teams[lado]
                rival_lado = "away" if lado == "home" else "home"
                rival = teams[rival_lado]

                probable = equipo.get("probablePitcher")
                if not probable:
                    continue

                juegos.append({
                    "game_pk": game_pk,
                    "pitcher_id": probable["id"],
                    "pitcher_name": probable["fullName"],
                    "team_id": equipo["team"]["id"],
                    "team_name": equipo["team"]["name"],
                    "rival_team_id": rival["team"]["id"],
                    "rival_team_name": rival["team"]["name"],
                    "is_home": lado == "home",
                })
    return juegos


def get_odds_api_event_id_for_game(odds_api_key: str, home_team_name: str) -> str:
    """
    Mapea un juego a un event_id de The Odds API, buscando por equipo local.
    """
    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events"
    r = requests.get(url, params={"apiKey": odds_api_key}, timeout=10)
    r.raise_for_status()
    for evento in r.json():
        if home_team_name.lower() in evento.get("home_team", "").lower():
            return evento["id"]
    return None


def get_k_prop_odds(odds_api_key: str, event_id: str, pitcher_full_name: str) -> dict:
    """
    Trae línea + cuotas Over/Under de pitcher_strikeouts para un pitcher específico.
    Devuelve None si no está disponible en ningún bookmaker.
    """
    url = f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds"
    params = {
        "apiKey": odds_api_key,
        "regions": "us",
        "markets": "pitcher_strikeouts",
        "oddsFormat": "american",
    }
    r = requests.get(url, params=params, timeout=10)
    if r.status_code != 200:
        return None

    data = r.json()
    for bookmaker in data.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market["key"] != "pitcher_strikeouts":
                continue
            outcomes_por_linea = {}
            for outcome in market.get("outcomes", []):
                if outcome.get("description") != pitcher_full_name:
                    continue
                outcomes_por_linea.setdefault(outcome["point"], {})[outcome["name"]] = outcome["price"]

            for line, precios in outcomes_por_linea.items():
                if "Over" in precios and "Under" in precios:
                    return {
                        "line": line,
                        "over_odds": precios["Over"],
                        "under_odds": precios["Under"],
                        "bookmaker": bookmaker["title"],
                    }
    return None


# Control de cuota propio del módulo — ajústalo al patrón que ya exista en kelly_odds.py
# si el Paso 1 encontró uno; si no, usa este tal cual.
_K_PROPS_MAX_DAILY_CALLS = 10
_k_props_calls_today = {"fecha": None, "contador": 0}


def _puede_llamar_odds_api() -> bool:
    hoy = datetime.now().strftime("%Y-%m-%d")
    if _k_props_calls_today["fecha"] != hoy:
        _k_props_calls_today["fecha"] = hoy
        _k_props_calls_today["contador"] = 0
    return _k_props_calls_today["contador"] < _K_PROPS_MAX_DAILY_CALLS


def _registrar_llamada_odds_api():
    _k_props_calls_today["contador"] += 1


def run_k_props_scan(odds_api_key: str, bankroll: float = 1000) -> list:
    """
    Escanea los abridores confirmados de hoy, busca su línea de ponches,
    calcula el edge, y devuelve solo los que superan el filtro de valor (5%+).
    Respeta un tope diario de llamadas para no quemar cuota de Odds API.
    """
    resultados_con_valor = []
    juegos = get_probable_pitchers_today()
    event_id_cache = {}

    for juego in juegos:
        if not _puede_llamar_odds_api():
            print("k_props: tope diario de llamadas alcanzado, deteniendo scan")
            break

        home_name = juego["team_name"] if juego["is_home"] else juego["rival_team_name"]

        if home_name not in event_id_cache:
            event_id_cache[home_name] = get_odds_api_event_id_for_game(odds_api_key, home_name)
        event_id = event_id_cache[home_name]
        if not event_id:
            continue

        _registrar_llamada_odds_api()  # una sola llamada por juego — Over y Under vienen juntos
        odds_info = get_k_prop_odds(odds_api_key, event_id, juego["pitcher_name"])
        if not odds_info:
            continue

        candidatos = []
        for lado, cuota_lado, cuota_contraria in [
            ("Over",  odds_info["over_odds"],  odds_info["under_odds"]),
            ("Under", odds_info["under_odds"], odds_info["over_odds"]),
        ]:
            try:
                resultado = analyze_k_prop(
                    pitcher_name=juego["pitcher_name"],
                    pitcher_id=juego["pitcher_id"],
                    rival_team_id=juego["rival_team_id"],
                    line=odds_info["line"],
                    side=lado,
                    odds_side=cuota_lado,
                    odds_other=cuota_contraria,
                    bankroll=bankroll,
                )
                candidatos.append(resultado)
            except Exception as e:
                print(f"k_props: error analizando {juego['pitcher_name']} ({lado}): {e}")
                continue

        if not candidatos:
            continue

        # Se queda con el lado de mayor edge, sea Over o Under
        mejor = max(candidatos, key=lambda r: r["edge"])
        log_k_prop(mejor)

        if mejor["hay_valor"]:
            resultados_con_valor.append(mejor)

    return resultados_con_valor


if __name__ == "__main__":
    resultado = analyze_k_prop_by_name(
        pitcher_name="Cristopher Sanchez",
        rival_team_name="Yankees",
        line=6.5,
        side="Over",
        odds_side=-140,
    )
    print(format_notification(resultado))
    log_k_prop(resultado)
