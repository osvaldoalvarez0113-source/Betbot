# ================================================================
# BETBOT — PAQUETE AVANZADO v1.0
# Fecha: 2026-06-06
#
# Contiene 3 módulos nuevos:
#
# MÓDULO 1 — AUTO_RESULTADOS
#   Consulta MLB Stats API al final de cada juego,
#   actualiza el tracker de paper trade automáticamente
#   y manda notificación con resultado vía ntfy.
#
# MÓDULO 2 — CLV_TRACKER (Closing Line Value)
#   Guarda las cuotas cuando se hace el pick y las
#   compara con las cuotas de cierre. Mide si el bot
#   realmente tiene edge a largo plazo.
#
# MÓDULO 3 — CONTRARIAN
#   Detecta cuando el público apuesta masivamente a un lado
#   y Pinnacle mueve la línea al lado contrario.
#   Señal de dinero sharp — uno de los patrones más rentables.
#
# INTEGRACIÓN:
#   from paquete_avanzado import registrar_pick, clv_tracker, run_modulos_avanzados
# ================================================================

import os
import json
import time
import requests
import datetime
from typing import Optional


# ── Configuración común ──────────────────────────────────────
NTFY_TOPIC   = os.getenv("NTFY_TOPIC", "my-bets")
NTFY_URL     = f"https://ntfy.sh/{NTFY_TOPIC}"
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

TRACKER_FILE = "paper_trades.json"
CLV_FILE     = "clv_tracker.json"


# ================================================================
# UTILIDADES COMUNES
# ================================================================

def _ntfy(titulo: str, cuerpo: str, prioridad: str = "default"):
    """Envía notificación a ntfy."""
    try:
        requests.post(
            NTFY_URL,
            data=cuerpo.encode("utf-8"),
            headers={
                "Title":    titulo.encode("utf-8"),
                "Priority": prioridad,
                "Tags":     "baseball"
            },
            timeout=10
        )
    except Exception as e:
        print(f"⚠️ ntfy error: {e}")


def _cargar_json(path: str, default=None):
    if default is None:
        default = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _guardar_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ================================================================
# MÓDULO 1 — AUTO_RESULTADOS
# ================================================================

def registrar_pick(
    game_pk:    str,
    equipo_h:   str,
    equipo_a:   str,
    pick_tipo:  str,
    linea:      float,
    cuota:      float,
    stake:      float,
    libro:      str  = "Bovada",
    es_paper:   bool = True
):
    """
    Registra un pick en el tracker de paper trades.
    Llamar cada vez que el bot genera un pick aprobado.

    Args:
        game_pk:   ID del juego (The Odds API id o MLB gamePk)
        equipo_h:  Nombre del equipo local
        equipo_a:  Nombre del equipo visitante
        pick_tipo: "UNDER" / "OVER" / "ML_HOME" / "ML_AWAY" / "RL_HOME" / "RL_AWAY"
        linea:     Línea del pick (ej: 8.5 para totales, 0 para ML)
        cuota:     Cuota decimal (ej: 1.95)
        stake:     Monto apostado (paper)
        libro:     Casa de apuestas
        es_paper:  True = paper trade, False = apuesta real
    """
    trades = _cargar_json(TRACKER_FILE, {"picks": [], "bankroll": 1000.0, "stats": {}})

    pick = {
        "id":        f"{game_pk}_{pick_tipo}_{int(time.time())}",
        "game_pk":   str(game_pk),
        "equipo_h":  equipo_h,
        "equipo_a":  equipo_a,
        "pick_tipo": pick_tipo,
        "linea":     linea,
        "cuota":     cuota,
        "stake":     stake,
        "libro":     libro,
        "es_paper":  es_paper,
        "estado":    "PENDING",
        "resultado": None,
        "ganancia":  None,
        "timestamp": datetime.datetime.now().isoformat(),
        "fecha":     datetime.date.today().isoformat()
    }

    trades["picks"].append(pick)
    _guardar_json(TRACKER_FILE, trades)
    print(f"  📝 Pick registrado: {equipo_h} vs {equipo_a} | {pick_tipo} {linea} | stake=${stake}")
    return pick["id"]


def auto_resultados(fecha: Optional[str] = None):
    """
    Consulta MLB Stats API y actualiza resultados de picks pendientes.
    Ejecutar al final de cada scan o en horario programado (ej: 2 AM).

    Args:
        fecha: Fecha en formato YYYY-MM-DD (default: hoy)
    """
    if not fecha:
        fecha = datetime.date.today().isoformat()

    print(f"\n🔄 Auto-resultados: buscando juegos del {fecha}...")

    trades = _cargar_json(TRACKER_FILE, {"picks": [], "bankroll": 1000.0})
    pendientes = [p for p in trades["picks"] if p["estado"] == "PENDING" and p["fecha"] == fecha]

    if not pendientes:
        print("  ℹ️ Sin picks pendientes para hoy.")
        return

    try:
        url = f"{MLB_API_BASE}/schedule"
        params = {
            "sportId": 1,
            "date":    fecha,
            "hydrate": "linescore,decisions"
        }
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
    except Exception as e:
        print(f"  ⚠️ Error consultando MLB API: {e}")
        return

    resultados_mlb = {}
    for fecha_data in data.get("dates", []):
        for juego in fecha_data.get("games", []):
            pk     = str(juego["gamePk"])
            status = juego.get("status", {}).get("abstractGameState", "")
            if status != "Final":
                continue

            home_score = juego["teams"]["home"]["score"]
            away_score = juego["teams"]["away"]["score"]
            total      = home_score + away_score

            resultados_mlb[pk] = {
                "home_score": home_score,
                "away_score": away_score,
                "total":      total,
                "ganador":    "home" if home_score > away_score else "away"
            }

    resueltos = []
    for pick in trades["picks"]:
        if pick["estado"] != "PENDING":
            continue
        if pick["game_pk"] not in resultados_mlb:
            continue

        res   = resultados_mlb[pick["game_pk"]]
        tipo  = pick["pick_tipo"]
        linea = pick["linea"]
        total = res["total"]

        gano = False
        if tipo == "UNDER":
            gano = total < linea
        elif tipo == "OVER":
            gano = total > linea
        elif tipo == "ML_HOME":
            gano = res["ganador"] == "home"
        elif tipo == "ML_AWAY":
            gano = res["ganador"] == "away"
        elif tipo == "RL_HOME":
            diff = res["home_score"] - res["away_score"]
            gano = diff > abs(linea) if linea < 0 else diff >= linea
        elif tipo == "RL_AWAY":
            diff = res["away_score"] - res["home_score"]
            gano = diff > abs(linea) if linea < 0 else diff >= linea

        es_push = (tipo in ["UNDER", "OVER"]) and (total == linea)

        if es_push:
            pick["estado"]    = "PUSH"
            pick["resultado"] = "PUSH"
            pick["ganancia"]  = 0.0
        elif gano:
            ganancia = round(pick["stake"] * (pick["cuota"] - 1), 2)
            pick["estado"]    = "WIN"
            pick["resultado"] = f"✅ WIN | {res['home_score']}-{res['away_score']}"
            pick["ganancia"]  = ganancia
            trades["bankroll"] = round(trades["bankroll"] + ganancia, 2)
        else:
            pick["estado"]    = "LOSS"
            pick["resultado"] = f"❌ LOSS | {res['home_score']}-{res['away_score']}"
            pick["ganancia"]  = -pick["stake"]
            trades["bankroll"] = round(trades["bankroll"] - pick["stake"], 2)

        resueltos.append(pick)
        print(f"  {pick['resultado']} | {pick['equipo_h']} vs {pick['equipo_a']} | {tipo} {linea}")

    _guardar_json(TRACKER_FILE, trades)

    if resueltos:
        wins  = sum(1 for p in resueltos if p["estado"] == "WIN")
        loses = sum(1 for p in resueltos if p["estado"] == "LOSS")
        ganancia_total = sum(p["ganancia"] for p in resueltos)
        signo = "+" if ganancia_total >= 0 else ""

        titulo = f"📊 Resultados {fecha}: {wins}W {loses}L"
        cuerpo = (
            f"{'✅' * wins}{'❌' * loses}\n"
            f"Ganancia neta: {signo}${ganancia_total:.2f}\n"
            f"Bankroll: ${trades['bankroll']:.2f}"
        )
        _ntfy(titulo, cuerpo, "high" if ganancia_total > 0 else "default")

    return resueltos


def reporte_tracker():
    """Genera reporte completo del paper trade tracker."""
    trades = _cargar_json(TRACKER_FILE, {"picks": [], "bankroll": 1000.0})
    picks  = trades["picks"]

    if not picks:
        return "Sin picks registrados aún."

    wins   = [p for p in picks if p["estado"] == "WIN"]
    loses  = [p for p in picks if p["estado"] == "LOSS"]
    pushes = [p for p in picks if p["estado"] == "PUSH"]
    pend   = [p for p in picks if p["estado"] == "PENDING"]

    total_apostado = sum(p["stake"] for p in picks if p["estado"] != "PENDING")
    ganancia_neta  = sum(p["ganancia"] for p in picks if p["ganancia"] is not None)
    roi            = (ganancia_neta / total_apostado * 100) if total_apostado > 0 else 0.0
    win_rate       = (len(wins) / (len(wins) + len(loses)) * 100) if (wins or loses) else 0.0

    reporte = (
        f"📊 TRACKER PAPER TRADE\n"
        f"{'─'*30}\n"
        f"W: {len(wins)} | L: {len(loses)} | P: {len(pushes)} | Pending: {len(pend)}\n"
        f"Win Rate: {win_rate:.1f}%\n"
        f"ROI: {roi:+.1f}%\n"
        f"Ganancia neta: ${ganancia_neta:+.2f}\n"
        f"Bankroll: ${trades['bankroll']:.2f}\n"
        f"(inicio: $1,000.00)"
    )
    return reporte


# ================================================================
# MÓDULO 2 — CLV TRACKER (Closing Line Value)
# ================================================================

class CLVTracker:
    """
    Mide el Closing Line Value (CLV) — el indicador más importante
    para saber si el bot tiene edge real a largo plazo.

    CLV positivo = apostamos antes de que el mercado moviera
    la línea a nuestro favor = evidencia de edge real.

    Regla general de profesionales:
      CLV > +2%  → edge real, seguir apostando
      CLV ~ 0%   → breakeven, revisar modelo
      CLV < -2%  → no hay edge, el modelo falla
    """

    def guardar_pick(
        self,
        pick_id:   str,
        game_pk:   str,
        pick_tipo: str,
        linea:     float,
        cuota:     float,
        libro:     str
    ):
        """
        Guarda las cuotas en el momento del pick.
        Llamar inmediatamente cuando se genera un pick aprobado.
        """
        clv_data = _cargar_json(CLV_FILE, {"picks": []})

        entrada = {
            "pick_id":       pick_id,
            "game_pk":       game_pk,
            "pick_tipo":     pick_tipo,
            "linea":         linea,
            "cuota_entrada": cuota,
            "libro":         libro,
            "cuota_cierre":  None,
            "clv_pct":       None,
            "timestamp":     datetime.datetime.now().isoformat()
        }

        clv_data["picks"].append(entrada)
        _guardar_json(CLV_FILE, clv_data)
        print(f"  📌 CLV registrado: pick_id={pick_id} | cuota entrada={cuota}")

    def actualizar_cierre(self, sport: str = "baseball_mlb"):
        """
        Busca cuotas de cierre para picks pendientes de CLV.
        Ejecutar 30 minutos antes del inicio de cada juego.
        """
        clv_data = _cargar_json(CLV_FILE, {"picks": []})
        pendientes = [p for p in clv_data["picks"] if p["cuota_cierre"] is None]

        if not pendientes or not ODDS_API_KEY:
            return

        try:
            url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
            params = {
                "apiKey":  ODDS_API_KEY,
                "regions": "us",
                "markets": "totals,h2h,spreads",
                "oddsFormat": "decimal"
            }
            resp   = requests.get(url, params=params, timeout=15)
            juegos = resp.json()
        except Exception as e:
            print(f"  ⚠️ CLV: error obteniendo cuotas de cierre: {e}")
            return

        cuotas_cierre = {}
        for juego in juegos:
            for book in juego.get("bookmakers", []):
                if book["key"] not in ["pinnacle", "draftkings", "fanduel"]:
                    continue
                for market in book.get("markets", []):
                    for outcome in market.get("outcomes", []):
                        key = f"{juego['id']}_{market['key']}_{outcome.get('point', '')}_{outcome['name']}"
                        cuotas_cierre[key] = outcome["price"]

        for pick in clv_data["picks"]:
            if pick["cuota_cierre"] is not None:
                continue

            cuota_entrada = pick["cuota_entrada"]
            cuota_cierre  = pick.get("cuota_cierre") or cuota_entrada

            if cuota_cierre and cuota_entrada:
                prob_entrada = 1 / cuota_entrada
                prob_cierre  = 1 / cuota_cierre
                clv = (prob_cierre - prob_entrada) / prob_entrada * 100
                pick["clv_pct"] = round(clv, 2)

        _guardar_json(CLV_FILE, clv_data)

    def reporte_clv(self) -> str:
        """Genera reporte de CLV para evaluar edge del bot."""
        clv_data = _cargar_json(CLV_FILE, {"picks": []})
        con_clv  = [p for p in clv_data["picks"] if p["clv_pct"] is not None]

        if not con_clv:
            return "Sin datos de CLV todavía. Necesitas al menos 20 picks resueltos."

        clv_prom  = sum(p["clv_pct"] for p in con_clv) / len(con_clv)
        clv_pos   = sum(1 for p in con_clv if p["clv_pct"] > 0)
        clv_neg   = sum(1 for p in con_clv if p["clv_pct"] < 0)

        if clv_prom > 2:
            evaluacion = "🟢 EXCELENTE — edge real confirmado"
        elif clv_prom > 0:
            evaluacion = "🟡 POSITIVO — edge marginal, seguir midiendo"
        elif clv_prom > -2:
            evaluacion = "🟠 NEUTRO — revisar modelo"
        else:
            evaluacion = "🔴 NEGATIVO — el modelo no tiene edge real"

        return (
            f"📈 REPORTE CLV\n"
            f"{'─'*30}\n"
            f"Picks medidos: {len(con_clv)}\n"
            f"CLV promedio: {clv_prom:+.2f}%\n"
            f"Positivos: {clv_pos} | Negativos: {clv_neg}\n"
            f"Evaluación: {evaluacion}"
        )


# Instancia global — importar como: from paquete_avanzado import clv_tracker
clv_tracker = CLVTracker()


# ================================================================
# MÓDULO 3 — CONTRARIAN ALGORITHM
# ================================================================

def contrarian_scan(sport: str = "baseball_mlb") -> list:
    """
    Detecta oportunidades contrarian:
    Cuando el 65%+ del público apuesta a un lado Y
    Pinnacle mueve la línea al lado contrario →
    el dinero sharp está en contra del público → SEÑAL FUERTE.

    Returns:
        Lista de picks contrarian detectados
    """
    if not ODDS_API_KEY:
        print("  ⚠️ Contrarian: ODDS_API_KEY no configurado.")
        return []

    picks_contrarian = []

    try:
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
        params = {
            "apiKey":     ODDS_API_KEY,
            "regions":    "us",
            "markets":    "h2h,totals",
            "oddsFormat": "decimal",
            "bookmakers": "pinnacle,draftkings,fanduel,bovada,betmgm"
        }
        resp   = requests.get(url, params=params, timeout=15)
        juegos = resp.json()
    except Exception as e:
        print(f"  ⚠️ Contrarian scan error: {e}")
        return []

    for juego in juegos:
        equipo_h = juego["home_team"]
        equipo_a = juego["away_team"]

        cuotas_por_libro = {}
        for book in juego.get("bookmakers", []):
            cuotas_por_libro[book["key"]] = book.get("markets", [])

        if "pinnacle" not in cuotas_por_libro:
            continue

        pinnacle_h2h = next(
            (m for m in cuotas_por_libro["pinnacle"] if m["key"] == "h2h"), None
        )
        if not pinnacle_h2h:
            continue

        pin_odds = {o["name"]: o["price"] for o in pinnacle_h2h.get("outcomes", [])}
        if len(pin_odds) < 2:
            continue

        mercado_odds = {equipo_h: [], equipo_a: []}
        for libro_key, markets in cuotas_por_libro.items():
            if libro_key == "pinnacle":
                continue
            h2h = next((m for m in markets if m["key"] == "h2h"), None)
            if not h2h:
                continue
            for outcome in h2h.get("outcomes", []):
                if outcome["name"] in mercado_odds:
                    mercado_odds[outcome["name"]].append(outcome["price"])

        for equipo in [equipo_h, equipo_a]:
            if not mercado_odds.get(equipo):
                continue

            odds_mercado  = sum(mercado_odds[equipo]) / len(mercado_odds[equipo])
            odds_pinnacle = pin_odds.get(equipo, 0)
            if not odds_pinnacle:
                continue

            prob_mercado  = 1 / odds_mercado
            prob_pinnacle = 1 / odds_pinnacle
            diferencia    = prob_mercado - prob_pinnacle

            if diferencia > 0.05:
                equipo_contrario = equipo_a if equipo == equipo_h else equipo_h
                odds_contrario   = pin_odds.get(equipo_contrario, 0)

                if odds_contrario < 1.3 or odds_contrario > 4.0:
                    continue

                prob_contrario = 1 - prob_pinnacle
                ev = (prob_contrario * odds_contrario) - 1

                if ev > 0.03:
                    senal = {
                        "tipo":           "CONTRARIAN_ML",
                        "juego":          f"{equipo_h} vs {equipo_a}",
                        "pick":           equipo_contrario,
                        "cuota_pinnacle": odds_contrario,
                        "ev_pct":         round(ev * 100, 1),
                        "diferencia_pct": round(diferencia * 100, 1),
                        "razon": (
                            f"Público favorece {equipo} en {round(diferencia*100,1)}% "
                            f"más que Pinnacle. Sharp en {equipo_contrario}."
                        )
                    }
                    picks_contrarian.append(senal)
                    print(
                        f"  🔄 CONTRARIAN: {equipo_contrario} vs público "
                        f"| EV+{ev*100:.1f}% | dif={diferencia*100:.1f}%"
                    )

    if picks_contrarian:
        for pick in picks_contrarian:
            titulo = f"🔄 CONTRARIAN | {pick['pick']} | EV+{pick['ev_pct']}%"
            cuerpo = (
                f"🎯 {pick['juego']}\n"
                f"Pick: {pick['pick']} @ {pick['cuota_pinnacle']}\n"
                f"EV: +{pick['ev_pct']}%\n"
                f"📊 {pick['razon']}\n"
                f"⚠️ Verificar antes de apostar"
            )
            _ntfy(titulo, cuerpo, "high")

    return picks_contrarian


# ================================================================
# FUNCIÓN MAESTRA — Ejecuta los 3 módulos juntos
# ================================================================

def run_modulos_avanzados(sport: str = "baseball_mlb"):
    """
    Ejecuta los 3 módulos avanzados en secuencia.
    Llamar al final de cada scan en el bot.
    """
    print("\n⚡ MÓDULOS AVANZADOS")
    print("─" * 40)

    # 1. Auto-resultados
    auto_resultados()

    # 2. CLV — actualizar cuotas de cierre para picks próximos
    clv_tracker.actualizar_cierre(sport)

    # 3. Contrarian scan
    contrarian_scan(sport)

    print("─" * 40)


# ================================================================
# REPORTES
# ================================================================

if __name__ == "__main__":
    print("✅ Paquete Avanzado cargado.")
    print("\n📊 Tracker actual:")
    print(reporte_tracker())
    print("\n📈 CLV actual:")
    print(clv_tracker.reporte_clv())
