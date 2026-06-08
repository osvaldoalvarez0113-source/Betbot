#!/usr/bin/env python3
"""
BetBot Pro — ML Ensemble Model (Level 1A v3)

Changes from v2:
  1. Historical training data — downloads the last 2 completed MLB regular-season
     from the MLB Stats API (~4,800+ games per season) so the model learns from
     thousands of real game outcomes, not just the bot's limited backtest_log.csv.
  2. Pinnacle calibration — pinnacle_calibrate_prob() hard-clamps the model's
     output to within 15pp of Pinnacle's implied probability.  A model should
     never output 87% when the sharpest market says 50%.

Architecture:
  Model 1: RandomForest       (base weight 0.50)
  Model 2: GradientBoosting   (base weight 0.30)
  Model 3: LogisticRegression (base weight 0.20)
  All 3 wrapped with CalibratedClassifierCV(method='isotonic', cv=5).
  Final weights normalised by test-set accuracy after training.

Target  : 1 = UNDER wins (actual_total <= book_line), 0 = OVER wins
Features: [park_factor, book_line, projection, model_edge, is_over_pick]

Usage (standalone):
  python3 ml_model.py           # download history + train all 3 models → model.pkl
  python3 ml_model.py test      # load model.pkl and run a quick test prediction
  python3 ml_model.py fetch     # only refresh mlb_historical.json cache
"""
import os, sys, csv, json, pickle
from datetime import date

MODEL_FILE    = "model.pkl"
BACKTEST_CSV  = "backtest_log.csv"
HIST_CACHE    = "mlb_historical.json"  # local cache of MLB Stats API results
HIST_MAX_AGE  = 7                      # days before cache is considered stale
MIN_GAMES     = 100                    # minimum total rows needed to train
ACC_FLOOR     = 0.524                  # below this, ensemble is coin-flip territory
MLB_BOOK_LINE = 8.5                    # median MLB O/U line — proxy when real line unknown

# Maximum allowed divergence between model output and Pinnacle implied probability
PINNACLE_MAX_DIVERGENCE = 0.15         # 15 percentage points

# ── In-memory state ──────────────────────────────────────────────────────────
_ensemble:     "dict | None" = None
_n_samples:    int   = 0
_trained:      bool  = False
_ensemble_acc: float = 0.0


# ════════════════════════════════════════════════════════════════════════════
# HISTORICAL DATA — MLB Stats API
# ════════════════════════════════════════════════════════════════════════════

def _last_two_seasons() -> list:
    """
    Return the years of the last two *completed* MLB regular seasons.
    The MLB regular season runs approximately April–October.
    In June 2026: returns [2024, 2025] (2025 ended Oct 2025, 2024 ended Oct 2024).
    """
    today = date.today()
    # If before April, the current calendar year's season hasn't started yet
    latest_completed = today.year - 1 if today.month < 4 else today.year - 1
    return [latest_completed - 1, latest_completed]


def _fetch_mlb_season_results(season: int) -> list:
    """
    Fetch all completed regular-season games for *season* from the MLB Stats API.

    Endpoint: GET /api/v1/schedule?sportId=1&gameType=R&season=YEAR
    Returns list of dicts: {date, home_team, away_team, home_score, away_score}
    Returns [] gracefully on any network or parse error.
    """
    import urllib.request

    url = (
        f"https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&gameType=R&season={season}"
        f"&fields=dates,date,games,gamePk,status,statusCode,"
        f"teams,home,away,team,name,score"
    )
    games = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BetBot/1.0"})
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read())

        for date_entry in data.get("dates", []):
            date_str = date_entry.get("date", "")
            for g in date_entry.get("games", []):
                status = (g.get("status") or {}).get("statusCode", "")
                # Accept all "Final" status codes: F, FT (tie), FR (replay)
                if status not in ("F", "FT", "FR"):
                    continue
                t      = g.get("teams", {})
                home_t = t.get("home", {})
                away_t = t.get("away", {})
                h_sc   = home_t.get("score")
                a_sc   = away_t.get("score")
                h_name = (home_t.get("team") or {}).get("name", "")
                a_name = (away_t.get("team") or {}).get("name", "")
                if h_sc is None or a_sc is None or not h_name or not a_name:
                    continue
                games.append({
                    "date":       date_str,
                    "home_team":  h_name,
                    "away_team":  a_name,
                    "home_score": int(h_sc),
                    "away_score": int(a_sc),
                })

        print(f"  📥 {season}: {len(games)} partidos de MLB Stats API")

    except Exception as e:
        print(f"  ⚠️  _fetch_mlb_season_results({season}): {e}")

    return games


def load_or_fetch_historical(force: bool = False) -> list:
    """
    Return a combined list of historical game dicts from the last two seasons.

    Uses a local JSON cache (HIST_CACHE) when it is ≤ HIST_MAX_AGE days old
    to avoid redundant API calls on every retrain.  Pass force=True to bypass
    the cache and always re-fetch.

    Returns [] if both the cache is missing/stale AND the API is unreachable.
    """
    if not force and os.path.isfile(HIST_CACHE):
        try:
            with open(HIST_CACHE, encoding="utf-8") as f:
                cached = json.load(f)
            fetched_date = date.fromisoformat(cached.get("fetched", "2000-01-01"))
            age_days = (date.today() - fetched_date).days
            if age_days <= HIST_MAX_AGE:
                games = cached.get("games", [])
                print(f"  📂 Histórico MLB: {len(games)} juegos (caché {age_days}d)")
                return games
        except Exception:
            pass  # treat as stale and re-fetch

    seasons = _last_two_seasons()
    print(f"  🌐 Descargando historial MLB {seasons[0]} y {seasons[1]}...")
    all_games: list = []
    for s in seasons:
        all_games.extend(_fetch_mlb_season_results(s))

    if all_games:
        try:
            with open(HIST_CACHE, "w", encoding="utf-8") as f:
                json.dump(
                    {"fetched": date.today().isoformat(), "games": all_games},
                    f,
                )
            print(f"  💾 Caché guardado: {len(all_games)} juegos → {HIST_CACHE}")
        except Exception as e:
            print(f"  ⚠️  No se pudo guardar caché histórico: {e}")
    else:
        print("  ⚠️  MLB Stats API no disponible — solo se usará backtest_log.csv")

    return all_games


def _build_historical_features(games: list) -> "tuple[list, list]":
    """
    Convert raw historical game results into (X, y) training pairs.

    Feature engineering:
      Each team's RS/RA averages are computed from the full game list
      (season-level averages; no data leakage since these are whole-season
      means applied uniformly to every game in the same list).

      proj = (home_RS_avg + home_RA_avg + away_RS_avg + away_RA_avg) / 2

    Book-line proxy: MLB_BOOK_LINE (8.5) — used in absence of real sportsbook lines.
    Feature vector:  [park_factor, book_line_proxy, proj, model_edge, is_over=0]
    Target:          y = 1  if actual_total <= MLB_BOOK_LINE  (UNDER wins)
                     y = 0  otherwise                         (OVER wins)

    All historical rows use is_over=0 (neutral — no specific bet side assumed;
    the model learns the underlying run-distribution relative to 8.5).
    """
    if not games:
        return [], []

    # Season-level averages
    team_rs: dict = {}
    team_ra: dict = {}
    for g in games:
        ht, at = g["home_team"], g["away_team"]
        hs, as_ = int(g["home_score"]), int(g["away_score"])
        team_rs.setdefault(ht, []).append(hs)
        team_ra.setdefault(ht, []).append(as_)
        team_rs.setdefault(at, []).append(as_)
        team_ra.setdefault(at, []).append(hs)

    avg_rs = {t: sum(v) / len(v) for t, v in team_rs.items()}
    avg_ra = {t: sum(v) / len(v) for t, v in team_ra.items()}
    league_avg = sum(avg_rs.values()) / max(len(avg_rs), 1)

    X: list = []
    y: list = []
    for g in games:
        ht, at  = g["home_team"], g["away_team"]
        actual  = int(g["home_score"]) + int(g["away_score"])

        h_rs = avg_rs.get(ht, league_avg)
        h_ra = avg_ra.get(ht, league_avg)
        a_rs = avg_rs.get(at, league_avg)
        a_ra = avg_ra.get(at, league_avg)

        proj       = (h_rs + h_ra + a_rs + a_ra) / 2
        park_f     = proj / MLB_BOOK_LINE
        model_edge = abs(proj - MLB_BOOK_LINE)
        is_over    = 0   # neutral — not betting a specific side

        X.append([park_f, MLB_BOOK_LINE, proj, model_edge, is_over])
        y.append(1 if actual <= MLB_BOOK_LINE else 0)

    return X, y


# ════════════════════════════════════════════════════════════════════════════
# PINNACLE CALIBRATION CLAMP
# ════════════════════════════════════════════════════════════════════════════

def pinnacle_calibrate_prob(
    model_prob: float,
    pinnacle_prob: "float | None",
    max_divergence: float = PINNACLE_MAX_DIVERGENCE,
) -> float:
    """
    Hard-clamp model_prob to be within max_divergence of pinnacle_prob.

    Prevents the model from outputting 87% when the sharpest market in the
    world implies 50%.  With max_divergence=0.15 the output is forced into
    the interval [pinnacle_prob - 0.15, pinnacle_prob + 0.15].

    Args:
        model_prob:    raw model output (0.0 – 1.0)
        pinnacle_prob: Pinnacle's implied probability for the SAME side (0.0 – 1.0)
        max_divergence: maximum allowed divergence in probability units
                        (default 0.15 = 15 percentage points)

    Returns model_prob unchanged when pinnacle_prob is None (Pinnacle unavailable).

    Examples:
        pinnacle_calibrate_prob(0.87, 0.50)  → 0.65  (clamped down)
        pinnacle_calibrate_prob(0.55, 0.70)  → 0.55  (within 15pp, unchanged)
        pinnacle_calibrate_prob(0.30, 0.52)  → 0.37  (clamped up)
    """
    if pinnacle_prob is None:
        return float(model_prob)
    lo = max(0.01, float(pinnacle_prob) - max_divergence)
    hi = min(0.99, float(pinnacle_prob) + max_divergence)
    return round(max(lo, min(hi, float(model_prob))), 4)


# ════════════════════════════════════════════════════════════════════════════
# BACKTEST DATA
# ════════════════════════════════════════════════════════════════════════════

def _load_backtest_data() -> "tuple[list, list]":
    """
    Parse backtest_log.csv into (X, y).
    Skips PUSH rows. Returns ([], []) when file is missing or too few rows.
    """
    rows = []
    if not os.path.isfile(BACKTEST_CSV):
        return [], []
    try:
        with open(BACKTEST_CSV, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("result", "") not in ("WIN", "LOSS"):
                    continue
                try:
                    actual = float(row.get("actual", 0))
                    book   = float(row.get("book_line", 8.5))
                    proj   = float(row.get("projection", book))
                except (ValueError, TypeError):
                    continue
                if book <= 0:
                    continue
                y_label   = 1 if actual <= book else 0
                park_f    = proj / book
                model_edge = abs(proj - book)
                is_over   = 1 if row.get("our_pick", "") == "OVER" else 0
                rows.append(([park_f, book, proj, model_edge, is_over], y_label))
    except Exception as e:
        print(f"  ⚠️  _load_backtest_data: {e}")
        return [], []
    return [r[0] for r in rows], [r[1] for r in rows]


# ════════════════════════════════════════════════════════════════════════════
# TRAINING
# ════════════════════════════════════════════════════════════════════════════

def train(force: bool = False, use_historical: bool = True) -> bool:
    """
    Train the 3-model calibrated ensemble and save to model.pkl.

    Data sources combined:
      1. backtest_log.csv  — bot's own betting history (precise features + real bet sides)
      2. MLB Stats API     — last 2 seasons of real game results (~4,800–5,000 games)

    Each model is wrapped with CalibratedClassifierCV(method='isotonic', cv=5)
    for proper probability calibration: "70% UNDER" should win ~70% of the time.
    Weights are set proportional to each model's test-set accuracy.

    Args:
        force:          ignored (kept for API compatibility)
        use_historical: if True (default), downloads/loads MLB Stats API history
    """
    global _ensemble, _n_samples, _trained, _ensemble_acc
    try:
        from sklearn.ensemble import (RandomForestClassifier,
                                      GradientBoostingClassifier)
        from sklearn.linear_model  import LogisticRegression
        from sklearn.calibration   import CalibratedClassifierCV
        from sklearn.model_selection import train_test_split
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline      import Pipeline
        from sklearn.metrics       import accuracy_score, brier_score_loss
    except ImportError:
        print("  ⚠️  scikit-learn no instalado — ML ensemble desactivado")
        print("       pip install scikit-learn")
        return False

    # ── 1. Combine data sources ──────────────────────────────────────────────
    X_bt, y_bt = _load_backtest_data()
    print(f"  📊 backtest_log.csv : {len(X_bt)} partidos")

    X_hist, y_hist = [], []
    if use_historical:
        hist_games = load_or_fetch_historical()
        X_hist, y_hist = _build_historical_features(hist_games)
        print(f"  📊 MLB histórico    : {len(X_hist)} partidos")

    X = X_bt + X_hist
    y = y_bt + y_hist
    n = len(X)

    if n < MIN_GAMES:
        print(f"  ℹ️  Ensemble ML: {n} partidos < {MIN_GAMES} mínimo — sin entrenamiento")
        return False

    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )

    # ── 2. Base estimators (more trees to exploit larger dataset) ────────────
    base_models = {
        "RandomForest": RandomForestClassifier(
            n_estimators=300, max_depth=7, min_samples_leaf=5,
            class_weight="balanced", random_state=42, n_jobs=-1,
        ),
        "GradientBoosting": GradientBoostingClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, random_state=42,
        ),
        "LogisticRegression": Pipeline([
            ("scaler", StandardScaler()),
            ("lr", LogisticRegression(
                C=1.0, class_weight="balanced",
                max_iter=500, random_state=42,
            )),
        ]),
    }

    # ── 3. Calibrate with isotonic regression + evaluate ────────────────────
    trained_models: dict = {}
    acc_total = 0.0
    print(
        f"  🤖 Entrenando ensemble "
        f"({n} partidos: {len(X_bt)} backtest + {len(X_hist)} histórico)..."
    )
    for name, base in base_models.items():
        # CalibratedClassifierCV with isotonic regression:
        # fits a monotone non-decreasing function from raw scores → probabilities
        # using 5-fold cross-validation so calibration generalises to unseen data.
        cal = CalibratedClassifierCV(base, cv=5, method="isotonic")
        cal.fit(X_tr, y_tr)
        preds  = cal.predict(X_te)
        probas = cal.predict_proba(X_te)
        acc    = accuracy_score(y_te, preds)
        try:
            classes = list(cal.classes_)
            under_i = classes.index(1) if 1 in classes else 1
            brier   = brier_score_loss(y_te, [p[under_i] for p in probas])
        except Exception:
            brier   = 0.25
        verdict = "✅" if acc >= ACC_FLOOR else "⚠️"
        print(f"    {name:22s}  acc={acc:.1%}  brier={brier:.3f}  {verdict}")
        trained_models[name] = {
            "model": cal,
            "acc":   float(acc),
            "brier": float(brier),
        }
        acc_total += acc

    # ── 4. Accuracy-proportional weights ────────────────────────────────────
    for name in trained_models:
        trained_models[name]["weight"] = (
            trained_models[name]["acc"] / acc_total if acc_total > 0 else 1 / 3
        )

    w_acc = sum(m["acc"] * m["weight"] for m in trained_models.values())
    print(f"  🎯 Ensemble — precisión ponderada: {w_acc:.1%}")

    payload = {
        "models":       trained_models,
        "n_samples":    n,
        "ensemble_acc": float(w_acc),
    }
    with open(MODEL_FILE, "wb") as f:
        pickle.dump(payload, f)

    _ensemble     = trained_models
    _n_samples    = n
    _trained      = True
    _ensemble_acc = w_acc
    return True


# ════════════════════════════════════════════════════════════════════════════
# LOADING
# ════════════════════════════════════════════════════════════════════════════

def load() -> bool:
    """Load model.pkl into memory. Returns True if successful."""
    global _ensemble, _n_samples, _trained, _ensemble_acc
    if _trained:
        return True
    if not os.path.isfile(MODEL_FILE):
        return False
    try:
        with open(MODEL_FILE, "rb") as f:
            saved = pickle.load(f)
        if "models" in saved:
            _ensemble     = saved["models"]
            _n_samples    = saved.get("n_samples", 0)
            _ensemble_acc = saved.get("ensemble_acc", 0.0)
        else:
            # Legacy v1 single-model format
            _ensemble = {"RandomForest": {
                "model":  saved["model"],
                "acc":    saved.get("accuracy", 0.0),
                "weight": 1.0,
            }}
            _n_samples    = saved.get("n_samples", 0)
            _ensemble_acc = saved.get("accuracy", 0.0)
        _trained = True
        print(
            f"  🤖 Ensemble cargado — {_n_samples} partidos, "
            f"acc ponderada {_ensemble_acc:.1%} "
            f"({len(_ensemble)} modelo(s))"
        )
        return True
    except Exception as e:
        print(f"  ⚠️  ML load error: {e}")
        return False


# ════════════════════════════════════════════════════════════════════════════
# PREDICTION
# ════════════════════════════════════════════════════════════════════════════

def _feature_vector(book_line: float, projection: float,
                    is_over_pick: bool) -> list:
    park_f     = projection / book_line if book_line > 0 else 1.0
    model_edge = abs(projection - book_line)
    is_over    = 1 if is_over_pick else 0
    return [park_f, book_line, projection, model_edge, is_over]


def _under_idx(model) -> int:
    """Return the column index for class 1 (UNDER) in predict_proba output."""
    try:
        classes = list(model.classes_)
        return classes.index(1) if 1 in classes else 1
    except Exception:
        return 1


def predict_ensemble(
    book_line: float,
    projection: float,
    is_over_pick: bool,
) -> "dict | None":
    """
    Run all 3 calibrated models and return a dict with per-model probabilities
    plus the weighted consensus.

    Returns:
      {
        "RandomForest":       0.68,
        "GradientBoosting":   0.71,
        "LogisticRegression": 0.65,
        "consensus":          0.685,   # accuracy-weighted average P(UNDER wins)
        "side":               "UNDER", # winning side at consensus
        "confidence":         0.685,   # probability of the predicted winning side
      }
    or None if ensemble is unavailable or below accuracy floor.
    """
    global _ensemble, _trained
    if not _trained:
        if not load():
            return None
    if _ensemble is None or _ensemble_acc < ACC_FLOOR:
        return None

    x = [_feature_vector(book_line, projection, is_over_pick)]
    per_model:    dict  = {}
    weighted_sum: float = 0.0
    weight_total: float = 0.0

    for name, info in _ensemble.items():
        try:
            proba   = info["model"].predict_proba(x)[0]
            ui      = _under_idx(info["model"])
            under_p = float(proba[ui])
            w       = float(info.get("weight", 1.0))
            per_model[name] = round(under_p, 4)
            weighted_sum   += under_p * w
            weight_total   += w
        except Exception as e:
            print(f"  ⚠️  Ensemble predict [{name}]: {e}")

    if not per_model or weight_total == 0:
        return None

    consensus  = round(weighted_sum / weight_total, 4)
    side       = "UNDER" if consensus >= 0.5 else "OVER"
    confidence = consensus if side == "UNDER" else (1.0 - consensus)

    return {
        **per_model,
        "consensus":  consensus,
        "side":       side,
        "confidence": round(confidence, 4),
    }


def predict_under_prob(
    book_line: float,
    projection: float,
    is_over_pick: bool,
    pinnacle_prob: "float | None" = None,
) -> "float | None":
    """
    Return the weighted-ensemble probability (0–1) that UNDER wins.

    If pinnacle_prob is provided (Pinnacle's implied probability for the UNDER
    side), the raw model output is hard-clamped to within PINNACLE_MAX_DIVERGENCE
    (15pp) of that value via pinnacle_calibrate_prob().  This prevents the model
    from diverging wildly from the sharpest pricing in the world.

    Drop-in replacement for the old single-model function.
    Returns None when ensemble is unavailable or below accuracy floor.
    """
    result = predict_ensemble(book_line, projection, is_over_pick)
    if result is None:
        return None
    prob = result["consensus"]
    if pinnacle_prob is not None:
        prob = pinnacle_calibrate_prob(prob, pinnacle_prob)
    return prob


# ════════════════════════════════════════════════════════════════════════════
# ALERT FORMATTING
# ════════════════════════════════════════════════════════════════════════════

def ensemble_alert_line(book_line: float, projection: float,
                        is_over: bool) -> str:
    """
    Return the full Ensemble ML block for ntfy / Telegram alerts.

    Example output:
      🤖 Ensemble ML (4,923 partidos históricos):
         RandomForest          68% UNDER
         GradientBoosting      71% UNDER
         LogisticRegression    65% UNDER
         → Consenso: 68.5% UNDER  ✅
    """
    result = predict_ensemble(book_line, projection, is_over)
    if result is None:
        return ""
    consensus_pct = round(result["consensus"] * 100, 1)
    side          = result["side"]
    conf_pct      = round(result["confidence"] * 100, 1)
    verdict_emoji = "✅" if conf_pct >= 60 else ("⚠️" if conf_pct >= 52 else "❌")

    model_order = ["RandomForest", "GradientBoosting", "LogisticRegression"]
    lines = [f"🤖 Ensemble ML ({_n_samples} partidos históricos):"]
    for mname in model_order:
        if mname in result:
            p      = result[mname]
            side_m = "UNDER" if p >= 0.5 else "OVER"
            pct    = p * 100 if side_m == "UNDER" else (1.0 - p) * 100
            lines.append(f"   {mname:22s} {pct:.0f}% {side_m}")
    lines.append(f"   → Consenso: {conf_pct}% {side}  {verdict_emoji}")
    return "\n".join(lines)


# Keep old name as alias for backward compatibility
def ml_alert_line(book_line: float, projection: float, is_over: bool) -> str:
    return ensemble_alert_line(book_line, projection, is_over)


def blend_prob(model_prob: float, ml_prob: "float | None",
               hist_rate: float = 0.526) -> float:
    """
    Final probability blend for Kelly stake sizing:
      model × 0.50 + ensemble_ML × 0.30 + historical × 0.20

    Falls back to 70/30 model/historical when ensemble is unavailable.
    This blend is ONLY used for Kelly fraction sizing (conservative).
    EV calculation always uses the raw model probability.
    """
    if ml_prob is None:
        return round(model_prob * 0.70 + hist_rate * 0.30, 4)
    return round(model_prob * 0.50 + ml_prob * 0.30 + hist_rate * 0.20, 4)


# ════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""

    if arg == "test":
        if load():
            r = predict_ensemble(8.5, 9.1, False)
            if r:
                print("Test prediction  (proj=9.1, line=8.5, UNDER pick):")
                for k, v in r.items():
                    print(f"  {k}: {v}")
                print()
                print(ensemble_alert_line(8.5, 9.1, False))
                print()
                # Test Pinnacle clamp
                raw = predict_under_prob(8.5, 9.1, False)
                clamped = predict_under_prob(8.5, 9.1, False, pinnacle_prob=0.50)
                print(f"Pinnacle clamp test:  raw={raw:.4f}  clamped_to_50%={clamped:.4f}")
                print(f"  (Δ raw-pinnacle={abs(raw-0.50):.2f} → "
                      f"{'clamped' if clamped != raw else 'within 15pp, unchanged'})")
            else:
                print("Ensemble returned None (below accuracy floor or no data)")
        else:
            print("No model.pkl found — run without args to train first")

    elif arg == "fetch":
        games = load_or_fetch_historical(force=True)
        print(f"\n✅ {len(games)} juegos históricos guardados en {HIST_CACHE}")

    else:
        ok = train(force=True, use_historical=True)
        if ok:
            print(f"\n✅ Ensemble guardado en {MODEL_FILE}")
            print(f"\n--- Test prediction ---")
            r = predict_ensemble(8.5, 9.1, False)
            if r:
                print(ensemble_alert_line(8.5, 9.1, False))
        else:
            print(
                f"❌ Entrenamiento no completado "
                f"({MIN_GAMES} partidos mínimo en {BACKTEST_CSV})"
            )
