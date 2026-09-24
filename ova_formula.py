# ova_formula.py — fórmula de OVA portada a Python, para Betbot/Railway
# Sustituye el modelo Poisson/ELO/Pythagorean por la fórmula validada con backtest.

import requests
from datetime import datetime, timedelta

MLB_API = "https://statsapi.mlb.com/api/v1"

# ---------- constantes de la fórmula (de formula-manual) ----------
PESO_ABRIDOR = 0.65
PESO_OFENSIVA = 0.50
PESO_BULLPEN = 0.25
BONUS_LOCAL = 0.12
MULTIPLICADOR = 6
TECHO_CHANCE = 0.68
PISO_CHANCE = 0.32
F5_FRAC = 0.56
KELLY_FRACCION = 0.25


def ip_a_outs(ip_str):
    """Convierte innings formato ESPN (4.2 = 4 innings y 2 outs) a outs totales."""
    s = str(ip_str)
    partes = s.split(".")
    entero = int(partes[0]) if partes[0] else 0
    frac = int(partes[1]) if len(partes) > 1 else 0
    return entero * 3 + (1 if frac == 1 else 2 if frac == 2 else 0)


def outs_a_ip(outs):
    return outs / 3


def era_mezclado(era_temporada, ip_temporada, ultimas_5):
    """70% ERA de temporada + 30% ERA de últimas 5 salidas."""
    if not ultimas_5 or ip_temporada is None or ip_temporada < 1:
        return era_temporada

    outs_total = sum(ip_a_outs(s["ip"]) for s in ultimas_5)
    er_total = sum(s["er"] for s in ultimas_5)
    ip_5 = outs_a_ip(outs_total)
    era_5 = (er_total * 9 / ip_5) if ip_5 > 0 else era_temporada

    return era_temporada * 0.7 + era_5 * 0.3


def calc_delta(era_rival, era_mio, rg_mio, rg_rival, bullpen_era_rival, bullpen_era_mio, es_local):
    delta = (
        (era_rival - era_mio) * PESO_ABRIDOR
        + (rg_mio - rg_rival) * PESO_OFENSIVA
        + (bullpen_era_rival - bullpen_era_mio) * PESO_BULLPEN
    )
    if es_local:
        delta += BONUS_LOCAL
    return delta


def calc_chance(delta, multiplicador=MULTIPLICADOR):
    chance = 0.5 + (delta * multiplicador / 100)
    return max(PISO_CHANCE, min(TECHO_CHANCE, chance))


def cuota_justa_americana(chance):
    """Convierte % de chance a línea americana justa (sin vig)."""
    if chance <= 0 or chance >= 1:
        return None
    if chance >= 0.5:
        return -round((chance / (1 - chance)) * 100)
    else:
        return round(((1 - chance) / chance) * 100)


def implicita_desde_americana(cuota):
    """Probabilidad implícita (con vig) de una cuota americana."""
    if cuota > 0:
        return 100 / (cuota + 100)
    else:
        return -cuota / (-cuota + 100)


def calc_ev(chance_real, cuota_americana):
    """EV% quitando el vig, comparando tu probabilidad real contra la cuota del book."""
    if cuota_americana > 0:
        pago_neto = cuota_americana / 100
    else:
        pago_neto = 100 / -cuota_americana
    ev = (chance_real * pago_neto) - (1 - chance_real)
    return round(ev * 100, 2)  # en %


def kelly_stake(chance_real, cuota_americana, fraccion=KELLY_FRACCION):
    """Fracción de banca a apostar, con Kelly reducido (1/4 por defecto)."""
    if cuota_americana > 0:
        b = cuota_americana / 100
    else:
        b = 100 / -cuota_americana
    q = 1 - chance_real
    kelly_completo = (chance_real * b - q) / b
    kelly_completo = max(0, kelly_completo)
    return round(kelly_completo * fraccion * 100, 2)  # en % de banca


# ---------- datos desde MLB Stats API ----------

def juegos_de_hoy(fecha=None):
    """Calendario del día con abridores probables/confirmados."""
    fecha = fecha or datetime.now().strftime("%Y-%m-%d")
    url = f"{MLB_API}/schedule?sportId=1&date={fecha}&hydrate=probablePitcher,team,linescore"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    juegos = []
    for fecha_data in data.get("dates", []):
        for g in fecha_data.get("games", []):
            juegos.append({
                "gamePk": g["gamePk"],
                "gameDate": g["gameDate"],  # UTC, para saber cuándo procesarlo
                "status": g["status"]["abstractGameState"],
                "home_id": g["teams"]["home"]["team"]["id"],
                "away_id": g["teams"]["away"]["team"]["id"],
                "home_name": g["teams"]["home"]["team"]["name"],
                "away_name": g["teams"]["away"]["team"]["name"],
                "home_pitcher": g["teams"]["home"].get("probablePitcher"),
                "away_pitcher": g["teams"]["away"].get("probablePitcher"),
            })
    return juegos


def lineup_confirmado(juego):
    """True si ya hay abridor confirmado para ambos lados (no null)."""
    return bool(juego.get("home_pitcher")) and bool(juego.get("away_pitcher"))


def era_temporada_pitcher(pitcher_id, season):
    url = f"{MLB_API}/people/{pitcher_id}/stats?stats=season&group=pitching&season={season}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    try:
        stat = data["stats"][0]["splits"][0]["stat"]
        return float(stat["era"]), float(stat["inningsPitched"])
    except (KeyError, IndexError):
        return None, None


def ultimas_5_salidas(pitcher_id, season):
    url = f"{MLB_API}/people/{pitcher_id}/stats?stats=gameLog&group=pitching&season={season}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    try:
        splits = data["stats"][0]["splits"]
    except (KeyError, IndexError):
        return []
    # gameLog viene de más reciente a más viejo normalmente; nos aseguramos
    splits_ordenados = sorted(splits, key=lambda s: s["date"], reverse=True)
    salidas = []
    for s in splits_ordenados[:5]:
        st = s["stat"]
        salidas.append({"ip": st.get("inningsPitched", "0.0"), "er": st.get("earnedRuns", 0)})
    return salidas


def stats_equipo(team_id, season):
    """R/G del equipo y ERA del bullpen."""
    url_bat = f"{MLB_API}/teams/{team_id}/stats?stats=season&group=hitting&season={season}"
    url_pit = f"{MLB_API}/teams/{team_id}/stats?stats=season&group=pitching&season={season}"
    r_bat = requests.get(url_bat, timeout=15)
    r_pit = requests.get(url_pit, timeout=15)
    r_bat.raise_for_status()
    r_pit.raise_for_status()

    rg = None
    try:
        stat_bat = r_bat.json()["stats"][0]["splits"][0]["stat"]
        runs = float(stat_bat["runs"])
        juegos = float(stat_bat["gamesPlayed"])
        rg = runs / juegos if juegos > 0 else None
    except (KeyError, IndexError):
        pass

    bullpen_era = None
    try:
        stat_pit = r_pit.json()["stats"][0]["splits"][0]["stat"]
        # ERA de equipo completo como aproximación del bullpen mientras no separemos abridores;
        # ajustar esto en la siguiente ronda si hace falta más precisión
        bullpen_era = float(stat_pit["era"])
    except (KeyError, IndexError):
        pass

    return rg, bullpen_era


# ---------- orquestador: analiza un juego completo ----------

def analizar_juego(juego, season):
    if not lineup_confirmado(juego):
        return {"gamePk": juego["gamePk"], "estado": "pendiente_lineup"}

    home_pid = juego["home_pitcher"]["id"]
    away_pid = juego["away_pitcher"]["id"]

    era_home_temp, ip_home = era_temporada_pitcher(home_pid, season)
    era_away_temp, ip_away = era_temporada_pitcher(away_pid, season)

    era_home = era_mezclado(era_home_temp, ip_home, ultimas_5_salidas(home_pid, season))
    era_away = era_mezclado(era_away_temp, ip_away, ultimas_5_salidas(away_pid, season))

    rg_home, bullpen_home = stats_equipo(juego["home_id"], season)
    rg_away, bullpen_away = stats_equipo(juego["away_id"], season)

    if None in (era_home, era_away, rg_home, rg_away, bullpen_home, bullpen_away):
        return {"gamePk": juego["gamePk"], "estado": "datos_incompletos"}

    delta_home = calc_delta(era_away, era_home, rg_home, rg_away, bullpen_away, bullpen_home, es_local=True)
    delta_away = calc_delta(era_home, era_away, rg_away, rg_home, bullpen_home, bullpen_away, es_local=False)

    chance_home = calc_chance(delta_home)
    chance_away = calc_chance(delta_away)

    return {
        "gamePk": juego["gamePk"],
        "estado": "listo",
        "home": {"nombre": juego["home_name"], "chance": round(chance_home * 100, 1), "cuota_justa": cuota_justa_americana(chance_home)},
        "away": {"nombre": juego["away_name"], "chance": round(chance_away * 100, 1), "cuota_justa": cuota_justa_americana(chance_away)},
        "f5_frac": F5_FRAC,
    }
    # Nota: EV y Kelly se calculan aparte, cuando OVA jale la cuota fresca del momento
    # (calc_ev / kelly_stake ya están listas arriba para esa parte)
