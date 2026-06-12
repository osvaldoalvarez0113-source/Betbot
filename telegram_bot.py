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
import threading
import datetime
import urllib.request
import urllib.parse
import urllib.error

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CHATID_FILE      = "telegram_chat_id.txt"
TRACKER_FILE     = "paper_trades.json"
CLV_FILE         = "clv_tracker.json"
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
_start_time          = datetime.datetime.now()
_last_scan_time      = None   # updated by kelly_odds integration if desired


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
            return json.loads(r.read())
    except Exception as e:
        print(f"  ⚠️  Telegram API [{method}]: {e}")
        return {}


def _send(chat_id, text: str, parse_mode: str = "HTML"):
    _api("sendMessage", {
        "chat_id":    chat_id,
        "text":       text[:4000],
        "parse_mode": parse_mode,
    })


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
        "/picks     — picks pendientes de hoy\n"
        "/bankroll  — balance actual del bankroll\n"
        "/reporte   — reporte completo W/L/ROI\n"
        "/clv       — Closing Line Value (edge real)\n"
        "/estado    — salud del bot\n"
        "/hoy       — juegos MLB de hoy con pitchers y pick rápido\n"
        "/analizar  <code>Equipo A vs Equipo B</code> — análisis completo del partido\n"
        "/ayuda     — esta lista"
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


def _cmd_best_picks(chat_id: str, sport_key: str, emoji: str, label: str):
    """Analyze all games for sport_key, return top-5 picks by EV in one message."""
    if not _get_odds_fn or not _analyze_fn:
        _send(chat_id, "⚠️ Módulo de análisis no disponible (bot en modo básico).")
        return

    _send(chat_id, f"🔍 Buscando los mejores picks {label} de hoy...")

    try:
        games = _get_odds_fn(sport_key) or []
    except Exception as e:
        _send(chat_id, f"⚠️ Error al obtener partidos: {e}")
        return

    if not games:
        _send(chat_id, f"Sin partidos de {label} programados para hoy.")
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
            f"   Odds: {pk['odds']:+.0f} ({pk['book']})\n"
            f"   Prob: {pk['prob']}% | EV +{pk['ev']:.1f}%"
            f"{votos_line}{razon_line}"
        )

    _send(chat_id, "\n".join(lines))


def _cmd_picks(chat_id: str):
    _cmd_best_picks(chat_id, "baseball_mlb", "⚾", "MLB")


def _cmd_picks_futbol(chat_id: str):
    _cmd_best_picks(chat_id, "soccer_fifa_world_cup", "🏆", "del Mundial")


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
    today_str  = datetime.datetime.now().strftime("%Y-%m-%d")
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
    try:
        from paquete_avanzado import clv_tracker
        txt = clv_tracker.reporte_clv()
    except Exception:
        clv_data = _load_json(CLV_FILE, {"picks": []})
        con_clv  = [p for p in clv_data.get("picks", []) if p.get("clv_pct") is not None]
        if not con_clv:
            txt = "Sin datos de CLV todavía. Necesitas al menos 20 picks resueltos."
        else:
            prom = sum(p["clv_pct"] for p in con_clv) / len(con_clv)
            txt  = (
                f"📈 REPORTE CLV\n"
                f"{'─'*28}\n"
                f"Picks medidos: {len(con_clv)}\n"
                f"CLV promedio: {prom:+.2f}%"
            )
    _send(chat_id, f"<pre>{txt}</pre>")


def _cmd_estado(chat_id: str):
    uptime  = datetime.datetime.now() - _start_time
    horas   = int(uptime.total_seconds() // 3600)
    minutos = int((uptime.total_seconds() % 3600) // 60)
    trades  = _load_json(TRACKER_FILE, {"picks": []})
    hoy     = datetime.date.today().isoformat()
    picks_hoy = sum(1 for p in trades.get("picks", []) if p.get("fecha") == hoy)
    tk_ok   = "✅" if TELEGRAM_TOKEN else "❌"
    anlz_ok = "✅" if _analyze_fn else "❌"
    odds_ok = "✅" if _get_odds_fn else "❌"
    _send(chat_id, (
        f"🔧 <b>Estado del Bot</b>\n\n"
        f"Uptime:   {horas}h {minutos}m\n"
        f"Picks hoy: {picks_hoy}\n\n"
        f"Módulos:\n"
        f"  Telegram:  {tk_ok}\n"
        f"  Análisis:  {anlz_ok}\n"
        f"  Odds API:  {odds_ok}\n\n"
        f"Fecha: {hoy}"
    ))


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
    if not args or " vs " not in args.lower():
        _send(chat_id,
              "⚠️ Formato: /analizar <code>Equipo Local vs Equipo Visitante</code>\n"
              "Ejemplo: /analizar Cubs vs Cardinals")
        return

    if not _get_odds_fn or not _analyze_fn:
        _send(chat_id, "⚠️ Módulo de análisis no disponible (bot en modo básico).")
        return

    partes = args.split(" vs ", 1)
    home_raw = partes[0].strip()
    away_raw = partes[1].strip()
    home_q = _translate_team_name(home_raw).lower()
    away_q = _translate_team_name(away_raw).lower()

    _send(chat_id, f"🔍 Buscando <b>{home_raw} vs {away_raw}</b>…")

    game_found  = None
    sport_found = "baseball_mlb"

    for sport in ["baseball_mlb", "soccer_fifa_world_cup",
                  "soccer_epl", "soccer_uefa_champs_league",
                  "soccer_usa_mls", "soccer_spain_la_liga"]:
        try:
            games = _get_odds_fn(sport)
            if not games:
                print(f"  [analizar] {sport}: sin juegos disponibles (API vacía o error)")
                continue
            for g in games:
                gh = g.get("home_team", "")
                ga = g.get("away_team", "")
                # Orden normal: home_q → local, away_q → visitante
                # Orden inverso: home_q → visitante, away_q → local
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

    if not game_found:
        _send(chat_id,
              f"❌ No encontré el partido <b>{partes[0].strip()} vs {partes[1].strip()}</b>.\n"
              "Verifica los nombres y que el partido esté en las próximas 48 horas.")
        return

    try:
        result = _analyze_fn(game_found, sport_found, {}, force_panel=True)
    except Exception as e:
        _send(chat_id, f"⚠️ Error en el análisis: {e}")
        return

    if not result:
        _send(chat_id, (
            "⚠️ No se pudo obtener análisis para este partido.\n"
            "Posibles causas: partido no encontrado en API, datos insuficientes, "
            "o error de conexión. Intenta de nuevo en unos minutos."
        ))
        return

    # ── Usar build_analizar_text si está disponible (formato completo) ────────
    if _build_text_fn:
        try:
            parts = _build_text_fn(result)
            for part in parts:
                if part and part.strip():
                    _send(chat_id, part)
            return
        except Exception as _bte:
            _send(chat_id, f"⚠️ Error al formatear análisis: {_bte}")
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
            _send(chat_id, f"⚠️ Sin datos suficientes para {pair[0]} vs {pair[1]}.")
            continue

        if _build_text_fn:
            try:
                parts = _build_text_fn(result)
                for part in parts:
                    if part and part.strip():
                        _send(chat_id, part)
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
                    _send(cid, part)
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
                _send(chat_id, part)
    except Exception as e:
        _send(chat_id, f"⚠️ Error obteniendo juegos: {e}")


def _cmd_bulk_analysis(chat_id: str, sport_key: str, emoji: str, label: str):
    """Shared logic for /mlb and /mundial — analyzes all games for a sport key."""
    if not _get_odds_fn or not _analyze_fn:
        _send(chat_id, "⚠️ Módulo de análisis no disponible (bot en modo básico).")
        return

    _send(chat_id, f"{emoji} Analizando partidos {label} de hoy... dame un momento")

    try:
        games = _get_odds_fn(sport_key) or []
    except Exception as e:
        _send(chat_id, f"⚠️ Error al obtener partidos: {e}")
        return

    if not games:
        no_msg = {
            "baseball_mlb":          "⚾ Sin partidos MLB programados para hoy.",
            "soccer_fifa_world_cup":  "🏆 Sin partidos del Mundial programados para hoy.",
        }
        _send(chat_id, no_msg.get(sport_key, f"Sin partidos de {label} para hoy."))
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
                        _send(chat_id, part)
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


def _cmd_mundial(chat_id: str):
    _cmd_bulk_analysis(chat_id, "soccer_fifa_world_cup", "🏆", "del Mundial")


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

    # ── gather picks ─────────────────────────────────────────────────────────
    mlb_picks   = _collect_picks("baseball_mlb")
    wc_picks    = _collect_picks("soccer_fifa_world_cup")
    all_picks   = mlb_picks + wc_picks

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
        # Block ML+RL of same team
        if mtype in ("ml", "rl") and key in used_team_market:
            continue
        # Try to have at least 1 MLB + 1 Mundial if both available
        if (len(legs) == 0 and wc_picks and mlb_picks
                and pk["sport"] == "soccer_fifa_world_cup"
                and not any(l["sport"] == "baseball_mlb" for l in legs)):
            # Defer first WC pick until we have at least 1 MLB leg
            continue
        legs.append(pk)
        used_matches.add(pk["match"])
        used_team_market.add(key)

    # If we deferred WC picks and still have room, fill from all_picks again
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
            f"{leg['odds']:+.0f} @ {leg['book']}"
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


def _cmd_parlay_futbol(chat_id: str):
    """Build the best 2-3 leg parlay from today's strongest FIFA World Cup picks."""
    if not _get_odds_fn or not _analyze_fn:
        _send(chat_id, "⚠️ Módulo de análisis no disponible.")
        return

    _send(chat_id, "🏆 Armando el mejor parlay del Mundial de hoy...")

    GOOD_BOOKS = {"bovada", "betonline", "betonline.ag"}

    def _to_decimal(american: float) -> float:
        if american >= 0:
            return round(american / 100 + 1, 4)
        return round(100 / abs(american) + 1, 4)

    def _market_type_soccer(label: str) -> str:
        low = label.lower()
        if any(x in low for x in ("handicap", "asian handicap", "spread", "+", "-")):
            return "handicap"
        if any(x in low for x in ("over", "under", "total")):
            return "total"
        return "ml"

    # ── collect and filter picks ──────────────────────────────────────────────
    try:
        games = _get_odds_fn("soccer_fifa_world_cup") or []
    except Exception as e:
        _send(chat_id, f"⚠️ Error al obtener partidos: {e}")
        return

    if not games:
        _send(chat_id,
              "Sin partidos del Mundial hoy. Revisa mañana con /picksfutbol 📅")
        return

    all_picks = []
    for game in sorted(games, key=lambda g: g.get("commence_time", ""))[:20]:
        try:
            result = _analyze_fn(game, "soccer_fifa_world_cup", {}, force_panel=False)
        except Exception:
            continue
        if not result:
            continue
        intel    = result.get("claude_intel") or {}
        panel_ok = intel.get("apostar") is True
        razon_raw = intel.get("razonamiento", "")
        razon = (razon_raw[:77] + "…") if len(razon_raw) > 80 else razon_raw
        match = result.get("match", "")
        for cand in result.get("candidates") or []:
            ev   = cand.get("ev_pct", 0)
            prob = cand.get("true_prob", 0)
            book = (cand.get("book") or "").lower()
            if (panel_ok and ev >= 5.0 and prob >= 0.55
                    and book in GOOD_BOOKS):
                all_picks.append({
                    "match":  match,
                    "label":  cand.get("label", "?"),
                    "odds":   cand.get("odds", 0),
                    "dec":    _to_decimal(cand.get("odds", 0)),
                    "book":   cand.get("book", ""),
                    "prob":   prob,
                    "ev":     ev,
                    "mtype":  _market_type_soccer(cand.get("label", "")),
                    "razon":  razon,
                })

    if not all_picks:
        _send(chat_id,
              "No hay suficientes picks fuertes en el Mundial hoy para armar parlay.\n"
              "Usa /picksfutbol para ver los picks individuales disponibles.")
        return

    # Sort by EV descending
    all_picks.sort(key=lambda x: -x["ev"])

    # ── select legs (anti-correlation for soccer) ─────────────────────────────
    def _team_from_label_soccer(label: str) -> str:
        for prefix in ("moneyline ", "ml ", "draw no bet ", "handicap ",
                       "asian handicap ", "over ", "under ", "total over ",
                       "total under "):
            if label.lower().startswith(prefix):
                rest = label[len(prefix):]
                return rest.split()[0].lower()
        return label.lower().split()[0]

    legs = []
    used_matches = set()
    used_team_market = set()  # (team, mtype) — block ML+handicap same team

    # Prefer: 1 ML + 1 total from different match
    # Strategy: first pass prefer total after ML, second pass fill freely
    for pk in all_picks:
        if len(legs) >= 3:
            break
        if pk["match"] in used_matches:
            continue
        team  = _team_from_label_soccer(pk["label"])
        key   = (team, pk["mtype"])
        # Block ML + handicap same team
        block_types = {"ml": "handicap", "handicap": "ml"}
        conflict_key = (team, block_types.get(pk["mtype"], ""))
        if conflict_key in used_team_market:
            continue
        # Prefer mixing ML and total across different matches
        existing_types = {l["mtype"] for l in legs}
        if legs and pk["mtype"] == "ml" and "ml" in existing_types and "total" not in existing_types:
            continue  # defer: prefer a total next
        legs.append(pk)
        used_matches.add(pk["match"])
        used_team_market.add(key)

    # Second pass — fill remaining slots without the "prefer total" constraint
    if len(legs) < 2:
        for pk in all_picks:
            if len(legs) >= 3:
                break
            if pk["match"] in used_matches:
                continue
            team  = _team_from_label_soccer(pk["label"])
            key   = (team, pk["mtype"])
            block_types = {"ml": "handicap", "handicap": "ml"}
            conflict_key = (team, block_types.get(pk["mtype"], ""))
            if conflict_key in used_team_market:
                continue
            legs.append(pk)
            used_matches.add(pk["match"])
            used_team_market.add(key)

    if len(legs) < 2:
        _send(chat_id,
              "No hay suficientes picks fuertes en el Mundial hoy para armar parlay.\n"
              "Usa /picksfutbol para ver los picks individuales disponibles.")
        return

    # ── calculate parlay stats ────────────────────────────────────────────────
    bankroll  = _load_json(TRACKER_FILE, {"bankroll": 1000.0}).get("bankroll", 1000.0)
    stake     = max(10.0, min(20.0, bankroll * 0.02))
    odds_comb = 1.0
    prob_comb = 1.0
    for leg in legs:
        odds_comb *= leg["dec"]
        prob_comb *= leg["prob"]
    ganancia  = stake * odds_comb
    ev_parlay = (prob_comb * odds_comb - 1) * 100

    # ── build message ─────────────────────────────────────────────────────────
    DIV  = "━" * 22
    RANK = ["1️⃣", "2️⃣", "3️⃣"]
    lines = [f"🏆 <b>MEJOR PARLAY MUNDIAL HOY</b>\n{DIV}\n"]
    for i, leg in enumerate(legs):
        razon_block = f"\n   <i>'{leg['razon']}'</i>" if leg["razon"] else ""
        lines.append(
            f"{RANK[i]} <b>{leg['label']}</b>\n"
            f"   {round(leg['prob']*100,1)}% | EV +{leg['ev']:.1f}% | "
            f"{leg['odds']:+.0f} @ {leg['book']}"
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
        "/picksfutbol":  lambda: _cmd_picks_futbol(chat_id),
        "/mispicks":     lambda: _cmd_mispicks(chat_id),
        "/bankroll": lambda: _cmd_bankroll(chat_id),
        "/reporte":  lambda: _cmd_reporte(chat_id),
        "/clv":      lambda: _cmd_clv(chat_id),
        "/estado":   lambda: _cmd_estado(chat_id),
        "/hoy":      lambda: _cmd_hoy(chat_id),
        "/analizar": lambda: _cmd_analizar(chat_id, args),
        "/mlb":      lambda: _cmd_mlb(chat_id),
        "/mundial":  lambda: _cmd_mundial(chat_id),
        "/parlay":        lambda: _cmd_parlay(chat_id),
        "/parlayfutbol":  lambda: _cmd_parlay_futbol(chat_id),
    }

    handler = handlers.get(cmd)
    if handler:
        try:
            handler()
        except Exception as e:
            print(f"  ⚠️  Telegram handler [{cmd}]: {e}")
            _send(chat_id, f"⚠️ Error procesando {cmd}: {e}")
    else:
        _send(chat_id, f"❓ Comando desconocido: <code>{cmd}</code>\nUsa /ayuda.")


# ── Polling loop ────────────────────────────────────────────────

def _polling_loop():
    global _last_scan_time
    print("  📱 Telegram polling activo")
    offset = 0

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
                except Exception as _de:
                    print(f"  ⚠️  Telegram dispatch error: {_de}")
        except Exception as e:
            print(f"  ⚠️  Telegram polling error: {e}")
            time.sleep(10)


# ── Public entry point ──────────────────────────────────────────

def iniciar_telegram(analyze_fn=None, get_odds_fn=None, build_text_fn=None, get_hoy_fn=None):
    """
    Inicia el bot de Telegram en un hilo daemon.
    Llamar una sola vez al arranque del bot, antes del while True:.

    Args:
        analyze_fn:    referencia a analyze_game_full(game, sport_key, prev_map)
        get_odds_fn:   referencia a get_odds(sport_key) → list[dict]
        build_text_fn: referencia a build_analizar_text(result) → list[str]
        get_hoy_fn:    referencia a get_today_hoy_summary() → list[str]
    """
    global _analyze_fn, _get_odds_fn, _build_text_fn, _get_hoy_fn

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

    _analyze_fn    = analyze_fn
    _get_odds_fn   = get_odds_fn
    _build_text_fn = build_text_fn
    _get_hoy_fn    = get_hoy_fn

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
