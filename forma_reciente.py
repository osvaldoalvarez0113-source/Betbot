"""
forma_reciente.py — Forma reciente por equipo/pitcher para Betbot.

Fuentes: pybaseball (FanGraphs) + Baseball Reference.
Caché:   forma_cache.json — TTL 12 horas, refresco único a las 9 AM CT.
Respaldo: GitHub REST API (mismo patrón que daily_exposure / elite_counter).

INTEGRACIÓN (fase posterior):
  1. Importar en kelly_odds.py: import forma_reciente as fr
  2. Llamar _github_push_forma_cache() al final de run_scan() junto a los
     otros pushes de GitHub (línea ~15849).
  Sin esos pasos el módulo ya funciona de forma autónoma — el respaldo
  simplemente no se sincronizará hasta que se complete la integración.

NO modifica kelly_odds.py, contexto_juego.py ni ningún archivo existente.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timedelta

# ── Dependencias opcionales ────────────────────────────────────────────────────
try:
    import pytz
    CDT = pytz.timezone("America/Chicago")
except ImportError:
    import zoneinfo
    CDT = zoneinfo.ZoneInfo("America/Chicago")

try:
    import pandas as _pd
    _PANDAS_OK = True
except ImportError:
    _PANDAS_OK = False
    _pd = None

try:
    from pybaseball import (
        batting_stats_range,
        pitching_stats_range,
        schedule_and_record,
        cache as _pb_cache,
    )
    _pb_cache.enable()
    _PYBASEBALL_OK = True
except Exception as _pb_err:
    _PYBASEBALL_OK = False
    print(f"  ⚠️  forma_reciente: pybaseball no disponible — {_pb_err}")

# ── Constantes ────────────────────────────────────────────────────────────────
CACHE_FILE      = "forma_cache.json"
CACHE_TTL_HOURS = 12
CURRENT_SEASON  = 2026

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO",  "osvaldoalvarez0113-source/Betbot")

# Valores neutrales devueltos cuando pybaseball falla o el dato no existe
NEUTRAL_OFENSIVA = {"ops": 0.715, "wrc_plus": None, "flag": "SIN DATO"}
NEUTRAL_BULLPEN  = {"era": 4.00,  "fip": None,      "flag": "SIN DATO"}
NEUTRAL_ABRIDOR  = {"era": 4.00,  "k_bb": None, "innings": 0.0, "flag": "SIN DATO"}
NEUTRAL_SPLITS   = {
    "home_w": 0, "home_l": 0, "away_w": 0, "away_l": 0,
    "day_w":  0, "day_l":  0, "night_w": 0, "night_l": 0,
    "flag": "SIN DATO",
}

# ── Mapas de nombres de equipo ─────────────────────────────────────────────────
# Nombre común (lower) → abreviatura FanGraphs (batting/pitching_stats_range)
_TEAM_ABBR: dict[str, str] = {
    # AL East
    "yankees": "NYY", "new york yankees": "NYY",
    "red sox":  "BOS", "boston red sox":   "BOS",
    "rays":     "TB",  "tampa bay rays":   "TB",
    "blue jays":"TOR", "toronto blue jays":"TOR",
    "orioles":  "BAL", "baltimore orioles":"BAL",
    # AL Central
    "white sox":"CWS", "chicago white sox":"CWS",
    "guardians":"CLE", "cleveland guardians":"CLE",
    "tigers":   "DET", "detroit tigers":   "DET",
    "royals":   "KC",  "kansas city royals":"KC",
    "twins":    "MIN", "minnesota twins":  "MIN",
    # AL West
    "astros":   "HOU", "houston astros":   "HOU",
    "angels":   "LAA", "los angeles angels":"LAA",
    "athletics":"ATH", "a's": "ATH", "oakland athletics":"ATH",
    "mariners": "SEA", "seattle mariners": "SEA",
    "rangers":  "TEX", "texas rangers":    "TEX",
    # NL East
    "braves":   "ATL", "atlanta braves":   "ATL",
    "marlins":  "MIA", "miami marlins":    "MIA",
    "mets":     "NYM", "new york mets":    "NYM",
    "phillies": "PHI", "philadelphia phillies":"PHI",
    "nationals":"WSH", "washington nationals":"WSH",
    # NL Central
    "cubs":     "CHC", "chicago cubs":     "CHC",
    "reds":     "CIN", "cincinnati reds":  "CIN",
    "brewers":  "MIL", "milwaukee brewers":"MIL",
    "pirates":  "PIT", "pittsburgh pirates":"PIT",
    "cardinals":"STL", "st. louis cardinals":"STL", "st louis cardinals":"STL",
    # NL West
    "diamondbacks":"ARI", "arizona diamondbacks":"ARI", "d-backs":"ARI",
    "rockies":  "COL", "colorado rockies": "COL",
    "dodgers":  "LAD", "los angeles dodgers":"LAD",
    "padres":   "SD",  "san diego padres": "SD",
    "giants":   "SF",  "san francisco giants":"SF",
}

# schedule_and_record acepta la abreviatura directamente ("NYY", "LAD", etc.)
# — _TEAM_ABBR sirve para eso.

# Mapeo para batting/pitching_stats_range (FanGraphs vía pybaseball):
# usa nombre de ciudad completo, y equipos que comparten ciudad se distinguen
# por el campo 'Lev' ('Maj-AL' o 'Maj-NL').
# Formato: alias → (Tm ciudad, prefijo Lev para filtrar — '' = único en la ciudad)
_TEAM_PB: dict[str, tuple[str, str]] = {
    "yankees":              ("New York",      "Maj-AL"),
    "new york yankees":     ("New York",      "Maj-AL"),
    "mets":                 ("New York",      "Maj-NL"),
    "new york mets":        ("New York",      "Maj-NL"),
    "dodgers":              ("Los Angeles",   "Maj-NL"),
    "los angeles dodgers":  ("Los Angeles",   "Maj-NL"),
    "angels":               ("Los Angeles",   "Maj-AL"),
    "los angeles angels":   ("Los Angeles",   "Maj-AL"),
    "cubs":                 ("Chicago",       "Maj-NL"),
    "chicago cubs":         ("Chicago",       "Maj-NL"),
    "white sox":            ("Chicago",       "Maj-AL"),
    "chicago white sox":    ("Chicago",       "Maj-AL"),
    "red sox":              ("Boston",        ""),
    "boston red sox":       ("Boston",        ""),
    "rays":                 ("Tampa Bay",     ""),
    "tampa bay rays":       ("Tampa Bay",     ""),
    "blue jays":            ("Toronto",       ""),
    "toronto blue jays":    ("Toronto",       ""),
    "orioles":              ("Baltimore",     ""),
    "baltimore orioles":    ("Baltimore",     ""),
    "guardians":            ("Cleveland",     ""),
    "cleveland guardians":  ("Cleveland",     ""),
    "tigers":               ("Detroit",       ""),
    "detroit tigers":       ("Detroit",       ""),
    "royals":               ("Kansas City",   ""),
    "kansas city royals":   ("Kansas City",   ""),
    "twins":                ("Minnesota",     ""),
    "minnesota twins":      ("Minnesota",     ""),
    "astros":               ("Houston",       ""),
    "houston astros":       ("Houston",       ""),
    "athletics":            ("Athletics",     ""),
    "a's":                  ("Athletics",     ""),
    "oakland athletics":    ("Athletics",     ""),
    "mariners":             ("Seattle",       ""),
    "seattle mariners":     ("Seattle",       ""),
    "rangers":              ("Texas",         ""),
    "texas rangers":        ("Texas",         ""),
    "braves":               ("Atlanta",       ""),
    "atlanta braves":       ("Atlanta",       ""),
    "marlins":              ("Miami",         ""),
    "miami marlins":        ("Miami",         ""),
    "phillies":             ("Philadelphia",  ""),
    "philadelphia phillies":("Philadelphia",  ""),
    "nationals":            ("Washington",    ""),
    "washington nationals": ("Washington",    ""),
    "reds":                 ("Cincinnati",    ""),
    "cincinnati reds":      ("Cincinnati",    ""),
    "brewers":              ("Milwaukee",     ""),
    "milwaukee brewers":    ("Milwaukee",     ""),
    "pirates":              ("Pittsburgh",    ""),
    "pittsburgh pirates":   ("Pittsburgh",   ""),
    "cardinals":            ("St. Louis",     ""),
    "st. louis cardinals":  ("St. Louis",     ""),
    "st louis cardinals":   ("St. Louis",     ""),
    "diamondbacks":         ("Arizona",       ""),
    "arizona diamondbacks": ("Arizona",       ""),
    "d-backs":              ("Arizona",       ""),
    "rockies":              ("Colorado",      ""),
    "colorado rockies":     ("Colorado",      ""),
    "padres":               ("San Diego",     ""),
    "san diego padres":     ("San Diego",     ""),
    "giants":               ("San Francisco", ""),
    "san francisco giants": ("San Francisco", ""),
}


def _resolve_team(equipo: str) -> str:
    """Normaliza el nombre de equipo a abreviatura BR para schedule_and_record."""
    key = equipo.strip().lower()
    if key in _TEAM_ABBR:
        return _TEAM_ABBR[key]
    for alias, abbr in _TEAM_ABBR.items():
        if alias in key or key in alias:
            return abbr
    return equipo.upper()[:3]


def _resolve_team_pb(equipo: str) -> tuple[str, str]:
    """
    Devuelve (Tm ciudad, prefijo Lev) para filtrar batting/pitching_stats_range.
    El prefijo Lev distingue equipos que comparten ciudad ('Maj-AL'/'Maj-NL').
    '' significa ciudad única — solo filtrar a Maj*.
    """
    key = equipo.strip().lower()
    if key in _TEAM_PB:
        return _TEAM_PB[key]
    for alias, val in _TEAM_PB.items():
        if alias in key or key in alias:
            return val
    return (equipo, "")


# ── Timeout helper (threading — funciona en cualquier thread de Railway) ───────
def _with_timeout(fn, timeout: int = 30):
    """Ejecuta fn() en un thread separado. Lanza TimeoutError si tarda más de `timeout` s."""
    result:    list = [None]
    error:     list = [None]
    completed: list = [False]

    def _target():
        try:
            result[0] = fn()
            completed[0] = True
        except Exception as e:
            error[0] = e
            completed[0] = True

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)
    if not completed[0]:
        raise TimeoutError(f"pybaseball tardó más de {timeout}s")
    if error[0]:
        raise error[0]
    return result[0]


# ══════════════════════════════════════════════════════════════════════════════
# CACHÉ LOCAL
# ══════════════════════════════════════════════════════════════════════════════

def _load_cache() -> dict:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cache(data: dict) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"  ⚠️  forma_reciente: error guardando caché — {e}")


def _get_cache(section: str, key: str) -> dict | None:
    """Devuelve el valor cacheado si existe y tiene menos de CACHE_TTL_HOURS horas. None si expiró."""
    data = _load_cache()
    entry = data.get(section, {}).get(key)
    if not entry:
        return None
    ts = entry.get("_ts", 0)
    age_h = (time.time() - ts) / 3600
    if age_h < CACHE_TTL_HOURS:
        return {k: v for k, v in entry.items() if k != "_ts"}
    return None


def _set_cache(section: str, key: str, value: dict) -> None:
    """Guarda value en caché con timestamp actual."""
    data = _load_cache()
    data.setdefault(section, {})[key] = {**value, "_ts": time.time()}
    _save_cache(data)


# ══════════════════════════════════════════════════════════════════════════════
# RESPALDO GITHUB (mismo patrón que daily_exposure / elite_counter)
# ══════════════════════════════════════════════════════════════════════════════

def _github_pull_forma_cache() -> dict | None:
    """Restaura forma_cache.json desde GitHub. Devuelve dict o None si falla."""
    if not GITHUB_TOKEN:
        return None
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{CACHE_FILE}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        })
        with urllib.request.urlopen(req, timeout=8) as r:
            info = json.loads(r.read())
        return json.loads(base64.b64decode(info["content"]).decode())
    except Exception:
        return None


def _github_push_forma_cache() -> None:
    """
    Sincroniza forma_cache.json a GitHub.
    Llamar desde run_scan() junto a _github_push_daily_exposure() (fase integración).
    """
    if not GITHUB_TOKEN:
        return
    if not os.path.exists(CACHE_FILE):
        return
    try:
        with open(CACHE_FILE, "rb") as f:
            raw = f.read()
        api = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{CACHE_FILE}"
        hdrs = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept":        "application/vnd.github.v3+json",
            "Content-Type":  "application/json",
        }
        sha = None
        try:
            r0 = urllib.request.Request(api, headers=hdrs)
            with urllib.request.urlopen(r0, timeout=8) as r:
                sha = json.loads(r.read()).get("sha")
        except Exception:
            pass
        body: dict = {
            "message": f"bot: forma_cache update {datetime.utcnow().strftime('%Y-%m-%dT%H:%M')}",
            "content": base64.b64encode(raw).decode(),
        }
        if sha:
            body["sha"] = sha
        req = urllib.request.Request(api, data=json.dumps(body).encode(), headers=hdrs, method="PUT")
        with urllib.request.urlopen(req, timeout=12):
            pass
        print("  ☁️  forma_cache.json sincronizado a GitHub")
    except Exception as e:
        print(f"  ⚠️  GitHub forma_cache push error: {e}")


def _restore_cache_from_github() -> None:
    """
    Intenta restaurar forma_cache.json desde GitHub si el local no existe.
    Llamar al inicio del proceso (e.g. en bot startup).
    """
    if os.path.exists(CACHE_FILE):
        return
    data = _github_pull_forma_cache()
    if data:
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            print("  ☁️  forma_cache.json restaurado desde GitHub")
        except Exception as e:
            print(f"  ⚠️  forma_cache restore error: {e}")


# ── Auto-restore al importar (sobrevive redeploys en Railway) ─────────────────
_restore_cache_from_github()


# ══════════════════════════════════════════════════════════════════════════════
# FUNCIONES PRINCIPALES
# ══════════════════════════════════════════════════════════════════════════════

def _date_range_14d() -> tuple[str, str]:
    """Devuelve (start_dt, end_dt) como strings YYYY-MM-DD para los últimos 14 días."""
    end   = datetime.utcnow().date()
    start = end - timedelta(days=14)
    return str(start), str(end)


def _safe_float(val, default: float) -> float:
    """Convierte val a float; devuelve default si es NaN, None, o no numérico."""
    try:
        v = float(val)
        import math
        return default if math.isnan(v) else v
    except Exception:
        return default


# ─────────────────────────────────────────────────────────────────────────────
# A) FORMA OFENSIVA — OPS / wRC+ últimos 14 días
# ─────────────────────────────────────────────────────────────────────────────

def forma_ofensiva_14d(equipo: str) -> dict:
    """
    OPS (y wRC+ si disponible) del equipo en los últimos 14 días.

    Flags:
      "CALIENTE" — wRC+ >= 115  ó  OPS >= .780
      "FRIO"     — wRC+ <=  85  ó  OPS <= .650
      "NORMAL"   — rango medio

    Devuelve NEUTRAL_OFENSIVA y registra error si pybaseball falla o tarda > 30s.
    """
    if not _PYBASEBALL_OK:
        return {**NEUTRAL_OFENSIVA}

    pb_name, lev_filter = _resolve_team_pb(equipo)
    cache_key = pb_name + (f"_{lev_filter}" if lev_filter else "")
    cached = _get_cache("forma_ofensiva", cache_key)
    if cached:
        print(f"  [forma] USANDO CACHE — {pb_name} ofensiva ({cached.get('flag')})")
        return cached

    start_dt, end_dt = _date_range_14d()
    try:
        df = _with_timeout(lambda: batting_stats_range(start_dt, end_dt), timeout=30)
    except TimeoutError:
        print(f"  ⚠️  forma_ofensiva_14d({equipo}): timeout >30s — devolviendo neutral")
        return {**NEUTRAL_OFENSIVA}
    except Exception as e:
        print(f"  ⚠️  forma_ofensiva_14d({equipo}): {e} — devolviendo neutral")
        return {**NEUTRAL_OFENSIVA}

    try:
        # pybaseball usa 'Tm' con nombre de ciudad completo; 'Lev' distingue equipos en misma ciudad
        tm_col = "Tm" if "Tm" in df.columns else ("Team" if "Team" in df.columns else None)
        if tm_col is None:
            print(f"  ⚠️  forma_ofensiva_14d: columna de equipo no encontrada — {list(df.columns[:6])}")
            return {**NEUTRAL_OFENSIVA}
        team_df = df[df[tm_col] == pb_name]
        # Filtrar liga para ciudades con dos equipos (NY, LA, CHI)
        if lev_filter and "Lev" in team_df.columns:
            team_df = team_df[team_df["Lev"].str.contains(lev_filter, na=False)]
        # Incluir solo jugadores MLB (excluir MiLB)
        if "Lev" in team_df.columns:
            team_df = team_df[team_df["Lev"].str.startswith("Maj")]
        if team_df.empty:
            print(f"  ⚠️  forma_ofensiva_14d: sin datos para '{pb_name}' en {start_dt}→{end_dt}")
            return {**NEUTRAL_OFENSIVA}

        pa_col = "PA" if "PA" in team_df.columns else None

        # OPS ponderado por PA
        if pa_col and "OPS" in team_df.columns:
            total_pa = team_df[pa_col].sum()
            ops = (_safe_float((team_df["OPS"] * team_df[pa_col]).sum() / total_pa, 0.715)
                   if total_pa > 0 else 0.715)
        elif "OPS" in team_df.columns:
            ops = _safe_float(team_df["OPS"].mean(), 0.715)
        else:
            ops = 0.715

        # wRC+ ponderado por PA (preferido si disponible)
        wrc_plus: float | None = None
        wrc_col = next((c for c in ("wRC+", "wRC_plus", "wRC") if c in team_df.columns), None)
        if wrc_col and pa_col:
            total_pa = team_df[pa_col].sum()
            if total_pa > 0:
                wrc_plus = _safe_float(
                    (team_df[wrc_col] * team_df[pa_col]).sum() / total_pa, None
                )
        elif wrc_col:
            wrc_plus = _safe_float(team_df[wrc_col].mean(), None)

        # Flags: preferir wRC+ si disponible
        if wrc_plus is not None:
            if   wrc_plus >= 115: flag = "CALIENTE"
            elif wrc_plus <=  85: flag = "FRIO"
            else:                  flag = "NORMAL"
        else:
            if   ops >= 0.780: flag = "CALIENTE"
            elif ops <= 0.650: flag = "FRIO"
            else:               flag = "NORMAL"

        result = {
            "ops":       round(ops, 3),
            "wrc_plus":  round(wrc_plus, 1) if wrc_plus is not None else None,
            "flag":      flag,
            "rango":     f"{start_dt} → {end_dt}",
        }
        _set_cache("forma_ofensiva", cache_key, result)
        return result

    except Exception as e:
        print(f"  ⚠️  forma_ofensiva_14d({equipo}): error procesando datos — {e}")
        return {**NEUTRAL_OFENSIVA}


# ─────────────────────────────────────────────────────────────────────────────
# B) BULLPEN — ERA / FIP últimos 14 días (sólo relevistas)
# ─────────────────────────────────────────────────────────────────────────────

def bullpen_14d(equipo: str) -> dict:
    """
    ERA (y FIP/SIERA si disponibles) del bullpen en los últimos 14 días.
    Solo cuenta lanzadores con GS == 0 (o muy bajo ratio GS/G).

    Flags:
      "ELITE"   — ERA <= 3.20
      "QUEMADO" — ERA >= 4.60
      "NORMAL"  — resto
    """
    if not _PYBASEBALL_OK:
        return {**NEUTRAL_BULLPEN}

    pb_name, lev_filter = _resolve_team_pb(equipo)
    cache_key = pb_name + (f"_{lev_filter}" if lev_filter else "")
    cached = _get_cache("bullpen", cache_key)
    if cached:
        print(f"  [forma] USANDO CACHE — {pb_name} bullpen ({cached.get('flag')})")
        return cached

    start_dt, end_dt = _date_range_14d()
    try:
        df = _with_timeout(lambda: pitching_stats_range(start_dt, end_dt), timeout=30)
    except TimeoutError:
        print(f"  ⚠️  bullpen_14d({equipo}): timeout >30s — devolviendo neutral")
        return {**NEUTRAL_BULLPEN}
    except Exception as e:
        print(f"  ⚠️  bullpen_14d({equipo}): {e} — devolviendo neutral")
        return {**NEUTRAL_BULLPEN}

    try:
        # pybaseball usa 'Tm' con nombre de ciudad completo en pitching_stats_range
        tm_col = "Tm" if "Tm" in df.columns else ("Team" if "Team" in df.columns else None)
        if tm_col is None:
            print(f"  ⚠️  bullpen_14d: columna de equipo no encontrada — {list(df.columns[:6])}")
            return {**NEUTRAL_BULLPEN}
        team_df = df[df[tm_col] == pb_name].copy()
        if lev_filter and "Lev" in team_df.columns:
            team_df = team_df[team_df["Lev"].str.contains(lev_filter, na=False)]
        if "Lev" in team_df.columns:
            team_df = team_df[team_df["Lev"].str.startswith("Maj")]
        if team_df.empty:
            print(f"  ⚠️  bullpen_14d: sin datos para '{pb_name}'")
            return {**NEUTRAL_BULLPEN}

        # Filtrar relevistas: GS == 0 o ratio GS/G < 0.3
        if "GS" in team_df.columns and "G" in team_df.columns:
            rel = team_df[
                (team_df["GS"] == 0) |
                (team_df["GS"] / team_df["G"].replace(0, 1) < 0.3)
            ]
        else:
            rel = team_df  # sin columna GS, usar todos

        if rel.empty:
            rel = team_df  # fallback al equipo completo

        # ERA ponderado por innings
        ip_col  = "IP"  if "IP"  in rel.columns else None
        era_val = 4.00

        if ip_col and "ERA" in rel.columns:
            total_ip = rel[ip_col].sum()
            if total_ip > 0:
                era_val = _safe_float(
                    (rel["ERA"] * rel[ip_col]).sum() / total_ip, 4.00
                )
        elif "ERA" in rel.columns:
            era_val = _safe_float(rel["ERA"].mean(), 4.00)

        # FIP (opcional)
        fip_val: float | None = None
        if "FIP" in rel.columns and ip_col:
            total_ip = rel[ip_col].sum()
            if total_ip > 0:
                fip_val = _safe_float(
                    (rel["FIP"] * rel[ip_col]).sum() / total_ip, None
                )

        # SIERA (si disponible sin cálculo adicional)
        siera_val: float | None = None
        if "SIERA" in rel.columns and ip_col:
            total_ip = rel[ip_col].sum()
            if total_ip > 0:
                siera_val = _safe_float(
                    (rel["SIERA"] * rel[ip_col]).sum() / total_ip, None
                )

        # Usar SIERA si está disponible; si no, ERA para el flag
        ref_era = siera_val if siera_val is not None else era_val
        if   ref_era <= 3.20: flag = "ELITE"
        elif ref_era >= 4.60: flag = "QUEMADO"
        else:                  flag = "NORMAL"

        result = {
            "era":   round(era_val,  2),
            "fip":   round(fip_val,  2) if fip_val  is not None else None,
            "siera": round(siera_val, 2) if siera_val is not None else None,
            "flag":  flag,
            "rango": f"{start_dt} → {end_dt}",
        }
        _set_cache("bullpen", cache_key, result)
        return result

    except Exception as e:
        print(f"  ⚠️  bullpen_14d({equipo}): error procesando — {e}")
        return {**NEUTRAL_BULLPEN}


# ─────────────────────────────────────────────────────────────────────────────
# C) FORMA DEL ABRIDOR — últimas ~3 salidas (ventana 21 días)
# ─────────────────────────────────────────────────────────────────────────────

def forma_abridor(nombre_pitcher: str) -> dict:
    """
    ERA, K/BB e innings totales del abridor en los últimos 21 días
    (ventana que cubre ~3 salidas para un pitcher de rotación).
    NO usa xFIP.

    Flags:
      "EN RACHA" — ERA <= 3.00
      "EN CAIDA" — ERA >= 5.50
      "NORMAL"   — resto
    """
    if not _PYBASEBALL_OK:
        return {**NEUTRAL_ABRIDOR}

    key = nombre_pitcher.strip()
    cached = _get_cache("abridor", key)
    if cached:
        print(f"  [forma] USANDO CACHE — {key} ({cached.get('flag')})")
        return cached

    end   = datetime.utcnow().date()
    start = end - timedelta(days=21)
    start_dt, end_dt = str(start), str(end)

    try:
        df = _with_timeout(lambda: pitching_stats_range(start_dt, end_dt), timeout=30)
    except TimeoutError:
        print(f"  ⚠️  forma_abridor({nombre_pitcher}): timeout >30s")
        return {**NEUTRAL_ABRIDOR}
    except Exception as e:
        print(f"  ⚠️  forma_abridor({nombre_pitcher}): {e}")
        return {**NEUTRAL_ABRIDOR}

    try:
        # Buscar pitcher por nombre (parcial, case-insensitive)
        name_col = "Name" if "Name" in df.columns else df.columns[0]
        pitcher_df = df[df[name_col].str.contains(key, case=False, na=False)]

        # Filtrar solo abridores: GS >= 1
        if "GS" in pitcher_df.columns:
            pitcher_df = pitcher_df[pitcher_df["GS"] >= 1]

        if pitcher_df.empty:
            print(f"  ⚠️  forma_abridor: '{nombre_pitcher}' no encontrado en {start_dt}→{end_dt}")
            return {**NEUTRAL_ABRIDOR}

        row = pitcher_df.iloc[0]
        era     = _safe_float(row.get("ERA",  4.00), 4.00)
        innings = _safe_float(row.get("IP",   0.0),  0.0)
        so      = _safe_float(row.get("SO",   0.0),  0.0)
        bb      = _safe_float(row.get("BB",   1.0),  1.0)
        k_bb    = round(so / max(bb, 1.0), 2)

        if   era <= 3.00: flag = "EN RACHA"
        elif era >= 5.50: flag = "EN CAIDA"
        else:              flag = "NORMAL"

        result = {
            "era":     round(era, 2),
            "k_bb":    k_bb,
            "innings": round(innings, 1),
            "flag":    flag,
            "rango":   f"{start_dt} → {end_dt} (~3 salidas)",
        }
        _set_cache("abridor", key, result)
        return result

    except Exception as e:
        print(f"  ⚠️  forma_abridor({nombre_pitcher}): error procesando — {e}")
        return {**NEUTRAL_ABRIDOR}


# ─────────────────────────────────────────────────────────────────────────────
# D) SPLITS DE EQUIPO — récord casa/visita y día/noche de la temporada
# ─────────────────────────────────────────────────────────────────────────────

def splits_equipo(equipo: str) -> dict:
    """
    Récord de la temporada dividido por casa/visita y día/noche.
    Usa schedule_and_record de pybaseball (Baseball Reference).
    """
    if not _PYBASEBALL_OK:
        return {**NEUTRAL_SPLITS}

    abbr   = _resolve_team(equipo)
    cached = _get_cache("splits", abbr)
    if cached:
        print(f"  [forma] USANDO CACHE — {abbr} splits")
        return cached

    try:
        df = _with_timeout(
            lambda: schedule_and_record(CURRENT_SEASON, abbr),
            timeout=30
        )
    except TimeoutError:
        print(f"  ⚠️  splits_equipo({equipo}): timeout >30s")
        return {**NEUTRAL_SPLITS}
    except Exception as e:
        print(f"  ⚠️  splits_equipo({equipo}): {e}")
        return {**NEUTRAL_SPLITS}

    try:
        # Solo juegos jugados — deben tener resultado W o L
        wl_col = next((c for c in df.columns if "W/L" in str(c) or str(c).strip() == "W-L"), None)
        if wl_col:
            played = df[df[wl_col].isin(["W", "L", "W-wo", "L-wo"])].copy()
        else:
            played = df.dropna(subset=[df.columns[-1]]).copy()

        if played.empty:
            print(f"  ⚠️  splits_equipo: sin juegos jugados para {sched_name}")
            return {**NEUTRAL_SPLITS}

        # ── Home / Away ───────────────────────────────────────────────────────
        # Baseball Reference marca visitante con '@' en la columna Unnamed:2 o similar
        ha_col = next(
            (c for c in played.columns
             if str(c).startswith("Unnamed") or str(c).strip() in ("", "H/A", "HA")),
            None,
        )
        if ha_col:
            away_mask = played[ha_col].astype(str).str.strip() == "@"
            home_mask = ~away_mask
        else:
            # Fallback: asumir mitad en casa
            home_mask = played.index < len(played) // 2
            away_mask = ~home_mask

        wl = wl_col or played.columns[-1]
        home_games = played[home_mask]
        away_games = played[away_mask]
        home_w = int((home_games[wl].str.startswith("W")).sum())
        home_l = int((home_games[wl].str.startswith("L")).sum())
        away_w = int((away_games[wl].str.startswith("W")).sum())
        away_l = int((away_games[wl].str.startswith("L")).sum())

        # ── Day / Night ───────────────────────────────────────────────────────
        dn_col = next((c for c in played.columns if str(c).strip() in ("D/N", "DN")), None)
        if dn_col:
            day_mask   = played[dn_col].astype(str).str.strip().str.upper() == "D"
            night_mask = played[dn_col].astype(str).str.strip().str.upper() == "N"
            day_games  = played[day_mask]
            night_games = played[night_mask]
        else:
            day_games   = played.head(0)  # vacío si no hay columna
            night_games = played.head(0)

        day_w   = int((day_games[wl].str.startswith("W")).sum())   if not day_games.empty   else 0
        day_l   = int((day_games[wl].str.startswith("L")).sum())   if not day_games.empty   else 0
        night_w = int((night_games[wl].str.startswith("W")).sum()) if not night_games.empty else 0
        night_l = int((night_games[wl].str.startswith("L")).sum()) if not night_games.empty else 0

        result = {
            "home_w":  home_w,  "home_l":  home_l,
            "away_w":  away_w,  "away_l":  away_l,
            "day_w":   day_w,   "day_l":   day_l,
            "night_w": night_w, "night_l": night_l,
            "flag":    "OK",
        }
        _set_cache("splits", abbr, result)
        return result

    except Exception as e:
        print(f"  ⚠️  splits_equipo({equipo}): error procesando — {e}")
        return {**NEUTRAL_SPLITS}


# ══════════════════════════════════════════════════════════════════════════════
# TEST DE VERIFICACIÓN
# ══════════════════════════════════════════════════════════════════════════════

def test_forma() -> None:
    """
    Corre las 4 funciones para Yankees y Dodgers e imprime resultados.
    Verifica que el segundo llamado use caché.
    Reporta tiempo de la primera carga.
    """
    equipos  = ["Yankees", "Dodgers"]
    pitcher  = "Gerrit Cole"

    print("\n" + "═" * 60)
    print("  TEST forma_reciente.py — primera carga (pybaseball)")
    print("═" * 60)

    for equipo in equipos:
        print(f"\n── {equipo.upper()} ──")

        t0 = time.time()
        of = forma_ofensiva_14d(equipo)
        print(f"  Ofensiva [{of['flag']}]: OPS={of['ops']}  wRC+={of.get('wrc_plus')}  ({time.time()-t0:.1f}s)")

        t0 = time.time()
        bp = bullpen_14d(equipo)
        print(f"  Bullpen  [{bp['flag']}]: ERA={bp['era']}  FIP={bp.get('fip')}  SIERA={bp.get('siera')}  ({time.time()-t0:.1f}s)")

        t0 = time.time()
        sp = splits_equipo(equipo)
        print(f"  Splits   [{sp['flag']}]: Casa {sp['home_w']}-{sp['home_l']} | Visita {sp['away_w']}-{sp['away_l']} | "
              f"Día {sp['day_w']}-{sp['day_l']} | Noche {sp['night_w']}-{sp['night_l']}  ({time.time()-t0:.1f}s)")

    # Abridor
    print(f"\n── PITCHER: {pitcher} ──")
    t0 = time.time()
    ab = forma_abridor(pitcher)
    print(f"  Abridor [{ab['flag']}]: ERA={ab['era']}  K/BB={ab.get('k_bb')}  IP={ab['innings']}  ({time.time()-t0:.1f}s)")

    # Segundo llamado — debe usar caché
    print("\n" + "═" * 60)
    print("  SEGUNDA VUELTA — deben aparecer 'USANDO CACHE'")
    print("═" * 60)
    for equipo in equipos:
        forma_ofensiva_14d(equipo)
        bullpen_14d(equipo)
        splits_equipo(equipo)
    forma_abridor(pitcher)

    print("\n✅ test_forma() completado\n")


# ── Punto de entrada directo ──────────────────────────────────────────────────
if __name__ == "__main__":
    test_forma()
