"""
ova_formula_servidor.py
========================
Paquete completo: la fórmula de OVA (calculadora "Ventaja") portada a Python,
con todo lo que le faltaba al servidor para poder usarla igual que la app:

  1. ERA real de liga (en vez de un número fijo) — ligaEra() de OVA.
  2. Carreras por juego del equipo en los últimos 14 días (mínimo 5 juegos) —
     ofensiva14() de OVA.
  3. eraUsado() — regresión en dos capas (FIP-ancla, luego ERA/últimas 5).
  4. rgUsado() — mezcla 70% temporada / 30% últimos 14 días.
  5. calcular_chance_ova() — arma todo el cálculo de "ventaja" de OVA y
     devuelve el % de victoria del local, ya con el tope 22–78% aplicado.

Pensado para pegarse tal cual en el servidor de Railway (o importarse como
módulo). Usa solo `requests` y cachea en memoria por día — mismo patrón que
kelly_odds.py, así no duplicas llamadas dentro del mismo scan.

Uso rápido al final del archivo, en el bloque __main__.
"""

import requests
from datetime import datetime, date, timedelta

API = "https://statsapi.mlb.com/api/v1"

# ── Constantes que replican exactamente los valores por defecto de OVA ───────
PRIOR_ERA = 40.0          # innings de "prior" para el ERA regresado — sin medir, es el default de OVA
LIGA_ERA_FALLBACK = 4.10  # si por algún motivo no se puede calcular la liga real
MULT = 6                  # multiplicador carreras→porcentaje, medido en OVA sobre 3,077 juegos
CORTE_MIN_PCT = 22.0
CORTE_MAX_PCT = 78.0

# ── Caché en memoria, una entrada por día (se resetea sola al cambiar de día) ─
_cache = {
    "liga_era": {},     # {"YYYY-MM-DD": float}
    "rg14": {},          # {"team_id|YYYY-MM-DD": float|None}
}


def _hoy_str():
    return datetime.now().strftime("%Y-%m-%d")


def _get(url, params=None, timeout=10):
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ══════════════════════════════════════════════════════════════════════════
# 1. ERA REAL DE LIGA — puerto de ligaEra() de OVA
# ══════════════════════════════════════════════════════════════════════════
def liga_era(year: int = None) -> float:
    """
    ERA real de toda la liga en lo que va de temporada:
        ERA_liga = (suma de carreras limpias de TODOS los equipos × 9)
                   / (suma de innings lanzados de TODOS los equipos)
    Cacheado por día — se pide una sola vez por scan/día, no por juego.
    Cae a LIGA_ERA_FALLBACK si la API falla.
    """
    year = year or datetime.now().year
    hoy = _hoy_str()
    if hoy in _cache["liga_era"]:
        return _cache["liga_era"][hoy]
    try:
        d = _get(f"{API}/teams/stats", {
            "stats": "season", "group": "pitching",
            "season": year, "sportIds": 1, "gameType": "R",
        })
        splits = (d.get("stats") or [{}])[0].get("splits") or []
        er = ip = 0.0
        for s in splits:
            st = s.get("stat", {})
            er += float(st.get("earnedRuns", 0) or 0)
            ip += _ip_to_num(st.get("inningsPitched", 0))
        valor = (er * 9 / ip) if ip > 0 else LIGA_ERA_FALLBACK
    except Exception as e:
        print(f"  ⚠️  liga_era() falló, usando fallback {LIGA_ERA_FALLBACK}: {e}")
        valor = LIGA_ERA_FALLBACK
    _cache["liga_era"][hoy] = valor
    return valor


def _ip_to_num(ip) -> float:
    """'6.2' (formato MLB, 2 outs) → 6.667 innings decimales."""
    if ip is None:
        return 0.0
    try:
        n = float(ip)
    except (TypeError, ValueError):
        return 0.0
    entero = int(n)
    dec = round((n - entero) * 10)
    frac = {1: 1 / 3, 2: 2 / 3}.get(dec, 0.0)
    return entero + frac


# ══════════════════════════════════════════════════════════════════════════
# 2. CARRERAS POR JUEGO — ÚLTIMOS 14 DÍAS — puerto de ofensiva14() de OVA
# ══════════════════════════════════════════════════════════════════════════
def rg_ultimos_14_dias(team_id: int, fecha_base: date, year: int = None) -> "float | None":
    """
    Carreras por juego del equipo en los últimos 14 días antes de fecha_base.
    Devuelve None si jugó menos de 5 juegos en esa ventana (igual que OVA:
    con muestra tan chica el número no significa nada y rg_usado_ova() debe
    caer solo a temporada completa).
    """
    year = year or fecha_base.year
    ck = f"{team_id}|{fecha_base.isoformat()}"
    if ck in _cache["rg14"]:
        return _cache["rg14"][ck]

    ini = (fecha_base - timedelta(days=14)).strftime("%Y-%m-%d")
    fin = (fecha_base - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        d = _get(f"{API}/teams/{team_id}/stats", {
            "stats": "byDateRange", "startDate": ini, "endDate": fin,
            "group": "hitting", "season": year, "gameType": "R",
        })
        s = (d.get("stats") or [{}])[0].get("splits") or [{}]
        st = s[0].get("stat", {}) if s else {}
        g = int(st.get("gamesPlayed", 0) or 0)
        r = float(st.get("runs", 0) or 0)
        valor = (r / g) if g >= 5 else None
    except Exception as e:
        print(f"  ⚠️  rg_ultimos_14_dias({team_id}) falló: {e}")
        valor = None
    _cache["rg14"][ck] = valor
    return valor


# ══════════════════════════════════════════════════════════════════════════
# 3. ERA USADO — regresión en dos capas — puerto EXACTO de eraUsado() de OVA
# ══════════════════════════════════════════════════════════════════════════
def _reg(era, ip, prior, ancla, liga):
    """Regresión bayesiana simple: jala 'era' hacia 'ancla' según cuántos
    innings (ip) hay detrás del número, frente a 'prior' innings de liga."""
    if era is None or ip is None:
        return None
    L = ancla if ancla is not None else liga
    return (era * ip + L * prior) / (ip + prior)


def era_usado_ova(era_temporada, ip_temporada, fip_temporada,
                   era_last5, ip_last5, liga: float = None) -> "float | None":
    """
    Puerto exacto de eraUsado() de OVA (modo 'mix', el default de la app):

      Capa 1 (ancla): el FIP de temporada se regresa hacia la liga con
                       prior = 30 innings. Ese resultado es el "ancla".
      Capa 2: tanto el ERA de temporada (prior=40) como el ERA de últimas 5
              salidas (prior=40×0.625=25) se regresan hacia ESE ancla,
              no hacia la liga directo.
      Capa 3: mezcla 70% temporada-regresado + 30% últimas5-regresado.

    Ejemplo real (Kyle Leahy, del caso que comparamos): con pocos innings el
    prior de 40 domina sobre el ERA crudo, y el ancla ya no es un 4.10 fijo
    sino el FIP regresado del propio pitcher — por eso 5.13 crudo baja a ~4.21.
    """
    liga = liga if liga is not None else liga_era()

    ancla = None
    if fip_temporada is not None and ip_temporada and ip_temporada > 0:
        ancla = _reg(fip_temporada, ip_temporada, 30.0, liga, liga)

    s = _reg(era_temporada, ip_temporada, PRIOR_ERA, ancla, liga)
    l = _reg(era_last5, ip_last5, PRIOR_ERA * 0.625, ancla, liga)

    if s is not None and l is not None:
        return s * 0.70 + l * 0.30
    return s if s is not None else l


# ══════════════════════════════════════════════════════════════════════════
# 4. R/G USADO — puerto EXACTO de rgUsado() de OVA
# ══════════════════════════════════════════════════════════════════════════
def rg_usado_ova(rg_temporada, rg_14dias) -> "float | None":
    """70% carreras/juego de temporada + 30% de últimos 14 días.
    rg_14dias debe venir en None si hubo <5 juegos en esa ventana."""
    if rg_14dias is None:
        return rg_temporada
    if rg_temporada is None:
        return rg_14dias
    return rg_temporada * 0.70 + rg_14dias * 0.30


# ══════════════════════════════════════════════════════════════════════════
# 5. LA FÓRMULA DE VENTAJA COMPLETA — puerto de ventaja() de OVA
# ══════════════════════════════════════════════════════════════════════════
def calcular_chance_ova(era_local, era_visita, rg_local, rg_visita,
                          bullpen_local, bullpen_visita,
                          multiplicador: int = MULT) -> dict:
    """
    La fórmula de ventaja de OVA, ya con los ERA y R/G que le pases
    (que deberían venir de era_usado_ova() / rg_usado_ova()):

        Δ = (eraA − eraH) × 0.65 + (rgH − rgA) × 0.5
            + (bpA − bpH) × 0.25 + 0.12 (localía)
        Chance_local = 50 + (Δ × multiplicador), tope duro 22–78%

    Devuelve un dict con el desglose completo, igual que la pestaña
    "Números" de OVA, para que puedas loguearlo o mostrarlo.
    """
    p1 = (era_visita - era_local) * 0.65
    p2 = (rg_local - rg_visita) * 0.5
    p3 = (bullpen_visita - bullpen_local) * 0.25
    delta = p1 + p2 + p3 + 0.12

    pct_crudo = 50 + delta * multiplicador
    capeado = pct_crudo > CORTE_MAX_PCT or pct_crudo < CORTE_MIN_PCT
    pct_local = max(CORTE_MIN_PCT, min(CORTE_MAX_PCT, pct_crudo))

    return {
        "pct_local": round(pct_local, 1),
        "pct_visita": round(100 - pct_local, 1),
        "delta": round(delta, 4),
        "capeado": capeado,
        "desglose": {
            "abridores_x065": round(p1, 4),
            "ofensiva_x050": round(p2, 4),
            "bullpen_x025": round(p3, 4),
            "localia": 0.12,
        },
    }


# ══════════════════════════════════════════════════════════════════════════
# FUNCIÓN DE ALTO NIVEL — junta todo, lista para pegar en el loop del scan
# ══════════════════════════════════════════════════════════════════════════
def analizar_juego_ova(
    fecha_juego: date,
    team_id_local: int, team_id_visita: int,
    era_temp_local, ip_temp_local, fip_temp_local, era_l5_local, ip_l5_local,
    era_temp_visita, ip_temp_visita, fip_temp_visita, era_l5_visita, ip_l5_visita,
    rg_temp_local, rg_temp_visita,
    bullpen_era_local, bullpen_era_visita,
    year: int = None,
) -> dict:
    """
    Todo el circuito en una sola llamada:
      1. Trae el ERA real de liga (cacheado por día).
      2. Trae carreras/juego de últimos 14 días de cada equipo (cacheado).
      3. Calcula el ERA usado (dos capas) de cada abridor.
      4. Calcula el R/G usado (70/30) de cada equipo.
      5. Corre la fórmula de ventaja y devuelve el % final.

    Todos los parámetros de entrada son los mismos que ya calculas hoy en tu
    servidor (ERA de temporada, innings, FIP, ERA últimas 5, bullpen ERA) —
    esto NO reemplaza esos fetches, solo la parte de regresión y mezcla que
    faltaba portar.
    """
    year = year or fecha_juego.year
    liga = liga_era(year)

    rg14_local = rg_ultimos_14_dias(team_id_local, fecha_juego, year)
    rg14_visita = rg_ultimos_14_dias(team_id_visita, fecha_juego, year)

    era_local = era_usado_ova(era_temp_local, ip_temp_local, fip_temp_local,
                               era_l5_local, ip_l5_local, liga)
    era_visita = era_usado_ova(era_temp_visita, ip_temp_visita, fip_temp_visita,
                                era_l5_visita, ip_l5_visita, liga)

    rg_local = rg_usado_ova(rg_temp_local, rg14_local)
    rg_visita = rg_usado_ova(rg_temp_visita, rg14_visita)

    if None in (era_local, era_visita, rg_local, rg_visita,
                bullpen_era_local, bullpen_era_visita):
        return {"error": "faltan datos para calcular (ERA, R/G o bullpen en None)"}

    resultado = calcular_chance_ova(
        era_local, era_visita, rg_local, rg_visita,
        bullpen_era_local, bullpen_era_visita,
    )
    resultado["liga_era_usada"] = round(liga, 3)
    resultado["era_local_usado"] = round(era_local, 3)
    resultado["era_visita_usado"] = round(era_visita, 3)
    resultado["rg_local_usado"] = round(rg_local, 3)
    resultado["rg_visita_usado"] = round(rg_visita, 3)
    resultado["rg14_local"] = rg14_local
    resultado["rg14_visita"] = rg14_visita
    return resultado


# ══════════════════════════════════════════════════════════════════════════
# EJEMPLO DE USO
# ══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # Ejemplo con datos inventados solo para ver que corre — sustituye por
    # los datos reales que ya trae tu servidor para cada juego.
    hoy = date.today()
    r = analizar_juego_ova(
        fecha_juego=hoy,
        team_id_local=143, team_id_visita=134,     # Phillies vs Pirates, ejemplo
        era_temp_local=3.80, ip_temp_local=140.0, fip_temp_local=3.65,
        era_l5_local=3.20, ip_l5_local=28.0,
        era_temp_visita=4.30, ip_temp_visita=110.0, fip_temp_visita=4.10,
        era_l5_visita=5.00, ip_l5_visita=22.0,
        rg_temp_local=4.6, rg_temp_visita=4.1,
        bullpen_era_local=3.90, bullpen_era_visita=4.20,
    )
    import json
    print(json.dumps(r, indent=2, ensure_ascii=False))
