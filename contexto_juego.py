# -*- coding: utf-8 -*-
"""
contexto_juego.py — Módulo de contexto pa' Betbot
==================================================
Todo GRATIS: MLB Stats API + Open-Meteo (clima, sin API key).
No gasta tokens de Anthropic ni cuota de la Odds API.

Qué hace:
  1. Park factors (factor de carreras por estadio)
  2. Clima y viento (Open-Meteo) con ajuste a totales
  3. Lineups confirmados (detecta regulares descansando)
  4. Splits L/R del equipo vs la mano del abridor rival
  5. Descanso y viaje (jugó anoche en otra zona horaria)
  6. Uso real del bullpen (relevistas quemados últimos 2 días)

Uso desde kelly_odds.py:
    from contexto_juego import obtener_contexto, ajustar_total, ajustar_ml

    ctx = obtener_contexto(game_pk)          # dict con todo el contexto
    total_ajustado = ajustar_total(total_modelo, ctx)
    prob_home_aj, prob_away_aj = ajustar_ml(prob_home, prob_away, ctx)

Uso desde telegram_bot.py (comando /contexto):
    from contexto_juego import resumen_contexto_telegram
    texto = resumen_contexto_telegram(game_pk)
"""

import requests
from datetime import datetime, timedelta, timezone

BASE_MLB = "https://statsapi.mlb.com/api/v1"
TIMEOUT = 12

# ============================================================
# 1) PARK FACTORS
# ------------------------------------------------------------
# Factor de carreras (1.00 = neutral). Valores promedio multi-año,
# ajústalos cuando quieras. venue_id del MLB Stats API.
# roof: "abierto", "techo" (fijo) o "retractil".
# ============================================================
PARK_FACTORS = {
    # venue_id: (nombre corto, factor_carreras, roof)
    19:   ("Coors Field (COL)",          1.28, "abierto"),
    3313: ("Yankee Stadium (NYY)",       1.05, "abierto"),
    3309: ("Fenway Park (BOS)",          1.08, "abierto"),
    17:   ("Wrigley Field (CHC)",        1.02, "abierto"),   # MUY sensible al viento
    31:   ("PNC Park (PIT)",             0.95, "abierto"),
    2680: ("Petco Park (SD)",            0.93, "abierto"),
    2395: ("Oracle Park (SF)",           0.90, "abierto"),
    22:   ("Dodger Stadium (LAD)",       0.97, "abierto"),
    2602: ("Great American (CIN)",       1.10, "abierto"),
    4169: ("loanDepot park (MIA)",       0.94, "retractil"),
    12:   ("Citi Field (NYM)",           0.96, "abierto"),
    2681: ("Citizens Bank (PHI)",        1.06, "abierto"),
    3312: ("Target Field (MIN)",         1.00, "abierto"),
    4:    ("Angel Stadium (LAA)",        0.98, "abierto"),
    2392: ("Minute Maid/Daikin (HOU)",   1.03, "retractil"),
    680:  ("T-Mobile Park (SEA)",        0.92, "retractil"),
    14:   ("Rogers Centre (TOR)",        1.02, "retractil"),
    15:   ("Chase Field (AZ)",           1.04, "retractil"),
    16:   ("Comerica Park (DET)",        0.97, "abierto"),
    7:    ("Kauffman Stadium (KC)",      1.01, "abierto"),
    32:   ("Nationals Park (WSH)",       1.00, "abierto"),
    2:    ("Oriole Park (BAL)",          1.02, "abierto"),
    5325: ("Globe Life Field (TEX)",     0.99, "retractil"),
    4705: ("Truist Park (ATL)",          1.02, "abierto"),
    3289: ("Busch Stadium (STL)",        0.97, "abierto"),
    2287: ("American Family (MIL)",      1.01, "retractil"),
    5:    ("Guaranteed Rate/Rate (CWS)", 1.04, "abierto"),
    2394: ("Progressive Field (CLE)",    0.99, "abierto"),
    10:   ("Coliseo/Sutter (ATH)",       0.96, "abierto"),
    12266:("Steinbrenner (TB temp)",     1.03, "abierto"),
    2523: ("Tropicana (TB)",             0.96, "techo"),
}

# Coordenadas de estadios pa'l clima (lat, lon)
STADIUM_COORDS = {
    19: (39.7559, -104.9942), 3313: (40.8296, -73.9262),
    3309: (42.3467, -71.0972), 17: (41.9484, -87.6553),
    31: (40.4469, -80.0057), 2680: (32.7076, -117.1570),
    2395: (37.7786, -122.3893), 22: (34.0739, -118.2400),
    2602: (39.0975, -84.5066), 4169: (25.7781, -80.2196),
    12: (40.7571, -73.8458), 2681: (39.9061, -75.1665),
    3312: (44.9817, -93.2776), 4: (33.8003, -117.8827),
    2392: (29.7573, -95.3555), 680: (47.5914, -122.3325),
    14: (43.6414, -79.3894), 15: (33.4455, -112.0667),
    16: (42.3390, -83.0485), 7: (39.0517, -94.4803),
    32: (38.8730, -77.0074), 2: (39.2838, -76.6217),
    5325: (32.7473, -97.0847), 4705: (33.8908, -84.4678),
    3289: (38.6226, -90.1928), 2287: (43.0280, -87.9712),
    5: (41.8299, -87.6338), 2394: (41.4962, -81.6852),
    10: (38.5810, -121.5130), 12266: (27.9803, -82.5067),
    2523: (27.7683, -82.6534),
}


def _get(url, params=None):
    """GET con manejo de errores. Devuelve dict o None."""
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[contexto_juego] Error GET {url}: {e}")
        return None


# ============================================================
# 2) CLIMA Y VIENTO (Open-Meteo, gratis, sin key)
# ============================================================
def get_clima(venue_id, game_dt_utc):
    """
    Clima pronosticado a la hora del juego.
    Devuelve dict: temp_f, viento_mph, direccion_viento, lluvia_prob
    o None si el estadio tiene techo cerrado / no hay datos.
    """
    info = PARK_FACTORS.get(venue_id)
    if info and info[2] == "techo":
        return {"techo_cerrado": True}

    coords = STADIUM_COORDS.get(venue_id)
    if not coords:
        return None

    lat, lon = coords
    data = _get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,precipitation_probability",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "UTC",
        "forecast_days": 3,
    })
    if not data or "hourly" not in data:
        return None
    try:
        horas = data["hourly"]["time"]
        objetivo = game_dt_utc.strftime("%Y-%m-%dT%H:00")
        if objetivo not in horas:
            return None
        i = horas.index(objetivo)
        return {
            "techo_cerrado": False,
            "retractil": bool(info and info[2] == "retractil"),
            "temp_f": data["hourly"]["temperature_2m"][i],
            "viento_mph": data["hourly"]["wind_speed_10m"][i],
            "direccion_viento": data["hourly"]["wind_direction_10m"][i],
            "lluvia_prob": data["hourly"]["precipitation_probability"][i],
        }
    except Exception as e:
        print(f"[contexto_juego] Error parseando clima: {e}")
        return None


def factor_clima(clima):
    """
    Convierte el clima en un multiplicador de carreras.
    Simple y conservador: temperatura + viento.
    """
    if not clima or clima.get("techo_cerrado"):
        return 1.00
    f = 1.00
    t = clima.get("temp_f")
    v = clima.get("viento_mph")
    if t is not None:
        if t >= 90:   f *= 1.05
        elif t >= 80: f *= 1.02
        elif t <= 55: f *= 0.96
    if v is not None and v >= 12:
        # Sin orientación exacta del estadio no sabemos si sale o entra;
        # viento fuerte = más varianza. Ajuste leve y simétrico:
        # solo marcamos bandera pa' que el panel lo considere.
        pass
    return round(f, 3)


# ============================================================
# 3) LINEUPS CONFIRMADOS
# ============================================================
def get_lineups(game_pk):
    """
    Lineups del boxscore live. Antes del juego, MLB los publica
    ~2-4h antes del primer pitch. Devuelve dict:
    { 'home': [ids...], 'away': [ids...], 'confirmado_home': bool, ... }
    """
    data = _get(f"{BASE_MLB}/game/{game_pk}/boxscore")
    if not data:
        return None
    out = {}
    for lado in ("home", "away"):
        try:
            batting = data["teams"][lado].get("battingOrder", [])
            out[lado] = batting
            out[f"confirmado_{lado}"] = len(batting) >= 9
        except Exception:
            out[lado] = []
            out[f"confirmado_{lado}"] = False
    return out


def detectar_descansos(game_pk, team_id, lado):
    """
    Compara el lineup de hoy con los titulares más usados del equipo.
    Devuelve cantidad estimada de regulares descansando (0 si no
    hay lineup confirmado todavía).
    """
    lineups = get_lineups(game_pk)
    if not lineups or not lineups.get(f"confirmado_{lado}"):
        return None  # aún no hay lineup

    hoy = set(lineups[lado])

    # Titulares habituales: jugadores con más juegos iniciados
    season = datetime.now().year
    data = _get(f"{BASE_MLB}/teams/{team_id}/roster/active")
    if not data:
        return None
    # Aproximación: usamos los últimos 5 lineups del equipo
    fin = datetime.now(timezone.utc).date()
    inicio = fin - timedelta(days=8)
    sched = _get(f"{BASE_MLB}/schedule", params={
        "sportId": 1, "teamId": team_id,
        "startDate": str(inicio), "endDate": str(fin - timedelta(days=1)),
    })
    if not sched:
        return None
    frecuencia = {}
    juegos = 0
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            box = _get(f"{BASE_MLB}/game/{g['gamePk']}/boxscore")
            if not box:
                continue
            for l in ("home", "away"):
                if box["teams"][l]["team"]["id"] == team_id:
                    for pid in box["teams"][l].get("battingOrder", []):
                        frecuencia[pid] = frecuencia.get(pid, 0) + 1
                    juegos += 1
    if juegos < 3:
        return None
    # Regulares = jugaron en 70%+ de los últimos juegos
    regulares = {pid for pid, n in frecuencia.items() if n / juegos >= 0.7}
    descansando = len(regulares - hoy)
    return descansando


# ============================================================
# 4) SPLITS L/R DEL EQUIPO vs MANO DEL ABRIDOR
# ============================================================
def get_split_equipo(team_id, mano_pitcher, season=None):
    """
    OPS del equipo vs zurdos ('L') o derechos ('R') esta temporada.
    Devuelve float OPS o None.
    """
    season = season or datetime.now().year
    sit = "vl" if mano_pitcher == "L" else "vr"
    data = _get(f"{BASE_MLB}/teams/{team_id}/stats", params={
        "stats": "statSplits", "sitCodes": sit,
        "group": "hitting", "season": season, "sportIds": 1,
    })
    try:
        splits = data["stats"][0]["splits"]
        if not splits:
            return None
        return float(splits[0]["stat"].get("ops", 0)) or None
    except Exception:
        return None


def get_mano_abridor(game_pk, lado):
    """Mano ('L'/'R') del abridor probable del lado dado."""
    data = _get(f"{BASE_MLB}/schedule", params={
        "sportId": 1, "gamePk": game_pk, "hydrate": "probablePitcher",
    })
    try:
        game = data["dates"][0]["games"][0]
        pit = game["teams"][lado].get("probablePitcher")
        if not pit:
            return None, None
        pid = pit["id"]
        pdata = _get(f"{BASE_MLB}/people/{pid}")
        mano = pdata["people"][0]["pitchHand"]["code"]
        return pit.get("fullName"), mano
    except Exception:
        return None, None


# ============================================================
# 5) DESCANSO Y VIAJE
# ============================================================
def get_descanso(team_id):
    """
    Devuelve dict:
      jugo_ayer (bool), fue_nocturno (bool), cambio_zona (bool aprox.)
    """
    ayer = datetime.now(timezone.utc).date() - timedelta(days=1)
    sched = _get(f"{BASE_MLB}/schedule", params={
        "sportId": 1, "teamId": team_id,
        "startDate": str(ayer), "endDate": str(ayer),
    })
    out = {"jugo_ayer": False, "fue_nocturno": False}
    try:
        games = sched["dates"][0]["games"]
        if games:
            out["jugo_ayer"] = True
            hora = games[0].get("gameDate", "")
            # gameDate viene en UTC; 23:00Z+ suele ser juego nocturno US
            if hora:
                h = int(hora[11:13])
                out["fue_nocturno"] = h >= 23 or h <= 4
    except Exception:
        pass
    return out


# ============================================================
# 6) USO REAL DEL BULLPEN (relevistas quemados)
# ============================================================
def get_bullpen_quemado(team_id):
    """
    Relevistas que lanzaron AYER y ANTEAYER (nombres).
    Devuelve dict: {'ayer': [...], 'dos_dias_seguidos': [...]}
    """
    hoy = datetime.now(timezone.utc).date()
    uso = {}  # pid -> set(fechas)
    nombres = {}
    for delta in (1, 2):
        dia = hoy - timedelta(days=delta)
        sched = _get(f"{BASE_MLB}/schedule", params={
            "sportId": 1, "teamId": team_id,
            "startDate": str(dia), "endDate": str(dia),
        })
        try:
            for g in sched["dates"][0]["games"]:
                box = _get(f"{BASE_MLB}/game/{g['gamePk']}/boxscore")
                for l in ("home", "away"):
                    eq = box["teams"][l]
                    if eq["team"]["id"] != team_id:
                        continue
                    for pid_str, pdata in eq["players"].items():
                        st = pdata.get("stats", {}).get("pitching", {})
                        if st and st.get("inningsPitched") not in (None, "0.0"):
                            pid = pdata["person"]["id"]
                            # excluir abridor (gamesStarted)
                            if st.get("gamesStarted", 0) == 0:
                                uso.setdefault(pid, set()).add(delta)
                                nombres[pid] = pdata["person"]["fullName"]
        except Exception:
            continue
    ayer = [nombres[p] for p, ds in uso.items() if 1 in ds]
    seguidos = [nombres[p] for p, ds in uso.items() if 1 in ds and 2 in ds]
    return {"ayer": ayer, "dos_dias_seguidos": seguidos}


# ============================================================
# CONTEXTO COMPLETO + AJUSTES
# ============================================================
def obtener_contexto(game_pk):
    """Junta TODO el contexto de un juego en un solo dict."""
    sched = _get(f"{BASE_MLB}/schedule", params={
        "sportId": 1, "gamePk": game_pk, "hydrate": "probablePitcher,venue",
    })
    try:
        game = sched["dates"][0]["games"][0]
    except Exception:
        return None

    venue_id = game.get("venue", {}).get("id")
    game_dt = datetime.fromisoformat(game["gameDate"].replace("Z", "+00:00"))
    home_id = game["teams"]["home"]["team"]["id"]
    away_id = game["teams"]["away"]["team"]["id"]

    park = PARK_FACTORS.get(venue_id, ("Desconocido", 1.00, "abierto"))
    clima = get_clima(venue_id, game_dt)

    p_away_nombre, p_away_mano = get_mano_abridor(game_pk, "away")
    p_home_nombre, p_home_mano = get_mano_abridor(game_pk, "home")

    ctx = {
        "game_pk": game_pk,
        "venue": park[0],
        "park_factor": park[1],
        "roof": park[2],
        "clima": clima,
        "factor_clima": factor_clima(clima),
        "home_id": home_id, "away_id": away_id,
        "abridor_home": p_home_nombre, "mano_home": p_home_mano,
        "abridor_away": p_away_nombre, "mano_away": p_away_mano,
        # OPS del lineup de cada equipo vs la mano del abridor RIVAL
        "ops_home_vs_abridor": get_split_equipo(home_id, p_away_mano) if p_away_mano else None,
        "ops_away_vs_abridor": get_split_equipo(away_id, p_home_mano) if p_home_mano else None,
        "descanso_home": get_descanso(home_id),
        "descanso_away": get_descanso(away_id),
        "bullpen_home": get_bullpen_quemado(home_id),
        "bullpen_away": get_bullpen_quemado(away_id),
        "regulares_fuera_home": detectar_descansos(game_pk, home_id, "home"),
        "regulares_fuera_away": detectar_descansos(game_pk, away_id, "away"),
    }
    return ctx


def ajustar_total(total_modelo, ctx):
    """Ajusta el total proyectado por park factor y clima."""
    if not ctx:
        return total_modelo
    f = ctx.get("park_factor", 1.0) * ctx.get("factor_clima", 1.0)
    # Amortiguamos: solo aplicamos 60% del ajuste bruto pa' no sobreajustar
    f_suave = 1 + (f - 1) * 0.6
    return round(total_modelo * f_suave, 2)


def ajustar_ml(prob_home, prob_away, ctx):
    """
    Ajuste leve al ML por splits L/R, descansos y regulares fuera.
    Máximo ±3% de movimiento — el modelo principal sigue mandando.
    """
    if not ctx:
        return prob_home, prob_away
    delta = 0.0

    # Splits: si un lineup tiene ventaja clara de OPS vs la mano rival
    oh, oa = ctx.get("ops_home_vs_abridor"), ctx.get("ops_away_vs_abridor")
    if oh and oa:
        dif = oh - oa
        delta += max(min(dif * 0.10, 0.015), -0.015)

    # Regulares descansando (cada uno resta ~0.8%)
    rf_h = ctx.get("regulares_fuera_home") or 0
    rf_a = ctx.get("regulares_fuera_away") or 0
    delta -= 0.008 * rf_h
    delta += 0.008 * rf_a

    # Viaje/descanso: visitante jugó anoche de noche y el local no
    dh, da = ctx.get("descanso_home", {}), ctx.get("descanso_away", {})
    if da.get("fue_nocturno") and not dh.get("jugo_ayer"):
        delta += 0.008
    if dh.get("fue_nocturno") and not da.get("jugo_ayer"):
        delta -= 0.008

    delta = max(min(delta, 0.03), -0.03)
    ph = min(max(prob_home + delta, 0.01), 0.99)
    return round(ph, 4), round(1 - ph, 4)


# ============================================================
# COMANDO /contexto PA' TELEGRAM
# ============================================================
def resumen_contexto_telegram(game_pk):
    """Texto listo pa' mandar por Telegram."""
    ctx = obtener_contexto(game_pk)
    if not ctx:
        return "No pude obtener el contexto de ese juego."

    lineas = [f"🏟 CONTEXTO — {ctx['venue']}"]
    lineas.append(f"Park factor: {ctx['park_factor']:.2f} ({'favorece carreras' if ctx['park_factor'] > 1.03 else 'favorece pitcheo' if ctx['park_factor'] < 0.97 else 'neutral'})")

    c = ctx.get("clima")
    if c and c.get("techo_cerrado"):
        lineas.append("Clima: techo cerrado, no aplica")
    elif c:
        lineas.append(f"Clima: {c['temp_f']:.0f}°F, viento {c['viento_mph']:.0f} mph, lluvia {c['lluvia_prob']}%")
        if c.get("viento_mph", 0) >= 12:
            lineas.append("⚠️ Viento fuerte: más varianza en el total")
        if c.get("lluvia_prob", 0) >= 50:
            lineas.append("⚠️ Riesgo de lluvia/delay")

    if ctx["abridor_away"] and ctx["abridor_home"]:
        lineas.append(f"Abridores: {ctx['abridor_away']} ({ctx['mano_away']}) vs {ctx['abridor_home']} ({ctx['mano_home']})")

    if ctx["ops_home_vs_abridor"] and ctx["ops_away_vs_abridor"]:
        lineas.append(f"OPS vs mano rival: away {ctx['ops_away_vs_abridor']:.3f} | home {ctx['ops_home_vs_abridor']:.3f}")

    for lado, tag in (("away", "Visita"), ("home", "Local")):
        rf = ctx.get(f"regulares_fuera_{lado}")
        if rf:
            lineas.append(f"⚠️ {tag}: ~{rf} regular(es) descansando")
        bp = ctx.get(f"bullpen_{lado}", {})
        if bp.get("dos_dias_seguidos"):
            lineas.append(f"🔥 Bullpen {tag} quemado: {', '.join(bp['dos_dias_seguidos'][:4])} (2 días seguidos)")

    return "\n".join(lineas)


if __name__ == "__main__":
    # Prueba rápida: busca el primer juego de hoy y muestra su contexto
    hoy = datetime.now(timezone.utc).date()
    sched = _get(f"{BASE_MLB}/schedule", params={
        "sportId": 1, "startDate": str(hoy), "endDate": str(hoy),
    })
    try:
        gp = sched["dates"][0]["games"][0]["gamePk"]
        print(resumen_contexto_telegram(gp))
    except Exception:
        print("No hay juegos hoy pa' probar (All-Star break).")
