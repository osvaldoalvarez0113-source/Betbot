# ================================================================
# BETBOT — TELEGRAM BOT MODULE
# Polling loop que escucha comandos y responde via Telegram Bot API.
# Corre en hilo daemon — nunca bloquea el scan loop principal.
#
# Comandos:
#   /start     — registra tu chat_id y activa notificaciones
#   /ayuda     — lista de comandos disponibles
#   /picks     — picks pendientes de hoy (paper trades)
#   /bankroll  — balance actual del bankroll paper
#   /reporte   — reporte completo W/L/ROI/win-rate
#   /clv       — reporte de Closing Line Value (edge real)
#   /estado    — salud del bot (módulos activos, último scan)
#   /analizar  — /analizar Cubs vs Cardinals → análisis completo
#
# Env vars requeridas:
#   TELEGRAM_TOKEN    — token del bot (BotFather)
#   TELEGRAM_CHAT_ID  — ID del chat autorizado (se captura en /start)
# ================================================================

import os
import json
import time
import sys
import threading
import traceback as _traceback
import datetime
import urllib.request
from zoneinfo import ZoneInfo
import urllib.parse
import urllib.error

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CHATID_FILE      = "telegram_chat_id.txt"
TZ_CT            = ZoneInfo("America/Chicago")
try:
    from contexto_juego import resumen_contexto_telegram as _resumen_contexto_fn
    _HAS_CONTEXTO = True
except ImportError:
    _resumen_contexto_fn = None
    _HAS_CONTEXTO = False
TRACKER_FILE     = "paper_trades.json"
CLV_FILE         = "clv_tracker.json"
BETS_TODAY_FILE  = "bets_today.json"
BETS_LOG_FILE    = "bets_log.csv"
LOCK_FILE        = "/tmp/betbot_telegram.lock"   # previene error 409 por instancias duplicadas

# ── Diagnóstico al cargar el módulo ────────────────────────────
if TELEGRAM_TOKEN:
    print(f"  [telegram_bot] ✅ TELEGRAM_TOKEN configurado (termina en ...{TELEGRAM_TOKEN[-6:]})")
else:
    print("  [telegram_bot] ❌ TELEGRAM_TOKEN NO configurado — agrega la variable en Railway")
if TELEGRAM_CHAT_ID:
    print(f"  [telegram_bot] ✅ TELEGRAM_CHAT_ID: {TELEGRAM_CHAT_ID}")
else:
    print("  [telegram_bot] ⚠️  TELEGRAM_CHAT_ID no configurado — envía /start al bot para registrarte")

_authorized_ids: set = set()
_analyze_fn          = None
_get_odds_fn         = None
_build_text_fn       = None
_get_hoy_fn          = None
_get_patrones_fn     = None
_start_time          = datetime.datetime.now()
_last_scan_time      = None   # updated by kelly_odds integration if desired
_last_analizar_chat_id: str = ""   # last chat_id that triggered /analizar (for crash notifications)


# ── Crash diagnostics: captura CUALQUIER excepción no atrapada ──────────────
def _mem_rss_mb() -> float:
    """Retorna memoria RSS actual del proceso en MB, o -1 si no disponible."""
    try:
        import resource as _res
        return round(_res.getrusage(_res.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except Exception:
        try:
            with open("/proc/self/status") as _f:
                for line in _f:
                    if line.startswith("VmRSS:"):
                        return round(int(line.split()[1]) / 1024, 1)
        except Exception:
            pass
        return -1.0


def _notify_analizar_error(label: str = ""):
    """
    Intenta enviar '⚠️ Error en el análisis, intenta de nuevo' al último chat_id
    que ejecutó /analizar.  Solo se llama desde los exception hooks — nunca bloquea.
    """
    cid = _last_analizar_chat_id
    if not cid:
        return
    try:
        msg = "⚠️ Error en el análisis, intenta de nuevo"
        if label:
            msg += f" [{label}]"
        _api("sendMessage", {"chat_id": cid, "text": msg, "parse_mode": "HTML"})
    except Exception:
        pass


def _global_excepthook(exc_type, exc_value, exc_tb):
    """Captura excepciones no manejadas en el hilo PRINCIPAL."""
    print(f"\n  💥 CRASH HILO PRINCIPAL [{exc_type.__name__}]: {exc_value}")
    print(f"     Memoria RSS: {_mem_rss_mb()} MB")
    _traceback.print_exception(exc_type, exc_value, exc_tb)
    _notify_analizar_error(exc_type.__name__)
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _global_excepthook


def _thread_excepthook(args):
    """Captura excepciones no manejadas en CUALQUIER hilo (Python ≥3.8)."""
    if args.exc_type in (SystemExit, KeyboardInterrupt):
        return  # No logear estas — son shutdown normal
    tname = getattr(args.thread, "name", "?")
    print(f"\n  💥 CRASH HILO DAEMON '{tname}' [{args.exc_type.__name__}]: {args.exc_value}")
    print(f"     Memoria RSS: {_mem_rss_mb()} MB")
    _traceback.print_tb(args.exc_tb)
    _notify_analizar_error(args.exc_type.__name__)


try:
    threading.excepthook = _thread_excepthook
except AttributeError:
    pass   # Python < 3.8 — no disponible, sin problema


# ── Telegram API helpers ────────────────────────────────────────

def _api(method: str, params: dict = None, timeout: int = 35) -> dict:
    if not TELEGRAM_TOKEN:
        return {}
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    data = urllib.parse.urlencode(params or {}).encode()
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            status = r.status
            parsed = json.loads(raw)
            if not parsed.get("ok"):
                print(f"  ⚠️  Telegram API [{method}] HTTP {status} ok=false: "
                      f"{raw.decode('utf-8', errors='replace')[:400]}")
            return parsed
    except urllib.error.HTTPError as http_err:
        # Telegram devuelve 4xx con JSON de error — necesitamos leer el cuerpo
        try:
            body = http_err.read().decode("utf-8", errors="replace")
        except Exception:
            body = "(no se pudo leer el cuerpo)"
        print(f"  ⚠️  Telegram API [{method}] HTTP {http_err.code}: {body[:400]}")
        return {"ok": False, "_http_code": http_err.code, "_body": body}
    except Exception as e:
        print(f"  ⚠️  Telegram API [{method}] excepción [{type(e).__name__}]: {e}")
        return {}


_TG_SAFE_LEN = 3900   # safe margin below Telegram's 4096-char hard limit

def _send(chat_id, text: str, parse_mode: str = "HTML"):
    """Send a single message. Logs a warning and truncates if text > _TG_SAFE_LEN.
    For messages that may be long, use _send_long() instead."""
    if len(text) > _TG_SAFE_LEN:
        print(f"  ⚠️  _send: mensaje de {len(text)} chars truncado a {_TG_SAFE_LEN} "
              f"— considera usar _send_long() en el llamador")
    _api("sendMessage", {
        "chat_id":    chat_id,
        "text":       text[:_TG_SAFE_LEN],
        "parse_mode": parse_mode,
    })


def _split_message(text: str, max_len: int = _TG_SAFE_LEN) -> list:
    """
    Divide `text` en chunks ≤ max_len chars respetando límites lógicos del mensaje.
    Prioriza cortes en separadores de sección (─── líneas), luego doble salto de
    línea, luego salto simple. Solo hace hard-cut como último recurso.
    Nunca devuelve chunks vacíos.
    """
    if len(text) <= max_len:
        return [text]

    chunks = []
    remaining = text

    while len(remaining) > max_len:
        window = remaining[:max_len]

        # 1. Buscar separador de sección (────) — corta antes de la siguiente sección
        idx_sep = window.rfind("\n─")
        if idx_sep > max_len // 4:
            cut = idx_sep          # preserva el salto previo, la nueva sección va al siguiente chunk
        else:
            # 2. Doble salto de línea (separador de párrafo)
            idx_pp = window.rfind("\n\n")
            if idx_pp > max_len // 4:
                cut = idx_pp + 1   # incluye el primer \n, el segundo inicia el siguiente chunk
            else:
                # 3. Salto simple de línea
                idx_p = window.rfind("\n")
                if idx_p > max_len // 4:
                    cut = idx_p
                else:
                    cut = max_len  # último recurso: hard-cut

        chunk = remaining[:cut].rstrip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[cut:].lstrip("\n")

    if remaining.strip():
        chunks.append(remaining.strip())

    return chunks or [text[:max_len]]


def _send_long(chat_id: str, text: str, parse_mode: str = "HTML",
               _caller: str = ""):
    """
    Envía texto que puede exceder el límite de 4096 chars de Telegram.
    Divide el mensaje en partes lógicas y las envía consecutivamente.
    Usar en lugar de _send() para: resultados de build_analizar_text,
    listas de picks, bulk analysis, o cualquier texto generado dinámicamente.
    """
    parts = _split_message(text)
    tag   = f"[{_caller}] " if _caller else ""
    print(f"  📨 {tag}_send_long → chat_id={chat_id!r} | "
          f"{len(text)} chars → {len(parts)} parte(s) "
          f"({', '.join(str(len(p)) for p in parts)} chars)")
    for i, part in enumerate(parts, 1):
        if not part:
            continue
        print(f"  📨 {tag}Enviando parte {i}/{len(parts)} a Telegram… ({len(part)} chars)")
        resp = _api("sendMessage", {
            "chat_id":    chat_id,
            "text":       part,
            "parse_mode": parse_mode,
        })
        ok       = resp.get("ok", False)
        http_c   = resp.get("_http_code", 200 if ok else "?")
        msg_id   = (resp.get("result") or {}).get("message_id", "?")
        err_desc = resp.get("description", resp.get("_body", ""))
        if ok:
            print(f"  ✅ {tag}Respuesta de Telegram parte {i}: HTTP {http_c} OK "
                  f"(message_id={msg_id})")
        else:
            print(f"  ❌ {tag}Respuesta de Telegram parte {i}: HTTP {http_c} FALLÓ — "
                  f"description={err_desc!r} | resp={resp}")


# ── Chat ID management ──────────────────────────────────────────

def _load_authorized():
    global TELEGRAM_CHAT_ID
    ids = set()
    if TELEGRAM_CHAT_ID:
        for cid in str(TELEGRAM_CHAT_ID).split(","):
            cid = cid.strip()
            if cid:
                ids.add(cid)
    try:
        with open(CHATID_FILE, "r") as f:
            for line in f:
                cid = line.strip()
                if cid:
                    ids.add(cid)
    except FileNotFoundError:
        pass
    return ids


def _save_chatid(chat_id: str):
    existing = _load_authorized()
    existing.add(str(chat_id))
    with open(CHATID_FILE, "w") as f:
        f.write("\n".join(existing))
    print(f"  📱 Telegram: chat_id {chat_id} guardado en {CHATID_FILE}")
    print(f"  📌 Agrega a Railway: TELEGRAM_CHAT_ID={chat_id}")


def _is_authorized(chat_id: str) -> bool:
    if not _authorized_ids:
        return True
    return str(chat_id) in _authorized_ids


# ── JSON helpers ────────────────────────────────────────────────

def _load_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


# ── Command handlers ────────────────────────────────────────────

def _cmd_start(chat_id: str):
    _save_chatid(chat_id)
    _authorized_ids.add(str(chat_id))
    _send(chat_id, (
        "🤖 <b>BetBot Pro activado</b>\n\n"
        f"Tu chat_id: <code>{chat_id}</code>\n\n"
        "⚠️ Guarda ese ID como variable de entorno en Railway:\n"
        f"<code>TELEGRAM_CHAT_ID={chat_id}</code>\n\n"
        "Usa /ayuda para ver los comandos disponibles."
    ))


def _cmd_ayuda(chat_id: str):
    _send(chat_id, (
        "📋 <b>Comandos disponibles</b>\n\n"
        "⚾ <b>MLB:</b>\n"
        "/picks — Top 5 mejores picks MLB de hoy\n"
        "/mlb — Análisis completo de todos los partidos MLB\n"
        "/parlay — Mejor parlay del día MLB\n"
        "/hoy — Resumen rápido MLB con pitchers\n"
        "/analizar <code>Equipo A vs Equipo B</code> — análisis completo\n"
        "/kprops <code>Pitcher | Rival | Línea | Over/Under | Cuota</code> — props de ponches\n\n"
        "💰 <b>Bankroll:</b>\n"
        "/aposte <code>Pick $monto</code> — registrar apuesta\n"
        "/historial — tus apuestas de hoy con P&L\n"
        "/resultado <code>Equipo W/L</code> — cerrar apuesta manualmente\n"
        "/bankroll — balance actual\n"
        "/clv — Closing Line Value\n\n"
        "📊 <b>Reportes:</b>\n"
        "/reporte — reporte completo W/L/ROI\n"
        "/estado — resumen operativo del día (picks activos, bankroll)\n"
        "/salud — diagnóstico técnico de APIs, archivos y estado de los modelos\n"
        "/elite <code>Local vs Visitante</code> — análisis elite (claude-fable-5)\n\n"
        "ℹ️ /ayuda — esta lista"
    ))


def _cmd_mispicks(chat_id: str):
    """Shows pending picks from the local tracker (original /picks behavior)."""
    trades = _load_json(TRACKER_FILE, {"picks": []})
    hoy    = datetime.date.today().isoformat()
    pend   = [p for p in trades.get("picks", []) if p.get("estado") == "PENDING"
              and p.get("fecha") == hoy]
    if not pend:
        _send(chat_id, "ℹ️ Sin picks pendientes para hoy.")
        return
    lines = [f"📋 <b>Picks pendientes hoy ({hoy})</b>\n"]
    for p in pend:
        tipo  = p.get("pick_tipo", "?")
        linea = p.get("linea", "")
        cuota = p.get("cuota", "")
        stake = p.get("stake", "")
        libro = p.get("libro", "")
        linea_str = f" {linea}" if linea else ""
        lines.append(
            f"• {p['equipo_h']} vs {p['equipo_a']}\n"
            f"  {tipo}{linea_str} @ {cuota} | ${stake} | {libro}"
        )
    _send(chat_id, "\n".join(lines))


def _enforce_market_diversity(picks_list, max_totals=2):
    """Limita a max_totals picks de OVER/UNDER; preserva ML, RL, F5."""
    if not picks_list:
        return picks_list
    try:
        TOTAL_KEYWORDS = {'OVER', 'UNDER', 'TOTAL'}

        def _is_total(p):
            for key in ('market', 'tipo', 'label'):
                val = str(p.get(key) or '').upper()
                if any(kw in val for kw in TOTAL_KEYWORDS):
                    return True
            return False

        totals     = [p for p in picks_list if _is_total(p)]
        non_totals = [p for p in picks_list if not _is_total(p)]
        before = len(totals)
        if len(totals) > max_totals:
            totals.sort(key=lambda x: float(x.get('ev', 0) or 0), reverse=True)
            totals = totals[:max_totals]
        result = non_totals + totals
        print(f"[DIVERSITY] Totals antes={before} | después={len(totals)} | "
              f"ML/RL/F5={len(non_totals)} | total picks={len(result)}")
        return result
    except Exception as e:
        print(f"[DIVERSITY] Error: {e}")
        return picks_list


def _cmd_best_picks(chat_id: str, sport_key: str, emoji: str, label: str):
    """Analyze all games for sport_key, return top-5 picks by EV in one message."""
    if not _get_odds_fn or not _analyze_fn:
        _send(chat_id, "⚠️ Módulo de análisis no disponible (bot en modo básico).")
        return

    _send(chat_id, f"🔍 Buscando los mejores picks {label} de hoy...")

    try:
        games = _get_odds_fn(sport_key) or []
    except Exception as e:
        _send(chat_id, f"⚠️ Error consultando la API de odds: {e}.\nRevisa quota o API key.")
        return

    if not games:
        _et_hoy = datetime.datetime.now(ZoneInfo("America/New_York")).strftime("%d/%m")
        _send(chat_id, f"📅 No hay partidos para hoy ({_et_hoy} ET).")
        return

    RANKS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    all_picks = []  # list of dicts with pick data

    for game in sorted(games, key=lambda g: g.get("commence_time", ""))[:20]:
        home = game.get("home_team", "?")
        away = game.get("away_team", "?")
        try:
            result = _analyze_fn(game, sport_key, {}, force_panel=False)
        except Exception:
            continue
        if not result:
            continue

        intel     = result.get("claude_intel") or {}
        panel_ok  = intel.get("apostar") is True
        confianza = intel.get("confianza", "")
        razon_raw = intel.get("razonamiento", "")
        # Shorten reasoning to ~80 chars
        razon = (razon_raw[:77] + "…") if len(razon_raw) > 80 else razon_raw

        # Count expert votes from individual responses if present
        votos_str = ""
        expertos  = intel.get("expertos") or []
        if expertos:
            si_count = sum(1 for ex in expertos if ex.get("apostar") is True)
            votos_str = f"{si_count}/{len(expertos)} expertos"
        elif confianza:
            votos_str = confianza

        for cand in result.get("candidates") or []:
            ev = cand.get("ev_pct", 0)
            if ev <= 0:
                continue
            all_picks.append({
                "match":      result.get("match", f"{home} vs {away}"),
                "label":      cand.get("label", "?"),
                "odds":       cand.get("odds", 0),
                "book":       cand.get("book", ""),
                "prob":       round(cand.get("true_prob", 0) * 100, 1),
                "ev":         ev,
                "panel_ok":   panel_ok,
                "votos":      votos_str,
                "razon":      razon,
            })

    if not all_picks:
        _send(chat_id,
              f"Sin picks con valor hoy en {label} — el modelo protege tu bankroll 🔒")
        return

    # Sort: panel-approved first, then by EV descending
    all_picks.sort(key=lambda x: (not x["panel_ok"], -x["ev"]))
    if all_picks:
        all_picks = _enforce_market_diversity(all_picks, max_totals=2)
        print(f"[MAIN] Picks después de diversificación: {len(all_picks)}")
    top5 = all_picks[:5]

    rank_emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    header = (f"{emoji} <b>TOP {len(top5)} PICKS {label.upper()} HOY</b>"
              + (f"\n<i>Solo {len(all_picks)} picks con valor hoy</i>"
                 if len(all_picks) < 5 else "") + "\n")
    lines  = [header]

    for i, pk in enumerate(top5):
        panel_icon = "✅" if pk["panel_ok"] else "⚠️"
        votos_line = f"\n   Panel: {panel_icon} {pk['votos']}" if pk["votos"] else f"\n   Panel: {panel_icon}"
        razon_line = f"\n   <i>'{pk['razon']}'</i>" if pk["razon"] else ""
        lines.append(
            f"\n{rank_emoji[i]} <b>{pk['label']}</b> — {pk['match']}\n"
            f"   Odds: {pk['odds']:.2f} ({pk['book']})\n"
            f"   Prob: {pk['prob']}% | EV +{pk['ev']:.1f}%"
            f"{votos_line}{razon_line}"
        )

    _send_long(chat_id, "\n".join(lines))


def _cmd_picks(chat_id: str):
    _cmd_best_picks(chat_id, "baseball_mlb", "⚾", "MLB")


def _cmd_bankroll(chat_id: str):
    import csv as _csv
    trades   = _load_json(TRACKER_FILE, {"picks": [], "bankroll": 1000.0})
    bankroll = trades.get("bankroll", 1000.0)
    picks    = trades.get("picks", [])
    wins_l   = [p for p in picks if p.get("estado") == "WIN"]
    loses_l  = [p for p in picks if p.get("estado") == "LOSS"]
    pushes_l = [p for p in picks if p.get("estado") == "PUSH"]
    pend_l   = [p for p in picks if p.get("estado") == "PENDING"]

    # Financial stats
    ganancia_total = sum(p.get("ganancia", 0) or 0 for p in picks)
    total_apost    = sum(p.get("stake", 0) or 0 for p in picks
                        if p.get("estado") not in ("PENDING",))
    roi            = (ganancia_total / total_apost * 100) if total_apost > 0 else 0.0
    win_rate       = (len(wins_l) / (len(wins_l) + len(loses_l)) * 100
                      if (wins_l or loses_l) else 0.0)

    # Today's gains from bets_log.csv
    today_str  = datetime.datetime.now(TZ_CT).strftime("%Y-%m-%d")
    hoy        = 0.0
    semana     = 0.0
    last_bet   = "—"
    try:
        if os.path.isfile(BETS_LOG_FILE):
            with open(BETS_LOG_FILE, newline="", encoding="utf-8") as _f:
                rows = list(_csv.DictReader(_f))
            from datetime import date, timedelta
            week_start = date.today() - timedelta(days=date.today().weekday())
            for row in rows:
                row_date = (row.get("date") or row.get("timestamp") or "")[:10]
                gain     = float(row.get("ganancia") or row.get("profit") or 0)
                if row_date == today_str:
                    hoy += gain
                if row_date >= str(week_start):
                    semana += gain
            if rows:
                _lr = rows[-1]
                last_bet = (f"{_lr.get('pick','?')} @ {_lr.get('book','?')} "
                            f"— ${float(_lr.get('stake',0)):.0f}")
    except Exception:
        pass

    signo_t = "+" if ganancia_total >= 0 else ""
    signo_h = "+" if hoy >= 0 else ""
    signo_s = "+" if semana >= 0 else ""
    emoji   = "📈" if ganancia_total >= 0 else "📉"
    div     = "━" * 20

    _send(chat_id, (
        f"💰 <b>ESTADO DEL BANKROLL</b>\n"
        f"{div}\n"
        f"💵 Bankroll actual:  <b>${bankroll:,.2f}</b>\n"
        f"📈 Ganancia de hoy:  {signo_h}${hoy:.2f}\n"
        f"📊 Esta semana:      {signo_s}${semana:.2f}\n"
        f"🏆 Total acumulado:  {signo_t}${ganancia_total:.2f}\n"
        f"{div}\n"
        f"📋 Récord: {len(wins_l)} ganadas – {len(loses_l)} perdidas – {len(pushes_l)} empujadas\n"
        f"📉 Tasa de acierto: {win_rate:.1f}%\n"
        f"💹 ROI total: {roi:+.1f}%\n"
        f"{div}\n"
        f"Última apuesta registrada: {last_bet}"
    ))


def _cmd_reporte(chat_id: str):
    try:
        from paquete_avanzado import reporte_tracker
        txt = reporte_tracker()
    except Exception:
        trades = _load_json(TRACKER_FILE, {"picks": [], "bankroll": 1000.0})
        picks  = trades.get("picks", [])
        wins   = [p for p in picks if p.get("estado") == "WIN"]
        loses  = [p for p in picks if p.get("estado") == "LOSS"]
        pushes = [p for p in picks if p.get("estado") == "PUSH"]
        pend   = [p for p in picks if p.get("estado") == "PENDING"]
        total_apost = sum(p.get("stake", 0) for p in picks if p.get("estado") != "PENDING")
        ganancia    = sum(p.get("ganancia", 0) or 0 for p in picks)
        roi         = (ganancia / total_apost * 100) if total_apost > 0 else 0.0
        win_rate    = (len(wins) / (len(wins) + len(loses)) * 100) if (wins or loses) else 0.0
        txt = (
            f"📊 TRACKER PAPER TRADE\n"
            f"{'─'*28}\n"
            f"W: {len(wins)} | L: {len(loses)} | P: {len(pushes)} | Pending: {len(pend)}\n"
            f"Win Rate: {win_rate:.1f}%\n"
            f"ROI: {roi:+.1f}%\n"
            f"Ganancia neta: ${ganancia:+.2f}\n"
            f"Bankroll: ${trades.get('bankroll', 1000):.2f}"
        )
    _send(chat_id, f"<pre>{txt}</pre>")


def _cmd_clv(chat_id: str):
    import csv as _csv, os as _os
    CLV_CSV = "clv_log.csv"

    if not _os.path.exists(CLV_CSV):
        _send(chat_id, "📈 Sin datos de CLV todavía.\nLos picks registrados se trackean automáticamente.")
        return

    try:
        with_clv   = []   # rows where clv_pct is a valid number
        sin_dato   = 0    # rows missing closing odds

        with open(CLV_CSV, newline="", encoding="utf-8") as _f:
            for row in _csv.DictReader(_f):
                raw = row.get("clv_pct", "")
                if raw == "" or raw is None:
                    sin_dato += 1
                else:
                    try:
                        with_clv.append({
                            "match":   row.get("match", "?"),
                            "market":  row.get("market_type", ""),
                            "side":    row.get("bet_side", ""),
                            "clv_pct": float(raw),
                            "modelo":  row.get("modelo", "haiku"),
                        })
                    except Exception:
                        sin_dato += 1

        if not with_clv:
            _send(chat_id,
                  f"📈 CLV: sin datos calculados todavía.\n"
                  f"Picks sin línea de cierre: {sin_dato}")
            return

        prom      = sum(p["clv_pct"] for p in with_clv) / len(with_clv)
        positivos = sum(1 for p in with_clv if p["clv_pct"] > 0)
        negativos = sum(1 for p in with_clv if p["clv_pct"] <= 0)
        beat_rate = positivos / len(with_clv) * 100

        # Best and worst picks
        best  = max(with_clv, key=lambda x: x["clv_pct"])
        worst = min(with_clv, key=lambda x: x["clv_pct"])

        verdict = "💎 Modelo con edge real" if prom > 0 else "⚠️ Apostando en mal momento — revisar timing"

        # Desglose por modelo
        _haiku = [p for p in with_clv if p["modelo"] != "fable"]
        _fable = [p for p in with_clv if p["modelo"] == "fable"]
        _hprom = sum(p["clv_pct"] for p in _haiku) / len(_haiku) if _haiku else 0.0
        _fprom = sum(p["clv_pct"] for p in _fable) / len(_fable) if _fable else 0.0
        _model_block = (
            f"{'─'*32}\n"
            f"📊 Por modelo:\n"
            f"  haiku: {len(_haiku)} picks | CLV prom {_hprom:+.2f}%\n"
            f"  fable: {len(_fable)} picks | CLV prom {_fprom:+.2f}%"
        )

        txt = (
            f"📈 <b>REPORTE CLOSING LINE VALUE (CLV)</b>\n"
            f"{'─'*32}\n"
            f"Picks medidos:       {len(with_clv)}\n"
            f"Sin dato (excluidos):{sin_dato}\n"
            f"{'─'*32}\n"
            f"CLV promedio:        {prom:+.2f}%  {'✅' if prom > 0 else '❌'}\n"
            f"Beat rate cierre:    {beat_rate:.0f}%  "
            f"({positivos} ganaron / {negativos} no)\n"
            f"{'─'*32}\n"
            f"Mejor pick:  {best['match'][:28]}\n"
            f"             {best['side']} → CLV {best['clv_pct']:+.2f}%\n"
            f"Peor pick:   {worst['match'][:28]}\n"
            f"             {worst['side']} → CLV {worst['clv_pct']:+.2f}%\n"
            f"{'─'*32}\n"
            f"→ {verdict}\n"
            f"{_model_block}"
        )
    except Exception as e:
        txt = f"⚠️ Error leyendo CLV: {e}"

    _send(chat_id, txt)


def _cmd_estado(chat_id: str):
    """Resumen operativo del día: picks activos, bankroll, últimas apuestas."""
    uptime  = datetime.datetime.now() - _start_time
    horas   = int(uptime.total_seconds() // 3600)
    minutos = int((uptime.total_seconds() % 3600) // 60)
    trades  = _load_json(TRACKER_FILE, {"picks": [], "bankroll": 1000.0})
    hoy     = _ct_today()
    picks   = trades.get("picks", [])
    bankroll = trades.get("bankroll", 1000.0)

    pending = [p for p in picks if p.get("estado") == "PENDING" and p.get("fecha") == hoy]
    wins    = [p for p in picks if p.get("estado") == "WIN"     and p.get("fecha") == hoy]
    losses  = [p for p in picks if p.get("estado") == "LOSS"    and p.get("fecha") == hoy]

    pnl_hoy = sum(p.get("profit", 0) for p in picks
                  if p.get("fecha") == hoy and p.get("estado") in ("WIN", "LOSS", "PUSH"))

    lines = [
        f"📋 <b>ESTADO OPERATIVO — {hoy}</b>\n",
        f"🕐 Uptime: {horas}h {minutos}m\n",
        f"💰 Bankroll: <b>${bankroll:,.2f}</b>",
        f"📈 P&L hoy: {pnl_hoy:+.2f}\n",
        f"🎯 Picks hoy:",
        f"   ⏳ Pendientes: {len(pending)}",
        f"   ✅ Wins:       {len(wins)}",
        f"   ❌ Losses:     {len(losses)}",
    ]

    if pending:
        lines.append("\n<b>Pendientes:</b>")
        for p in pending[:5]:
            lines.append(f"  • {p.get('pick','?')}  ${p.get('monto',0):.0f}")

    _send(chat_id, "\n".join(lines))


def _cmd_salud(chat_id: str):
    import os as _os, json as _json
    _send(chat_id, "🏥 Verificando componentes... (5-10 seg)")
    _et_now = datetime.datetime.now(ZoneInfo("America/New_York"))
    lines   = [f"🏥 <b>SALUD DE BETBOT</b> — {_et_now.strftime('%d/%m %H:%M ET')}\n"]

    try:
        _ok = _os.environ.get("ODDS_API_KEY", "")
        if not _ok:
            lines.append("📡 The Odds API: ⚠️ ODDS_API_KEY no configurada")
        else:
            _url = f"https://api.the-odds-api.com/v4/sports?apiKey={urllib.parse.quote(_ok)}"
            with urllib.request.urlopen(urllib.request.Request(_url), timeout=5) as _r:
                _rem  = _r.headers.get("x-requests-remaining", "?")
                _used = _r.headers.get("x-requests-used",      "?")
            lines.append(f"📡 The Odds API: ✅ OK | {_rem} restantes / {_used} usadas")
    except Exception as _e:
        lines.append(f"📡 The Odds API: ❌ {str(_e)[:80]}")

    try:
        with urllib.request.urlopen(
                "https://statsapi.mlb.com/api/v1/sports/1", timeout=5) as _r:
            lines.append(f"⚾ MLB Stats API: ✅ OK (HTTP {_r.status})")
    except Exception as _e:
        lines.append(f"⚾ MLB Stats API: ❌ {str(_e)[:80]}")

    try:
        _om = ("https://api.open-meteo.com/v1/forecast"
               "?latitude=40.7&longitude=-74.0&hourly=temperature_2m&forecast_days=1")
        with urllib.request.urlopen(_om, timeout=5) as _r:
            lines.append(f"🌤️ Open-Meteo: ✅ OK (HTTP {_r.status})")
    except Exception as _e:
        lines.append(f"🌤️ Open-Meteo: ❌ {str(_e)[:80]}")

    try:
        _ntfy_t = _os.environ.get("NTFY_TOPIC", "my-bets")
        _req = urllib.request.Request(
            f"https://ntfy.sh/{_ntfy_t}",
            data=b"BetBot /salud ping",
            headers={"Title": "BetBot health", "Priority": "min", "Tags": "stethoscope"},
            method="POST",
        )
        with urllib.request.urlopen(_req, timeout=5) as _r:
            lines.append(f"🔔 ntfy ({_ntfy_t}): ✅ OK (HTTP {_r.status})")
    except Exception as _e:
        lines.append(f"🔔 ntfy: ❌ {str(_e)[:80]}")

    try:
        _ak = _os.environ.get("ANTHROPIC_API_KEY", "")
        if not _ak:
            lines.append("🤖 Panel expertos (Anthropic): 💤 Sin API key configurada")
        elif not _analyze_fn:
            lines.append("🤖 Panel expertos (Anthropic): ⚠️ Módulo no cargado")
        else:
            lines.append("🤖 Panel expertos (Anthropic): ✅ Key OK y módulo activo")
    except Exception as _e:
        lines.append(f"🤖 Panel expertos: ❌ {str(_e)[:80]}")

    _flist = [("elo_ratings.json", "ELO ratings"),
              ("bets_log.csv",     "Log de picks"),
              ("bankroll_log.csv", "Bankroll log")]
    _flines = []
    for _fn, _lbl in _flist:
        try:
            if _os.path.isfile(_fn):
                _mt = datetime.datetime.fromtimestamp(_os.path.getmtime(_fn))
                _flines.append(f"  • {_lbl}: ✅ mod {_mt.strftime('%d/%m %H:%M')}")
            else:
                _flines.append(f"  • {_lbl}: ⚠️ No existe")
        except Exception as _fe:
            _flines.append(f"  • {_lbl}: ❌ {str(_fe)[:40]}")
    lines.append("💾 Datos locales:\n" + "\n".join(_flines))

    try:
        _sf = "/tmp/betbot_scan_status.json"
        if _os.path.isfile(_sf):
            with open(_sf) as _f:
                _ss = _json.load(_f)
            _icon  = "✅" if _ss.get("ok") else "❌"
            _extra = f" | {_ss['error'][:60]}" if not _ss.get("ok") and _ss.get("error") else ""
            lines.append(f"📊 Último scan: {_icon} {_ss.get('ts', '?')}{_extra}")
        else:
            lines.append("📊 Último scan: ⚠️ Sin datos (bot recién iniciado)")
    except Exception as _e:
        lines.append(f"📊 Último scan: ❌ {str(_e)[:80]}")

    # ── Fix verification section ──────────────────────────────────────────────
    import inspect as _inspect
    try:
        import kelly_odds as _ko
        _src = _inspect.getsource(_ko)

        # Fix 1: ERA del abridor confirmado vs promedio de equipo
        _fix1 = "✅" if "_pitcher_adjusted_ra" in _src else "❌"

        # Fix 2: Boost de localía direccional (signed_boost = boost if favored_is_home else -boost)
        _fix2 = "✅" if "signed_boost" in _src else "❌"

        # Fix 3: ELO seed desde win% temporada (no 50/50 falso)
        _fix3 = "✅" if "_ensure_mlb_elo_seeded" in _src else "❌"

        lines.append(
            f"\n🔬 <b>Estado de los 3 fixes del modelo:</b>\n"
            f"  {_fix1} ERA abridor confirmado (vs promedio equipo)\n"
            f"  {_fix2} Boost localía direccional (no siempre +local)\n"
            f"  {_fix3} ELO seed desde win% (no 50/50 falso)"
        )
    except Exception as _fe:
        lines.append(f"\n🔬 Fixes: ⚠️ No se pudo verificar ({str(_fe)[:60]})")

    _send(chat_id, "\n".join(lines))


def _cmd_elite(chat_id: str, args: str):
    if not args or " vs " not in args.lower():
        _send(chat_id,
              "⚠️ Formato: /elite <code>Equipo Local vs Equipo Visitante</code>\n"
              "Ejemplo: /elite Cubs vs Cardinals\n\n"
              "🧠 Fuerza análisis con modelo elite (sin límite de edge ni contador diario).")
        return

    if not _get_odds_fn or not _analyze_fn:
        _send(chat_id, "⚠️ Módulo de análisis no disponible.")
        return

    partes   = args.split(" vs ", 1)
    home_raw = partes[0].strip()
    away_raw = partes[1].strip()
    home_q   = _translate_team_name(home_raw).lower()
    away_q   = _translate_team_name(away_raw).lower()

    _send(chat_id, f"🧠 Análisis elite: buscando <b>{home_raw} vs {away_raw}</b>…")

    game_found  = None
    sport_found = "baseball_mlb"
    for sport in ["baseball_mlb", "soccer_fifa_world_cup",
                  "soccer_epl", "soccer_uefa_champs_league",
                  "soccer_usa_mls", "soccer_spain_la_liga"]:
        try:
            games = _get_odds_fn(sport, force_fresh=True)
            if not games:
                continue
            for g in games:
                gh = g.get("home_team", "")
                ga = g.get("away_team", "")
                if ((_team_words_match(home_q, gh) and _team_words_match(away_q, ga)) or
                        (_team_words_match(away_q, gh) and _team_words_match(home_q, ga))):
                    game_found  = g
                    sport_found = sport
                    break
            if game_found:
                break
        except Exception as _gse:
            print(f"  [elite] excepción buscando en {sport}: {_gse}")
            continue

    if not game_found:
        _send(chat_id,
              f"❌ No encontré el partido <b>{partes[0].strip()} vs {partes[1].strip()}</b>.")
        return

    try:
        result = _analyze_fn(game_found, sport_found, {}, force_panel=True,
                             _force_elite_panel=True)
    except Exception as e:
        _send(chat_id, f"⚠️ Error en el análisis elite: {e}")
        return

    if not result:
        _send(chat_id, "⚠️ No se pudo obtener análisis elite para este partido.")
        return

    if _build_text_fn:
        try:
            parts = _build_text_fn(result)
            for part in parts:
                if part and part.strip():
                    _send_long(chat_id, part)
        except Exception as _bte:
            _send(chat_id, f"⚠️ Error al formatear análisis elite: {_bte}")
    else:
        cands = result.get("candidates", [])
        best  = cands[0] if cands else {}
        pick  = result.get("best_label", best.get("label", "?"))
        ev    = result.get("best_ev",   best.get("ev_pct", 0))
        _send(chat_id, f"🧠 ANÁLISIS ELITE\n{home_raw} vs {away_raw}\nPick: {pick} | EV: +{ev:.1f}%")


def _team_words_match(query: str, team: str) -> bool:
    """True si cada palabra de `query` aparece como subcadena en `team` (case-insensitive).
    Ejemplo: _team_words_match("guardians", "Cleveland Guardians") → True
             _team_words_match("red sox",   "Boston Red Sox")      → True
    """
    t = team.lower()
    return all(w in t for w in query.lower().split())


_ES_TO_EN_TEAMS = {
    "corea del sur": "South Korea",
    "república checa": "Czech Republic",
    "chequia": "Czech Republic",
    "estados unidos": "United States",
    "países bajos": "Netherlands",
    "alemania": "Germany",
    "francia": "France",
    "españa": "Spain",
    "brasil": "Brazil",
    "marruecos": "Morocco",
    "japón": "Japan",
    "méxico": "Mexico",
    "panamá": "Panama",
    "bélgica": "Belgium",
    "croacia": "Croatia",
    "suiza": "Switzerland",
    "polonia": "Poland",
    "turquía": "Turkey",
    "irán": "Iran",
    "arabia saudita": "Saudi Arabia",
    "corea": "South Korea",
    "costa de marfil": "Ivory Coast",
    "rep. checa": "Czech Republic",
    "eslovaquia": "Slovakia",
    "eslovenia": "Slovenia",
    "rumania": "Romania",
    "dinamarca": "Denmark",
    "austria": "Austria",
    "hungría": "Hungary",
    "ucrania": "Ukraine",
    "portugal": "Portugal",
    "argentina": "Argentina",
    "uruguay": "Uruguay",
    "colombia": "Colombia",
    "ecuador": "Ecuador",
    "perú": "Peru",
    "paraguay": "Paraguay",
    "senegal": "Senegal",
    "nigeria": "Nigeria",
    "ghana": "Ghana",
    "camerún": "Cameroon",
    "argelia": "Algeria",
    "egipto": "Egypt",
    "canadá": "Canada",
    "australia": "Australia",
}


def _translate_team_name(name: str) -> str:
    return _ES_TO_EN_TEAMS.get(name.lower().strip(), name)


def _cmd_analizar(chat_id: str, args: str):
    """
    /analizar — análisis completo con panel de expertos.
    Cada etapa está envuelta con diagnóstico de memoria y traceback completo
    para que Railway logs muestren el punto exacto de cualquier crash.
    """
    # ── Validación inicial ────────────────────────────────────────────────────
    if not args or " vs " not in args.lower():
        _send(chat_id,
              "⚠️ Formato: /analizar <code>Equipo Local vs Equipo Visitante</code>\n"
              "Ejemplo: /analizar Cubs vs Cardinals")
        return

    if not _get_odds_fn or not _analyze_fn:
        _send(chat_id, "⚠️ Módulo de análisis no disponible (bot en modo básico).")
        return

    partes   = args.split(" vs ", 1)
    home_raw = partes[0].strip()
    away_raw = partes[1].strip()
    home_q   = _translate_team_name(home_raw).lower()
    away_q   = _translate_team_name(away_raw).lower()

    _mem0 = _mem_rss_mb()
    # ── Registrar chat_id para notificación de crash (PASO 4) ───────────────
    global _last_analizar_chat_id
    _last_analizar_chat_id = chat_id

    # ── PASO 2 — Verificar chat_id ──────────────────────────────────────────
    # _authorized_ids es el set en memoria (poblado al arrancar por iniciar_telegram).
    # _load_authorized() lee env + archivo para comparar contra el estado en memoria.
    _ids_disk = _load_authorized()
    print(f"  [analizar] INICIO {home_raw} vs {away_raw} | RSS={_mem0}MB")
    print(f"  [analizar] CHATID CHECK → "
          f"chat_id del update: {chat_id!r} | "
          f"_authorized_ids en memoria: {sorted(_authorized_ids)!r} | "
          f"IDs en env+archivo (disk): {sorted(_ids_disk)!r} | "
          f"¿autorizado?: {chat_id in _authorized_ids} | "
          f"TELEGRAM_CHAT_ID env: {TELEGRAM_CHAT_ID!r}")
    _send(chat_id, f"🔍 Buscando <b>{home_raw} vs {away_raw}</b>…")

    # ── ETAPA 1: Buscar el partido en la API de odds ──────────────────────────
    game_found  = None
    sport_found = "baseball_mlb"
    try:
        for sport in ["baseball_mlb"]:   # solo MLB ahora que se eliminó WC/soccer
            try:
                games = _get_odds_fn(sport)
                if not games:
                    print(f"  [analizar] {sport}: sin juegos (API vacía o error)")
                    continue
                for g in games:
                    gh = g.get("home_team", "")
                    ga = g.get("away_team", "")
                    if ((_team_words_match(home_q, gh) and _team_words_match(away_q, ga)) or
                            (_team_words_match(away_q, gh) and _team_words_match(home_q, ga))):
                        game_found  = g
                        sport_found = sport
                        break
                if game_found:
                    break
            except Exception as _gse:
                print(f"  [analizar] excepción buscando en {sport}: {_gse}")
                continue
    except BaseException as _srch_err:
        print(f"  💥 [analizar] CRASH en búsqueda de partido [{type(_srch_err).__name__}]: {_srch_err} | RSS={_mem_rss_mb()}MB")
        _traceback.print_exc()
        _send(chat_id, f"⚠️ Error buscando el partido [{type(_srch_err).__name__}]: {_srch_err}")
        return

    if not game_found:
        _send(chat_id,
              f"❌ No encontré el partido <b>{partes[0].strip()} vs {partes[1].strip()}</b>.\n"
              "Verifica los nombres y que el partido esté en las próximas 48 horas.")
        return

    print(f"  [analizar] ETAPA 1 OK — partido encontrado: {game_found.get('home_team')} vs {game_found.get('away_team')} | RSS={_mem_rss_mb()}MB")

    # ── ETAPA 2: Análisis completo (panel de 3 expertos + todos los módulos) ──
    result = None
    try:
        print(f"  [analizar] ETAPA 2 — llamando analyze_game_full (force_panel=True, _no_elite_panel=False) | RSS={_mem_rss_mb()}MB")
        result = _analyze_fn(game_found, sport_found, {}, force_panel=True,
                             _no_elite_panel=False)
        print(f"  [analizar] ETAPA 2 OK — analyze_game_full completado | RSS={_mem_rss_mb()}MB result={'ok' if result else 'None'}")
    except BaseException as _anl_err:
        _et = type(_anl_err).__name__
        print(f"  💥 [analizar] CRASH en analyze_game_full [{_et}]: {_anl_err} | RSS={_mem_rss_mb()}MB")
        _traceback.print_exc()
        _send(chat_id,
              f"⚠️ Error durante el análisis [{_et}]: {_anl_err}\n"
              f"Ver Railway logs para el traceback completo.")
        if isinstance(_anl_err, (SystemExit, KeyboardInterrupt)):
            raise
        return

    if not result:
        print(f"  [analizar] ETAPA 2 — resultado None (sin candidatos o datos insuficientes)")
        _send(chat_id, (
            "⚠️ No se pudo obtener análisis para este partido.\n"
            "Posibles causas: partido no encontrado en API, datos insuficientes, "
            "o error de conexión. Intenta de nuevo en unos minutos."
        ))
        return

    # ── ETAPA 3: Formatear y enviar el resultado ──────────────────────────────
    if _build_text_fn:
        try:
            print(f"  [analizar] ETAPA 3 — build_analizar_text | RSS={_mem_rss_mb()}MB")
            build_parts = _build_text_fn(result)
            # Normalizar: build_text_fn puede devolver lista de strings o string único
            if isinstance(build_parts, str):
                build_parts = [build_parts]
            build_parts = [p for p in build_parts if p and p.strip()]

            # Expandir: si alguna parte todavía supera el límite, dividir con _split_message
            _all_chunks: list = []
            for _bp in build_parts:
                _all_chunks.extend(_split_message(_bp))

            total_chars = sum(len(c) for c in _all_chunks)
            print(f"  [analizar] Mensaje armado, longitud: {total_chars} caracteres "
                  f"({len(_all_chunks)} parte(s): {[len(c) for c in _all_chunks]})")

            for _pi, chunk in enumerate(_all_chunks):
                pnum = _pi + 1
                ptot = len(_all_chunks)
                print(f"  [analizar] Enviando parte {pnum} a Telegram… ({len(chunk)} chars, "
                      f"chat_id={chat_id!r})")
                _resp = _api("sendMessage", {
                    "chat_id":    chat_id,
                    "text":       chunk,
                    "parse_mode": "HTML",
                })
                _ok      = _resp.get("ok", False)
                _http    = _resp.get("_http_code", 200 if _ok else "?")
                _msg_id  = (_resp.get("result") or {}).get("message_id", "?")
                _desc    = _resp.get("description", _resp.get("_body", ""))
                if _ok:
                    print(f"  [analizar] Respuesta de Telegram parte {pnum}: HTTP {_http} ✅ "
                          f"(message_id={_msg_id})")
                else:
                    print(f"  [analizar] Respuesta de Telegram parte {pnum}: HTTP {_http} ❌ — "
                          f"description={_desc!r} | respuesta completa: {_resp}")

            print(f"  [analizar] COMPLETADO {home_raw} vs {away_raw} | RSS_final={_mem_rss_mb()}MB")
            return
        except BaseException as _fmt_err:
            _et = type(_fmt_err).__name__
            print(f"  💥 [analizar] CRASH en formateo/envío [{_et}]: {_fmt_err} | RSS={_mem_rss_mb()}MB")
            _traceback.print_exc()
            _send(chat_id, f"⚠️ Error al formatear análisis [{_et}]: {_fmt_err}")
            if isinstance(_fmt_err, (SystemExit, KeyboardInterrupt)):
                raise
            return

    # ── Fallback: formato básico si build_text_fn no está disponible ──────────
    cands = result.get("candidates", [])
    best  = cands[0] if cands else {}
    pick  = result.get("best_label", best.get("label", "?"))
    ev    = result.get("best_ev",   best.get("ev_pct", 0))
    prob  = round(best.get("true_prob", 0) * 100)
    stake = best.get("stake", 0)
    match = result.get("match", "?")
    home  = match.split(" vs ")[0]
    away  = match.split(" vs ")[-1]

    cands_txt = ""
    for c in cands[:3]:
        cands_txt += f"\n  • {c.get('label','?')} @ {c.get('book','?')} | EV+{c.get('ev_pct',0):.1f}%"

    ci_data       = result.get("claude_intel") or {}
    final_apostar = ci_data.get("apostar")
    rec_icon      = "✅ APOSTAR" if final_apostar is True else ("❌ PASAR" if final_apostar is False else "")
    rec_txt       = f"\n\n<b>Recomendación:</b> {rec_icon}" if rec_icon else ""

    _send(chat_id, (
        f"🎯 <b>{home} vs {away}</b>\n\n"
        f"Pick: <b>{pick}</b>\n"
        f"EV: <b>+{ev:.1f}%</b>\n"
        f"Prob modelo: {prob}%\n"
        f"Stake sugerido: ${stake:.0f}\n"
        f"{cands_txt}"
        f"{rec_txt}"
    ))


def handle_photo(chat_id: str, msg: dict):
    """
    Photo pipeline:
      1. Claude Vision  → extract team names as JSON
      2. get_odds()     → find each game in the API
      3. analyze_game_full() + build_analizar_text() → same output as /analizar
    """
    print(f"  📸 handle_photo: iniciando para chat_id={chat_id}")

    if not ANTHROPIC_API_KEY:
        print("  📸 handle_photo: ANTHROPIC_API_KEY no configurada")
        _send(chat_id, "⚠️ API de Claude no configurada")
        return

    if not _get_odds_fn or not _analyze_fn:
        _send(chat_id, "⚠️ Módulo de análisis no disponible (bot en modo básico).")
        return

    # ── Step 1: resolve file_id ───────────────────────────────────────────────
    if msg.get("photo"):
        file_id = msg["photo"][-1]["file_id"]
    elif msg.get("document") and (msg["document"].get("mime_type") or "").startswith("image/"):
        file_id = msg["document"]["file_id"]
    else:
        _send(chat_id, "⚠️ No se recibió ninguna imagen.")
        return

    print(f"  📸 handle_photo: file_id={file_id[:20]}…")
    _send(chat_id, "🔍 Identificando partidos... dame un momento")

    # ── Step 2: download image from Telegram ─────────────────────────────────
    file_info = _api("getFile", {"file_id": file_id})
    if not file_info.get("ok"):
        print(f"  📸 handle_photo: getFile falló — {file_info}")
        _send(chat_id, "⚠️ No pude descargar la imagen de Telegram.")
        return
    file_path = file_info["result"]["file_path"]

    try:
        dl_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
        with urllib.request.urlopen(dl_url, timeout=20) as r:
            img_bytes = r.read()
        print(f"  📸 handle_photo: descargada {len(img_bytes):,} bytes")
    except Exception as e:
        print(f"  📸 handle_photo: error descargando — {e}")
        _send(chat_id, f"⚠️ Error descargando imagen: {e}")
        return

    import base64 as _b64
    img_b64   = _b64.b64encode(img_bytes).decode("utf-8")
    ext       = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else "jpeg"
    media_type = "image/png" if ext == "png" else "image/webp" if ext == "webp" else "image/jpeg"

    # ── Step 3: Claude Vision — extract full game data as JSON ──────────────
    EXTRACT_PROMPT = (
        "Analiza esta imagen de béisbol y extrae la información en formato JSON exacto:\n"
        "{\n"
        '  "partidos": [\n'
        "    {\n"
        '      "equipo_local": "nombre",\n'
        '      "equipo_visitante": "nombre",\n'
        '      "pitcher_local": "nombre o null",\n'
        '      "pitcher_visitante": "nombre o null",\n'
        '      "era_local": "numero o null",\n'
        '      "era_visitante": "numero o null",\n'
        '      "total_line": "numero o null",\n'
        '      "ml_local": "numero o null",\n'
        '      "ml_visitante": "numero o null"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Solo devuelve el JSON, sin texto adicional."
    )

    print("  📸 handle_photo: llamando Claude Vision para extraer datos…")
    try:
        import anthropic as _anth
        client = _anth.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp_cv = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_b64,
                        },
                    },
                    {"type": "text", "text": EXTRACT_PROMPT},
                ],
            }],
        )
        raw_json = resp_cv.content[0].text.strip()
        print(f"  📸 handle_photo: Claude raw → {raw_json[:200]}")
    except Exception as e:
        print(f"  📸 handle_photo: error Claude Vision — {e}")
        _send(chat_id, f"⚠️ Error al consultar Claude: {e}")
        return

    # ── Step 4: parse game data ───────────────────────────────────────────────
    import json as _json, re as _re
    try:
        clean    = _re.sub(r"```[a-z]*", "", raw_json).strip().strip("`").strip()
        parsed   = _json.loads(clean)
        # Accept both {"partidos": [...]} and a bare list
        if isinstance(parsed, dict):
            matchups = parsed.get("partidos", [])
        elif isinstance(parsed, list):
            matchups = parsed
        else:
            matchups = []
        if not matchups:
            raise ValueError("no partidos found")
    except Exception as pe:
        print(f"  📸 handle_photo: JSON parse error — {pe} | raw={raw_json[:200]}")
        _send(chat_id,
              "No pude identificar información de apuestas en la imagen. "
              "Manda una captura más clara del schedule o las líneas.")
        return

    print(f"  📸 handle_photo: {len(matchups)} partido(s) detectados")

    # ── Step 5: fetch MLB odds once, then match each pair ────────────────────
    try:
        games = _get_odds_fn("baseball_mlb") or []
    except Exception as e:
        games = []
        print(f"  📸 handle_photo: get_odds error — {e}")

    def _words_match(query: str, team: str) -> bool:
        t = team.lower()
        return all(w in t for w in query.lower().split())

    found_any = False
    for gdata in matchups:
        # Support both dict (new format) and 2-item list (legacy)
        if isinstance(gdata, dict):
            home_q = (gdata.get("equipo_local") or "").strip().lower()
            away_q = (gdata.get("equipo_visitante") or "").strip().lower()
            img_pitcher_home = gdata.get("pitcher_local")
            img_pitcher_away = gdata.get("pitcher_visitante")
            img_era_home     = gdata.get("era_local")
            img_era_away     = gdata.get("era_visitante")
            label_home = gdata.get("equipo_local", "?")
            label_away = gdata.get("equipo_visitante", "?")
        else:
            home_q = str(gdata[0]).strip().lower()
            away_q = str(gdata[1]).strip().lower()
            img_pitcher_home = img_pitcher_away = None
            img_era_home = img_era_away = None
            label_home, label_away = gdata[0], gdata[1]

        if not home_q or not away_q:
            continue

        game_found = None
        for g in games:
            gh = g.get("home_team", "")
            ga = g.get("away_team", "")
            if ((_words_match(home_q, gh) and _words_match(away_q, ga)) or
                    (_words_match(away_q, gh) and _words_match(home_q, ga))):
                game_found = g
                break

        if not game_found:
            _send(chat_id,
                  f"⚠️ <b>{label_home} vs {label_away}</b> — no encontrado en la API "
                  f"(puede que no esté en las próximas 48h o el nombre difiera).")
            continue

        # ── Run full analysis pipeline ────────────────────────────────────
        found_any = True

        # Build img_context: override TBD pitchers / ERAs with data from image
        img_ctx = {}
        if img_pitcher_home and str(img_pitcher_home).lower() not in ("null", "none", "tbd", ""):
            img_ctx["pname_home"] = img_pitcher_home
        if img_pitcher_away and str(img_pitcher_away).lower() not in ("null", "none", "tbd", ""):
            img_ctx["pname_away"] = img_pitcher_away
        try:
            if img_era_home is not None:
                img_ctx["era_home"] = float(img_era_home)
        except (TypeError, ValueError):
            pass
        try:
            if img_era_away is not None:
                img_ctx["era_away"] = float(img_era_away)
        except (TypeError, ValueError):
            pass
        if img_ctx:
            print(f"  📸 handle_photo: inyectando contexto imagen → {img_ctx}")

        try:
            result = _analyze_fn(game_found, "baseball_mlb", {}, force_panel=True,
                                 extra_ctx=img_ctx if img_ctx else None)
        except TypeError:
            # analyze_game_full may not accept extra_ctx — fall back gracefully
            result = _analyze_fn(game_found, "baseball_mlb", {}, force_panel=True)
            # Patch result context manually after the fact
            if result and img_ctx:
                ctx = result.setdefault("context", {})
                for k, v in img_ctx.items():
                    if ctx.get(k) in (None, "TBD", "", 0, 0.0):
                        ctx[k] = v
        except Exception as ae:
            _send(chat_id, f"⚠️ Error analizando {label_home} vs {label_away}: {ae}")
            continue

        if not result:
            _send(chat_id, f"⚠️ Sin datos suficientes para {label_home} vs {label_away}.")
            continue

        if _build_text_fn:
            try:
                parts = _build_text_fn(result)
                for part in parts:
                    if part and part.strip():
                        _send_long(chat_id, part)
            except Exception as bte:
                _send(chat_id, f"⚠️ Error formateando resultado: {bte}")
        else:
            best  = (result.get("candidates") or [{}])[0]
            _send(chat_id,
                  f"🎯 <b>{result.get('match','?')}</b>\n"
                  f"Pick: <b>{best.get('label','?')}</b> | "
                  f"EV +{best.get('ev_pct',0):.1f}% | "
                  f"Stake ${best.get('stake',0):.0f}")

    if not found_any and matchups:
        _send(chat_id,
              "No encontré ninguno de los partidos de la imagen en la API de odds. "
              "Verifica que los juegos estén dentro de las próximas 48 horas.")


def _broadcast_to_all(payload):
    """
    Broadcast to all authorized Telegram chats.
    payload: dict  → analysis result, formatted via _build_text_fn
             str   → sent as-is (plain text or HTML)
    """
    if isinstance(payload, dict):
        if _build_text_fn:
            try:
                parts = _build_text_fn(payload)
            except Exception as _fe:
                best  = (payload.get("candidates") or [{}])[0]
                match = payload.get("match", "?")
                ev    = payload.get("best_ev", best.get("ev_pct", 0))
                label = payload.get("best_label", best.get("label", "?"))
                parts = [f"🔍 <b>{match}</b>\nPick: <b>{label}</b> | EV: +{ev:.1f}%"]
        else:
            best  = (payload.get("candidates") or [{}])[0]
            match = payload.get("match", "?")
            ev    = payload.get("best_ev", best.get("ev_pct", 0))
            label = payload.get("best_label", best.get("label", "?"))
            parts = [f"🔍 <b>{match}</b>\nPick: <b>{label}</b> | EV: +{ev:.1f}%"]
    else:
        parts = [str(payload)]

    for cid in list(_authorized_ids):
        for part in parts:
            if part and part.strip():
                try:
                    _send_long(cid, part)
                except Exception as _se:
                    print(f"  ⚠️  Telegram broadcast [{cid}]: {_se}")


def _cmd_hoy(chat_id: str):
    if not _get_hoy_fn:
        _send(chat_id, "⚠️ Módulo /hoy no disponible — bot iniciando.")
        return
    _send(chat_id, "⏳ Obteniendo juegos MLB de hoy... (~15 segundos)")
    try:
        parts = _get_hoy_fn()
        for part in parts:
            if part and part.strip():
                _send_long(chat_id, part)
    except Exception as e:
        _send(chat_id, f"⚠️ Error obteniendo juegos: {e}")


def _cmd_pitchers(chat_id: str):
    """
    /pitchers — Abridores confirmados de hoy directo del MLB Stats API.
    Muestra equipo, hora, pitcher con récord/ERA/K de la temporada.
    """
    _send(chat_id, "⏳ Consultando MLB Stats API...")
    try:
        hoy = datetime.datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")
        MLB_API = "https://statsapi.mlb.com/api/v1"

        # ── Fetch schedule ────────────────────────────────────────────────
        params = urllib.parse.urlencode({
            "sportId": 1, "date": hoy,
            "hydrate": "probablePitcher,linescore,team",
        })
        with urllib.request.urlopen(f"{MLB_API}/schedule?{params}", timeout=10) as _r:
            sched = json.loads(_r.read())

        if not sched.get("dates"):
            _send(chat_id, "No hay juegos programados hoy.")
            return

        lineas = [f"⚾ ABRIDORES CONFIRMADOS — {hoy}\n"]

        def _pitcher_info(lado):
            p = lado.get("probablePitcher")
            if not p:
                return "TBD ❓"
            pid    = p["id"]
            nombre = p["fullName"]
            try:
                sp = urllib.parse.urlencode(
                    {"hydrate": "stats(group=[pitching],type=[season])"}
                )
                with urllib.request.urlopen(
                    f"{MLB_API}/people/{pid}?{sp}", timeout=10
                ) as _sr:
                    pr = json.loads(_sr.read())
                splits = pr["people"][0]["stats"][0]["splits"]
                s   = splits[0]["stat"]
                era = s.get("era", "?")
                w   = s.get("wins", "?")
                l   = s.get("losses", "?")
                so  = s.get("strikeOuts", "?")
                return f"{nombre} ({w}-{l}, {era} ERA, {so} K)"
            except Exception:
                return f"{nombre} (stats no disp.)"

        for juego in sched["dates"][0]["games"]:
            away   = juego["teams"]["away"]
            home   = juego["teams"]["home"]
            estado = juego["status"]["abstractGameState"]
            hora_utc = datetime.datetime.fromisoformat(
                juego["gameDate"].replace("Z", "+00:00")
            )
            hora_ct = hora_utc.astimezone(
                ZoneInfo("America/Chicago")
            ).strftime("%I:%M%p CT")
            marca = (
                "🔴 LIVE"    if estado == "Live"  else
                "✅ Final"   if estado == "Final" else
                hora_ct
            )
            lineas.append(
                f"{away['team']['name']} @ {home['team']['name']} — {marca}\n"
                f"  🅰️ {_pitcher_info(away)}\n"
                f"  🏠 {_pitcher_info(home)}\n"
            )

        texto = "\n".join(lineas)
        _send_long(chat_id, texto)

    except Exception as e:
        _send(chat_id, f"⚠️ Error consultando MLB Stats API: {e}")


def _cmd_patrones(chat_id: str):
    """
    /patrones — Detecta patrones situacionales del slate de MLB de hoy:
    getaway day masivo, bullpen games (openers), bullpens quemados.
    """
    if not _get_patrones_fn:
        _send(chat_id, "⚠️ Módulo /patrones no disponible (bot en modo básico).")
        return
    _send(chat_id, "⏳ Escaneando patrones del slate de hoy...")
    try:
        alertas = _get_patrones_fn()
        if not alertas:
            _send(chat_id, "✅ Sin patrones situacionales fuertes hoy.")
            return
        texto = "\n\n".join(alertas)
        _send_long(chat_id, texto)
    except Exception as e:
        _send(chat_id, f"⚠️ Error escaneando patrones: {e}")


def _cmd_kprops(chat_id: str, args: str):
    """
    /kprops [pitcher] | [rival] | [línea] | [Over/Under] | [cuota] | [cuota contraria, opcional]
    Ejemplo: /kprops Cristopher Sanchez | Yankees | 6.5 | Over | -140
    """
    try:
        from k_props import analyze_k_prop_by_name, format_notification, log_k_prop
    except ImportError as e:
        _send(chat_id, f"⚠️ Módulo k_props no disponible: {e}")
        return

    partes = [p.strip() for p in args.split("|")]
    if len(partes) < 5:
        _send(chat_id,
              "Formato: /kprops [pitcher] | [rival] | [línea] | [Over/Under] | [cuota] | [cuota contraria, opcional]\n"
              "Ejemplo: /kprops Cristopher Sanchez | Yankees | 6.5 | Over | -140")
        return

    try:
        pitcher_name, rival_name, line_str, side, odds_str = partes[:5]
        odds_other_str = partes[5] if len(partes) > 5 else None

        resultado = analyze_k_prop_by_name(
            pitcher_name=pitcher_name,
            rival_team_name=rival_name,
            line=float(line_str),
            side=side.capitalize(),
            odds_side=float(odds_str),
            odds_other=float(odds_other_str) if odds_other_str else None,
            bookmaker=None,
        )
        log_k_prop(resultado)
        _send(chat_id, format_notification(resultado))
    except Exception as e:
        _send(chat_id, f"⚠️ Error: {e}\nVerifica el nombre del pitcher y del equipo rival.")


def _cmd_contexto(chat_id: str):
    """
    /contexto — Contexto de juego para todos los partidos MLB de hoy:
    park factor, clima, abridores L/R, OPS splits, regulares descansando
    y bullpen quemado. Gratis: MLB Stats API + Open-Meteo.
    """
    if not _HAS_CONTEXTO:
        _send(chat_id, "⚠️ Módulo de contexto no disponible.")
        return
    _send(chat_id, "⏳ Obteniendo contexto de juegos de hoy...")
    try:
        hoy = datetime.datetime.now(TZ_CT).strftime("%Y-%m-%d")
        params = urllib.parse.urlencode({"sportId": 1, "date": hoy})
        with urllib.request.urlopen(
            f"https://statsapi.mlb.com/api/v1/schedule?{params}", timeout=10
        ) as _r:
            sched = json.loads(_r.read())

        fechas = sched.get("dates", [])
        if not fechas:
            _send(chat_id, "No hay juegos programados hoy.")
            return

        juegos = [g for d in fechas for g in d.get("games", [])]
        if not juegos:
            _send(chat_id, "No hay juegos programados hoy.")
            return

        textos = []
        for g in juegos:
            gp = g.get("gamePk")
            if not gp:
                continue
            try:
                txt = _resumen_contexto_fn(gp)
                textos.append(txt)
            except Exception as eg:
                away_t = g.get("teams", {}).get("away", {}).get("team", {}).get("name", "?")
                home_t = g.get("teams", {}).get("home", {}).get("team", {}).get("name", "?")
                textos.append(f"⚠️ {away_t} @ {home_t}: error al obtener contexto ({eg})")

        if not textos:
            _send(chat_id, "No se pudo obtener contexto de ningún juego.")
            return

        # Enviar en bloques de 8 para no pasarse del límite de Telegram
        bloque = []
        for txt in textos:
            bloque.append(txt)
            if len(bloque) >= 8:
                _send(chat_id, "\n\n─────────────\n\n".join(bloque))
                bloque = []
        if bloque:
            _send(chat_id, "\n\n─────────────\n\n".join(bloque))

    except Exception as e:
        _send(chat_id, f"⚠️ Error obteniendo contexto: {e}")


def _cmd_bulk_analysis(chat_id: str, sport_key: str, emoji: str, label: str):
    """Shared logic for /mlb — analyzes all games for a sport key."""
    if not _get_odds_fn or not _analyze_fn:
        _send(chat_id, "⚠️ Módulo de análisis no disponible (bot en modo básico).")
        return

    _send(chat_id, f"{emoji} Analizando partidos {label} de hoy... dame un momento")

    try:
        games = _get_odds_fn(sport_key) or []
    except Exception as e:
        _send(chat_id, f"⚠️ Error consultando la API de odds: {e}.\nRevisa quota o API key.")
        return

    if not games:
        _et_hoy = datetime.datetime.now(ZoneInfo("America/New_York")).strftime("%d/%m")
        no_msg = {
            "baseball_mlb":         f"⚾ No hay partidos MLB para hoy ({_et_hoy} ET).",
            "soccer_fifa_world_cup": f"🏆 Sin partidos del Mundial para hoy ({_et_hoy} ET).",
        }
        _send(chat_id, no_msg.get(sport_key, f"📅 No hay partidos para hoy ({_et_hoy} ET)."))
        return

    # Sort by commence time, cap at 15
    games_sorted = sorted(games, key=lambda g: g.get("commence_time", ""))[:15]

    found_any = False
    for i, game in enumerate(games_sorted):
        home = game.get("home_team", "?")
        away = game.get("away_team", "?")
        try:
            result = _analyze_fn(game, sport_key, {}, force_panel=True)
        except Exception as ae:
            _send(chat_id, f"⚠️ Error analizando {home} vs {away}: {ae}")
            continue

        if not result:
            print(f"  [bulk] Sin resultado para {home} vs {away}")
            continue

        found_any = True
        if _build_text_fn:
            try:
                parts = _build_text_fn(result)
                for part in parts:
                    if part and part.strip():
                        _send_long(chat_id, part)
            except Exception as bte:
                _send(chat_id, f"⚠️ Error formateando {home} vs {away}: {bte}")
        else:
            best = (result.get("candidates") or [{}])[0]
            _send(chat_id,
                  f"🎯 <b>{result.get('match','?')}</b>\n"
                  f"Pick: <b>{best.get('label','?')}</b> | "
                  f"EV +{best.get('ev_pct',0):.1f}% | "
                  f"Stake ${best.get('stake',0):.0f}")

        if i < len(games_sorted) - 1:
            import time as _t; _t.sleep(1)

    if not found_any:
        _send(chat_id,
              f"Sin picks recomendados en los partidos de {label} de hoy "
              f"(EV insuficiente o datos incompletos).")


def _cmd_mlb(chat_id: str):
    _cmd_bulk_analysis(chat_id, "baseball_mlb", "⚾", "MLB")


def _cmd_parlay(chat_id: str):
    """Build the best 2-3 leg parlay from today's strongest MLB (+ optional Mundial) picks."""
    if not _get_odds_fn or not _analyze_fn:
        _send(chat_id, "⚠️ Módulo de análisis no disponible.")
        return

    _send(chat_id, "🎰 Armando el mejor parlay del día... dame un momento")

    # ── helpers ──────────────────────────────────────────────────────────────
    GOOD_BOOKS = {"bovada", "betonline", "betonline.ag"}

    def _to_decimal(american: float) -> float:
        if american >= 0:
            return round(american / 100 + 1, 4)
        return round(100 / abs(american) + 1, 4)

    def _collect_picks(sport_key: str) -> list:
        """Return filtered pick dicts for a sport."""
        try:
            games = _get_odds_fn(sport_key) or []
        except Exception:
            return []
        picks = []
        for game in sorted(games, key=lambda g: g.get("commence_time", ""))[:20]:
            try:
                result = _analyze_fn(game, sport_key, {}, force_panel=False)
            except Exception:
                continue
            if not result:
                continue
            intel    = result.get("claude_intel") or {}
            panel_ok = intel.get("apostar") is True
            razon_raw= intel.get("razonamiento", "")
            razon    = (razon_raw[:77] + "…") if len(razon_raw) > 80 else razon_raw
            match    = result.get("match", "")
            for cand in result.get("candidates") or []:
                ev   = cand.get("ev_pct", 0)
                prob = cand.get("true_prob", 0)
                book = (cand.get("book") or "").lower()
                if (panel_ok and ev >= 5.0 and prob >= 0.55
                        and book in GOOD_BOOKS):
                    picks.append({
                        "match":   match,
                        "sport":   sport_key,
                        "label":   cand.get("label", "?"),
                        "odds":    cand.get("odds", 0),
                        "dec":     _to_decimal(cand.get("odds", 0)),
                        "book":    cand.get("book", ""),
                        "prob":    prob,
                        "ev":      ev,
                        "razon":   razon,
                    })
        return picks

    # ── gather picks (MLB only) ───────────────────────────────────────────────
    all_picks = _collect_picks("baseball_mlb")

    # Sort by EV descending
    all_picks.sort(key=lambda x: -x["ev"])

    # ── select legs (greedy, anti-correlation) ────────────────────────────────
    def _team_from_label(label: str) -> str:
        """Extract team name from label like 'Moneyline Cubs' or 'RL Cubs -1.5'."""
        for prefix in ("moneyline ", "ml ", "rl ", "runline ", "over ", "under ",
                       "total over ", "total under "):
            if label.lower().startswith(prefix):
                rest = label[len(prefix):]
                return rest.split()[0].lower()
        return label.lower()

    def _market_type(label: str) -> str:
        low = label.lower()
        if "rl" in low or "runline" in low:
            return "rl"
        if "moneyline" in low or low.startswith("ml "):
            return "ml"
        return "other"

    legs = []
    used_matches = set()
    used_team_market = set()  # (team, sport) — avoid ML+RL same team

    for pk in all_picks:
        if len(legs) >= 3:
            break
        if pk["match"] in used_matches:
            continue
        team = _team_from_label(pk["label"])
        mtype = _market_type(pk["label"])
        key = (team, pk["sport"])
        if mtype in ("ml", "rl") and key in used_team_market:
            continue
        legs.append(pk)
        used_matches.add(pk["match"])
        used_team_market.add(key)

    # Second pass to fill remaining slots
    if len(legs) < 3:
        for pk in all_picks:
            if len(legs) >= 3:
                break
            if pk["match"] in used_matches:
                continue
            team = _team_from_label(pk["label"])
            mtype = _market_type(pk["label"])
            key = (team, pk["sport"])
            if mtype in ("ml", "rl") and key in used_team_market:
                continue
            legs.append(pk)
            used_matches.add(pk["match"])
            used_team_market.add(key)

    if len(legs) < 2:
        _send(chat_id,
              "No hay suficientes picks fuertes hoy para armar parlay.\n"
              "Usa /picks para ver los picks individuales disponibles.")
        return

    # ── calculate parlay stats ────────────────────────────────────────────────
    bankroll = _load_json(TRACKER_FILE, {"bankroll": 1000.0}).get("bankroll", 1000.0)
    stake    = max(10.0, min(20.0, bankroll * 0.02))

    odds_comb = 1.0
    prob_comb = 1.0
    for leg in legs:
        odds_comb *= leg["dec"]
        prob_comb *= leg["prob"]

    ganancia   = stake * odds_comb
    ev_parlay  = (prob_comb * odds_comb - 1) * 100

    # ── build message ─────────────────────────────────────────────────────────
    DIV = "━" * 22
    RANK = ["1️⃣", "2️⃣", "3️⃣"]
    lines = [f"🎰 <b>MEJOR PARLAY DEL DÍA</b>\n{DIV}\n"]

    for i, leg in enumerate(legs):
        razon_block = f"\n   <i>'{leg['razon']}'</i>" if leg["razon"] else ""
        lines.append(
            f"{RANK[i]} <b>{leg['label']}</b>\n"
            f"   {round(leg['prob']*100,1)}% | EV +{leg['ev']:.1f}% | "
            f"{leg['odds']:.2f} @ {leg['book']}"
            f"{razon_block}\n"
        )

    lines.append(
        f"{DIV}\n"
        f"💰 Cuota combinada: <b>{odds_comb:.2f}x</b>\n"
        f"🎯 Apuesta: <b>${stake:.0f}</b> en {legs[0]['book']}\n"
        f"📈 Si gana: <b>${ganancia:.0f}</b>\n"
        f"📊 EV parlay: {ev_parlay:+.1f}%\n"
        f"{DIV}\n"
        f"⚠️ Apuesta pequeña — es parlay\n"
        f"Panel aprobó cada pierna individualmente"
    )

    _send(chat_id, "\n".join(lines))


# ── Bet tracking helpers ─────────────────────────────────────────────────────

def _ct_now_str() -> str:
    """Current time as 'YYYY-MM-DD HH:MM CT' using proper America/Chicago tz."""
    return datetime.datetime.now(TZ_CT).strftime("%Y-%m-%d %H:%M CT")

def _ct_today() -> str:
    return datetime.datetime.now(TZ_CT).strftime("%Y-%m-%d")

def _load_bets() -> dict:
    return _load_json(BETS_TODAY_FILE, {"date": _ct_today(), "bets": []})

def _save_bets(data: dict):
    try:
        with open(BETS_TODAY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  ⚠️  _save_bets: {e}")

def _midnight_reset():
    """Archive bets_today.json if date changed. Called at polling loop start."""
    try:
        data = _load_bets()
        saved_date = data.get("date", "")
        today = _ct_today()
        if saved_date and saved_date != today and data.get("bets"):
            archive = f"bets_{saved_date}.json"
            import shutil as _sh
            _sh.copy2(BETS_TODAY_FILE, archive)
            _save_bets({"date": today, "bets": []})
            print(f"  📦 Bets archivados en {archive} — nuevo día {today}")
    except Exception as e:
        print(f"  ⚠️  _midnight_reset: {e}")


def _cmd_aposte(chat_id: str, args: str):
    """Register a manual bet: /aposte Dodgers ML $25"""
    import re as _re
    if not args:
        _send(chat_id,
              "⚠️ Uso: <code>/aposte Pick $Monto</code>\n"
              "Ejemplos:\n"
              "  /aposte Dodgers ML $25\n"
              "  /aposte UNDER 8.5 Seattle $20\n"
              "  /aposte parlay Dodgers ML + UNDER 8.5 $15")
        return

    # Extract stake: find last $number in text
    m = _re.search(r'\$\s*(\d+(?:\.\d+)?)', args)
    if not m:
        _send(chat_id, "⚠️ No encontré el monto. Usa formato: <code>/aposte Pick $Monto</code>")
        return

    stake = float(m.group(1))
    # Pick is everything before the $amount match
    pick = args[:m.start()].strip().strip(",").strip()
    if not pick:
        _send(chat_id, "⚠️ No encontré el pick. Usa: <code>/aposte Dodgers ML $25</code>")
        return

    data = _load_bets()
    # Day rollover check
    if data.get("date") != _ct_today():
        _midnight_reset()
        data = _load_bets()

    bet_id = int(datetime.datetime.utcnow().timestamp())
    bet = {
        "id":        bet_id,
        "pick":      pick,
        "stake":     stake,
        "status":    "pending",
        "timestamp": _ct_now_str(),
        "odds":      None,
        "resultado": None,
        "pnl":       None,
    }
    data["bets"].append(bet)
    _save_bets(data)

    _send(chat_id,
          f"✅ <b>Apuesta registrada</b>\n"
          f"Pick: <b>{pick}</b>\n"
          f"Stake: <b>${stake:.2f}</b>\n"
          f"Estado: ⏳ Pendiente\n\n"
          f"Te aviso cuando salga el resultado.")


def _cmd_historial(chat_id: str):
    """Show today's bets from bets_today.json."""
    data = _load_bets()
    bets = data.get("bets", [])
    if not bets:
        _send(chat_id, "📭 Sin apuestas registradas hoy. Usa /aposte para registrar una.")
        return

    DIV = "━" * 22
    lines = [f"📊 <b>TUS APUESTAS DE HOY</b>\n{DIV}"]

    total_pnl = 0.0
    pending_stake = 0.0
    for b in bets:
        status  = b.get("status", "pending")
        pick    = b.get("pick", "?")
        stake   = b.get("stake", 0)
        pnl     = b.get("pnl")

        if status == "win":
            pnl_str = f" → <b>GANÓ +${pnl:.2f}</b>" if pnl is not None else " → <b>GANÓ ✅</b>"
            icon = "✅"
            total_pnl += pnl if pnl is not None else 0
        elif status == "loss":
            pnl_str = f" → <b>PERDIÓ -${stake:.2f}</b>"
            icon = "❌"
            total_pnl -= stake
        else:
            pnl_str = " → <b>Pendiente</b>"
            icon = "⏳"
            pending_stake += stake

        lines.append(f"{icon} {pick} <b>${stake:.0f}</b>{pnl_str}")

    lines.append(DIV)
    pnl_sign = "+" if total_pnl >= 0 else ""
    lines.append(f"💰 Balance hoy: <b>{pnl_sign}${total_pnl:.2f}</b>")
    if pending_stake > 0:
        lines.append(f"⏳ Pendiente: <b>${pending_stake:.0f}</b> en juego")

    _send(chat_id, "\n".join(lines))


def _cmd_resultado(chat_id: str, args: str):
    """Mark a bet result: /resultado Dodgers W  or  /resultado UNDER L"""
    if not args:
        _send(chat_id,
              "⚠️ Uso: <code>/resultado Pick W</code> o <code>/resultado Pick L</code>\n"
              "Ejemplos:\n"
              "  /resultado Dodgers W\n"
              "  /resultado UNDER L")
        return

    parts = args.strip().split()
    if len(parts) < 2 or parts[-1].upper() not in ("W", "L"):
        _send(chat_id,
              "⚠️ El último carácter debe ser W (ganó) o L (perdió).\n"
              "Ejemplo: <code>/resultado Dodgers ML W</code>")
        return

    outcome = parts[-1].upper()
    query   = " ".join(parts[:-1]).lower().strip()

    data = _load_bets()
    bets = data.get("bets", [])

    # Find the most recent pending bet that contains query keywords
    match_bet = None
    for b in reversed(bets):
        if b.get("status") != "pending":
            continue
        if any(w in b.get("pick", "").lower() for w in query.split()):
            match_bet = b
            break

    if not match_bet:
        _send(chat_id,
              f"❌ No encontré apuesta pendiente que coincida con <b>{query}</b>.\n"
              "Usa /historial para ver tus apuestas.")
        return

    pick  = match_bet["pick"]
    stake = match_bet.get("stake", 0)
    odds  = match_bet.get("odds")

    if outcome == "W":
        match_bet["status"]    = "win"
        match_bet["resultado"] = "W"
        # Calculate P&L if decimal odds available
        if odds and abs(odds) > 0:
            if odds > 0:
                pnl = stake * odds / 100
            else:
                pnl = stake * 100 / abs(odds)
            match_bet["pnl"] = round(pnl, 2)
        else:
            match_bet["pnl"] = None
        pnl_txt = (f"Ganancia: <b>+${match_bet['pnl']:.2f}</b>"
                   if match_bet["pnl"] is not None
                   else "Ganancia: <i>(registra las odds para cálculo exacto)</i>")
        status_txt = "GANÓ 🎉"
    else:
        match_bet["status"]    = "loss"
        match_bet["resultado"] = "L"
        match_bet["pnl"]       = -stake
        pnl_txt  = f"Pérdida: <b>-${stake:.2f}</b>"
        status_txt = "PERDIÓ ❌"

    _save_bets(data)

    _send(chat_id,
          f"✅ <b>Resultado registrado</b>\n"
          f"{pick} → <b>{status_txt}</b>\n"
          f"{pnl_txt}\n"
          f"Usa /historial para ver el balance del día.")


# ── Dispatcher ──────────────────────────────────────────────────

def _dispatch(update: dict):
    msg     = update.get("message") or update.get("edited_message") or {}
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text    = (msg.get("text") or "").strip()

    if not chat_id:
        return

    # Handle photo messages — both compressed photos and images sent as documents
    _is_image_doc = (
        msg.get("document") and
        (msg["document"].get("mime_type") or "").startswith("image/")
    )
    if msg.get("photo") or _is_image_doc:
        print(f"  📸 Telegram: {'foto' if msg.get('photo') else 'imagen-documento'} recibida de chat_id={chat_id}")
        if not _is_authorized(chat_id):
            _send(chat_id, "⛔ No autorizado. Envía /start para registrarte.")
            return
        handle_photo(chat_id, msg)
        return

    if not text or not text.startswith("/"):
        return

    if not _is_authorized(chat_id):
        _send(chat_id, "⛔ No autorizado. Envía /start para registrarte.")
        return

    parts   = text.split(None, 1)
    cmd     = parts[0].lower().split("@")[0]
    args    = parts[1].strip() if len(parts) > 1 else ""

    handlers = {
        "/start":    lambda: _cmd_start(chat_id),
        "/ayuda":    lambda: _cmd_ayuda(chat_id),
        "/help":     lambda: _cmd_ayuda(chat_id),
        "/picks":        lambda: _cmd_picks(chat_id),
        "/mispicks":     lambda: _cmd_mispicks(chat_id),
        "/bankroll": lambda: _cmd_bankroll(chat_id),
        "/reporte":  lambda: _cmd_reporte(chat_id),
        "/clv":      lambda: _cmd_clv(chat_id),
        "/estado":   lambda: _cmd_estado(chat_id),
        "/salud":    lambda: _cmd_salud(chat_id),
        "/elite":    lambda: _cmd_elite(chat_id, args),
        "/hoy":      lambda: _cmd_hoy(chat_id),
        "/pitchers": lambda: _cmd_pitchers(chat_id),
        "/patrones": lambda: _cmd_patrones(chat_id),
        "/kprops":   lambda: _cmd_kprops(chat_id, args),
        "/contexto": lambda: _cmd_contexto(chat_id),
        "/analizar": lambda: _cmd_analizar(chat_id, args),
        "/mlb":      lambda: _cmd_mlb(chat_id),
        "/parlay":   lambda: _cmd_parlay(chat_id),
        "/aposte":        lambda: _cmd_aposte(chat_id, args),
        "/historial":     lambda: _cmd_historial(chat_id),
        "/resultado":     lambda: _cmd_resultado(chat_id, args),
    }

    handler = handlers.get(cmd)
    if handler:
        try:
            handler()
        except BaseException as e:
            # BaseException captura también MemoryError, RecursionError, etc.
            _etype = type(e).__name__
            print(f"  💥 HANDLER CRASH [{cmd}] [{_etype}]: {e} | RSS={_mem_rss_mb()}MB")
            _traceback.print_exc()
            try:
                _send(chat_id, f"⚠️ Error interno [{_etype}] procesando {cmd}\nVer Railway logs para detalles.")
            except Exception:
                pass
            if isinstance(e, (SystemExit, KeyboardInterrupt)):
                raise   # propagar shutdown signals
    else:
        _send(chat_id, f"❓ Comando desconocido: <code>{cmd}</code>\nUsa /ayuda.")


# ── Polling loop ────────────────────────────────────────────────

def _polling_loop():
    global _last_scan_time
    print("  📱 Telegram polling activo")
    _midnight_reset()   # archive previous day's bets if date changed
    offset = 0
    _last_reset_date = _ct_today()

    # Drenar actualizaciones pendientes antes de entrar al loop principal.
    # Evita procesar comandos viejos acumulados durante el downtime.
    try:
        _drain = _api("getUpdates", {"timeout": 0}, timeout=8)
        if _drain.get("ok") and _drain.get("result"):
            offset = _drain["result"][-1]["update_id"] + 1
            print(f"  🧹 Telegram: {len(_drain['result'])} actualizaciones pendientes descartadas "
                  f"(offset → {offset})")
    except Exception as _dr_err:
        print(f"  ⚠️  Telegram drain: {_dr_err}")

    while True:
        # Daily midnight reset check
        _today = _ct_today()
        if _today != _last_reset_date:
            _midnight_reset()
            _last_reset_date = _today

        try:
            resp = _api("getUpdates", {
                "offset":          offset,
                "timeout":         30,
                "allowed_updates": json.dumps(["message"]),
            }, timeout=40)
            if not resp:
                time.sleep(5)
                continue
            # Error 409 Conflict: otra instancia está haciendo polling
            if not resp.get("ok"):
                err_code = resp.get("error_code", 0)
                if err_code == 409:
                    print("  ⛔ Telegram 409 Conflict detectado — "
                          "otra instancia activa. Esperando 30 s antes de reintentar…")
                    time.sleep(30)
                    continue
                # Cualquier otro error no-ok → esperar y reintentar
                time.sleep(10)
                continue
            for update in resp.get("result", []):
                offset = update["update_id"] + 1
                # Log every incoming update type for diagnostics
                _upd_types = [k for k in update if k != "update_id"]
                _msg_keys  = list((update.get("message") or {}).keys())
                print(f"  📨 Telegram update #{update['update_id']}: {_upd_types} | msg keys: {_msg_keys}")
                try:
                    _dispatch(update)
                except BaseException as _de:
                    # BaseException (no solo Exception) — captura MemoryError, RecursionError, etc.
                    _de_type = type(_de).__name__
                    print(f"  💥 DISPATCH CRASH [{_de_type}]: {_de} | RSS={_mem_rss_mb()}MB")
                    _traceback.print_exc()
                    if isinstance(_de, (SystemExit, KeyboardInterrupt)):
                        raise   # dejar que el loop exterior lo maneje
                    # Para cualquier otra BaseException (MemoryError, etc.): logear y continuar
                    try:
                        _cmd = ((update.get("message") or {}).get("text") or "?").split()[0]
                        for _cid in list(_authorized_ids)[:1]:
                            _send(_cid, f"⚠️ Error interno [{_de_type}] — bot recuperado")
                    except Exception:
                        pass
        except BaseException as e:
            _et = type(e).__name__
            print(f"  💥 POLLING CRASH [{_et}]: {e} | RSS={_mem_rss_mb()}MB")
            _traceback.print_exc()
            if isinstance(e, (SystemExit, KeyboardInterrupt)):
                raise
            time.sleep(10)


# ── Public entry point ──────────────────────────────────────────

def iniciar_telegram(analyze_fn=None, get_odds_fn=None, build_text_fn=None,
                     get_hoy_fn=None, get_patrones_fn=None):
    """
    Inicia el bot de Telegram en un hilo daemon.
    Llamar una sola vez al arranque del bot, antes del while True:.

    Args:
        analyze_fn:      referencia a analyze_game_full(game, sport_key, prev_map)
        get_odds_fn:     referencia a get_odds(sport_key) → list[dict]
        build_text_fn:   referencia a build_analizar_text(result) → list[str]
        get_hoy_fn:      referencia a get_today_hoy_summary() → list[str]
        get_patrones_fn: referencia a detectar_patrones_getaway() → list[str]
    """
    global _analyze_fn, _get_odds_fn, _build_text_fn, _get_hoy_fn, _get_patrones_fn

    if not TELEGRAM_TOKEN:
        print("  ⚠️  Telegram: TELEGRAM_TOKEN no configurado — bot desactivado")
        print("       Obtén un token en @BotFather y agrégalo en Railway como TELEGRAM_TOKEN")
        return

    # ── Lock: una sola instancia de polling (previene error 409) ──────────────
    _my_pid = os.getpid()
    try:
        if os.path.exists(LOCK_FILE):
            try:
                with open(LOCK_FILE) as _lf:
                    _old_pid = int(_lf.read().strip() or 0)
            except (ValueError, OSError):
                _old_pid = 0
            if _old_pid and _old_pid != _my_pid:
                try:
                    os.kill(_old_pid, 0)   # señal 0 = solo verifica si el proceso existe
                    # Proceso anterior sigue vivo → otra instancia está haciendo polling
                    print(f"  ⛔ Telegram: instancia duplicada detectada (PID {_old_pid}) — "
                          f"polling omitido para evitar error 409. "
                          f"La instancia anterior terminará sola.")
                    return
                except (ProcessLookupError, PermissionError, OSError):
                    # Lock obsoleto (proceso muerto) → reemplazamos
                    print(f"  🔄 Telegram: lock obsoleto (PID {_old_pid} ya no existe) — "
                          f"tomando control")
        with open(LOCK_FILE, "w") as _lf:
            _lf.write(str(_my_pid))
        import atexit as _atexit
        def _release_lock():
            try:
                if os.path.exists(LOCK_FILE):
                    with open(LOCK_FILE) as _ck:
                        if _ck.read().strip() == str(_my_pid):
                            os.remove(LOCK_FILE)
            except Exception:
                pass
        _atexit.register(_release_lock)
        print(f"  🔒 Telegram: lock adquirido (PID {_my_pid})")
    except Exception as _le:
        print(f"  ⚠️  Telegram: no se pudo gestionar lock: {_le} — continuando de todas formas")

    # ── Limpiar webhook + cola pendiente (drop_pending_updates) ───────────────
    # Elimina cualquier webhook activo y descarta la cola de updates acumulados.
    # Esto previene el error 409 y evita que el bot procese comandos viejos.
    try:
        _dw = _api("deleteWebhook", {"drop_pending_updates": "true"})
        if _dw.get("ok"):
            print("  🧹 Telegram: webhook eliminado y cola de updates limpiada")
        else:
            print(f"  ⚠️  Telegram: deleteWebhook respondió: {_dw.get('description', '?')}")
    except Exception as _dwe:
        print(f"  ⚠️  Telegram: deleteWebhook falló: {_dwe}")

    _analyze_fn      = analyze_fn
    _get_odds_fn     = get_odds_fn
    _build_text_fn   = build_text_fn
    _get_hoy_fn      = get_hoy_fn
    _get_patrones_fn = get_patrones_fn

    # Auto-broadcast a Telegram deshabilitado intencionalmente.
    # Las alertas automáticas van solo a ntfy. Telegram responde únicamente
    # a comandos manuales del usuario (/analizar, /picks, /estado, etc.).
    print("  📵 Telegram: auto-broadcast deshabilitado — solo respuestas a comandos manuales")

    # Load authorized chat IDs from env + file
    _authorized_ids.update(_load_authorized())
    if _authorized_ids:
        print(f"  📱 Telegram: {len(_authorized_ids)} chat(s) autorizado(s)")
    else:
        print("  📱 Telegram: sin chat_id configurado — envía /start para registrarte")

    t = threading.Thread(target=_polling_loop, name="TelegramPolling", daemon=True)
    t.start()
    print(f"  🤖 Telegram bot listo (token: ...{TELEGRAM_TOKEN[-6:]})")


if __name__ == "__main__":
    print("Telegram bot — modo standalone (solo para pruebas)")
    iniciar_telegram()
    while True:
        time.sleep(60)
