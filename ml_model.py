#!/usr/bin/env python3
"""
BetBot Pro — ML Ensemble Model (Level 1A v2)

Two upgrades over v1:
  1. Probability Calibration — each model wrapped with CalibratedClassifierCV
     (isotonic regression, cv=5) so "70% UNDER" really wins ~70% of the time.
  2. Ensemble Voting — 3 models trained on the same data; final probability is
     a weighted average where each model's weight = its test-set accuracy.

     Model 1: RandomForest      (base weight 0.50)
     Model 2: GradientBoosting  (base weight 0.30)
     Model 3: LogisticRegression(base weight 0.20)
     Actual weights normalised by test accuracy after training.

Target : 1 = UNDER wins (actual ≤ book_line), 0 = OVER wins
Features: park_factor, book_line, projection, model_edge, our_pick_side

Usage (standalone):
  python3 ml_model.py          # train all 3 models, save model.pkl
  python3 ml_model.py test     # load and run a test prediction with breakdown

Integration in kelly_odds.py:
  from ml_model import predict_under_prob, ensemble_alert_line, load as ml_load
  prob = predict_under_prob(book_line, projection, is_over_pick)
  line = ensemble_alert_line(book_line, projection, is_over_pick)
"""
import os, sys, csv, pickle

MODEL_FILE   = "model.pkl"
BACKTEST_CSV = "backtest_log.csv"
MIN_GAMES    = 100          # minimum completed rows required to train
ACC_FLOOR    = 0.524        # below this the ensemble is not used (coin-flip territory)

# ── In-memory state ────────────────────────────────────────────────────────────
_ensemble: "dict | None" = None   # {name: {model, acc, weight}} loaded after train/load
_n_samples:  int   = 0
_trained:    bool  = False
_ensemble_acc: float = 0.0        # weighted average accuracy across all 3 models


# ── Data loading ───────────────────────────────────────────────────────────────

def _load_backtest_data() -> "tuple[list, list]":
    """
    Parse backtest_log.csv into (X, y).
    Skips PUSH rows. Returns ([], []) when file missing or too few rows.
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
                y         = 1 if actual <= book else 0   # 1 = UNDER wins
                park_f    = proj / book
                model_edge = abs(proj - book)
                is_over   = 1 if row.get("our_pick", "") == "OVER" else 0
                rows.append(([park_f, book, proj, model_edge, is_over], y))
    except Exception as e:
        print(f"  ⚠️  _load_backtest_data: {e}")
        return [], []
    return [r[0] for r in rows], [r[1] for r in rows]


# ── Training ───────────────────────────────────────────────────────────────────

def train(force: bool = False) -> bool:
    """
    Train 3 calibrated models on backtest_log.csv (≥ MIN_GAMES rows required).
    Each model is wrapped with CalibratedClassifierCV(method='isotonic', cv=5).
    Weights are set proportional to each model's test-set accuracy.
    Saves everything to model.pkl. Returns True on success.
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
        print("  ⚠️  scikit-learn not installed — ML ensemble disabled")
        print("       pip install scikit-learn")
        return False

    X, y = _load_backtest_data()
    n    = len(X)
    if n < MIN_GAMES:
        print(f"  ℹ️  Ensemble ML: {n} partidos < {MIN_GAMES} mínimo")
        return False

    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )

    # ── Base estimators ────────────────────────────────────────────────────────
    base_models = {
        "RandomForest": RandomForestClassifier(
            n_estimators=200, max_depth=6, min_samples_leaf=5,
            class_weight="balanced", random_state=42, n_jobs=-1,
        ),
        "GradientBoosting": GradientBoostingClassifier(
            n_estimators=150, max_depth=4, learning_rate=0.05,
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

    # ── Calibrate + evaluate ───────────────────────────────────────────────────
    trained_models = {}
    acc_total      = 0.0
    print(f"  🤖 Entrenando ensemble ({n} partidos)...")
    for name, base in base_models.items():
        # Calibrate with isotonic regression for proper probability estimates
        cal = CalibratedClassifierCV(base, cv=5, method="isotonic")
        cal.fit(X_tr, y_tr)
        preds  = cal.predict(X_te)
        probas = cal.predict_proba(X_te)
        acc    = accuracy_score(y_te, preds)
        # Brier score: lower is better (0 = perfect), useful for calibration quality
        try:
            classes = list(cal.classes_)
            under_i = classes.index(1) if 1 in classes else 1
            brier   = brier_score_loss(y_te, [p[under_i] for p in probas])
        except Exception:
            brier   = 0.25  # neutral
        print(f"    {name:22s} acc={acc:.1%}  brier={brier:.3f}"
              f"  {'✅' if acc >= ACC_FLOOR else '⚠️'}")
        trained_models[name] = {"model": cal, "acc": float(acc), "brier": float(brier)}
        acc_total += acc

    # ── Compute accuracy-proportional weights ──────────────────────────────────
    for name in trained_models:
        trained_models[name]["weight"] = (
            trained_models[name]["acc"] / acc_total if acc_total > 0 else 1/3
        )

    # Weighted ensemble accuracy (for threshold check)
    w_acc = sum(m["acc"] * m["weight"] for m in trained_models.values())
    print(f"  🎯 Ensemble — precisión ponderada: {w_acc:.1%}")

    payload = {
        "models":   trained_models,
        "n_samples": n,
        "ensemble_acc": float(w_acc),
    }
    with open(MODEL_FILE, "wb") as f:
        pickle.dump(payload, f)

    _ensemble     = trained_models
    _n_samples    = n
    _trained      = True
    _ensemble_acc = w_acc
    return True


# ── Loading ────────────────────────────────────────────────────────────────────

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
        # Support old single-model format (upgrade path)
        if "models" in saved:
            _ensemble     = saved["models"]
            _n_samples    = saved.get("n_samples", 0)
            _ensemble_acc = saved.get("ensemble_acc", 0.0)
        else:
            # Legacy v1 model — wrap in ensemble dict as RF only
            _ensemble = {"RandomForest": {
                "model":  saved["model"],
                "acc":    saved.get("accuracy", 0.0),
                "weight": 1.0,
            }}
            _n_samples    = saved.get("n_samples", 0)
            _ensemble_acc = saved.get("accuracy", 0.0)
        _trained = True
        print(f"  🤖 Ensemble cargado — {_n_samples} partidos, "
              f"acc ponderada {_ensemble_acc:.1%} "
              f"({len(_ensemble)} modelo(s))")
        return True
    except Exception as e:
        print(f"  ⚠️  ML load error: {e}")
        return False


# ── Prediction ─────────────────────────────────────────────────────────────────

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
        "consensus":          0.685,   # weighted average
        "side":               "UNDER", # winning side
        "confidence":         0.685,   # probability of the winning side
      }
    or None if ensemble unavailable.
    """
    global _ensemble, _trained
    if not _trained:
        if not load():
            return None
    if _ensemble is None or _ensemble_acc < ACC_FLOOR:
        return None

    x = [_feature_vector(book_line, projection, is_over_pick)]
    per_model = {}
    weighted_sum  = 0.0
    weight_total  = 0.0

    for name, info in _ensemble.items():
        try:
            proba    = info["model"].predict_proba(x)[0]
            ui       = _under_idx(info["model"])
            under_p  = float(proba[ui])
            w        = float(info.get("weight", 1.0))
            per_model[name] = round(under_p, 4)
            weighted_sum  += under_p * w
            weight_total  += w
        except Exception as e:
            print(f"  ⚠️  Ensemble predict [{name}]: {e}")

    if not per_model or weight_total == 0:
        return None

    consensus  = round(weighted_sum / weight_total, 4)
    side       = "UNDER" if consensus >= 0.5 else "OVER"
    confidence = consensus if side == "UNDER" else (1.0 - consensus)

    return {**per_model, "consensus": consensus,
            "side": side, "confidence": round(confidence, 4)}


def predict_under_prob(
    book_line: float,
    projection: float,
    is_over_pick: bool,
) -> "float | None":
    """
    Return weighted-ensemble probability (0–1) that UNDER wins.
    Drop-in replacement for the old single-model function.
    Returns None when ensemble unavailable or below accuracy floor.
    """
    result = predict_ensemble(book_line, projection, is_over_pick)
    return result["consensus"] if result else None


# ── Alert formatting ───────────────────────────────────────────────────────────

def ensemble_alert_line(book_line: float, projection: float,
                         is_over: bool) -> str:
    """
    Return the full Ensemble ML block for inclusion in ntfy alerts.

    Example output:
      🤖 Ensemble ML:
         RandomForest:        68%
         GradientBoosting:    71%
         LogisticRegression:  65%
         → Consenso: 68.5% UNDER  ✅
         Basado en 312 partidos históricos
    """
    result = predict_ensemble(book_line, projection, is_over)
    if result is None:
        return ""
    consensus_pct = round(result["consensus"] * 100, 1)
    side          = result["side"]
    conf_pct      = round(result["confidence"] * 100, 1)
    verdict_emoji = "✅" if conf_pct >= 60 else ("⚠️" if conf_pct >= 52 else "❌")

    model_order = ["RandomForest", "GradientBoosting", "LogisticRegression"]
    lines = ["🤖 Ensemble ML:"]
    for mname in model_order:
        if mname in result:
            p    = result[mname]
            side_m = "UNDER" if p >= 0.5 else "OVER"
            pct  = p * 100 if side_m == "UNDER" else (1.0 - p) * 100
            lines.append(f"   {mname:22s} {pct:.0f}% {side_m}")
    lines.append(f"   → Consenso: {conf_pct}% {side}  {verdict_emoji}")
    lines.append(f"   Basado en {_n_samples} partidos históricos")
    return "\n".join(lines)


# Keep old name as alias for backward compatibility
def ml_alert_line(book_line: float, projection: float, is_over: bool) -> str:
    return ensemble_alert_line(book_line, projection, is_over)


def blend_prob(model_prob: float, ml_prob: "float | None",
               hist_rate: float = 0.526) -> float:
    """
    Final probability blend:
      model × 0.50 + ensemble_ML × 0.30 + historical × 0.20
    Falls back to 70/30 model/historical if ensemble unavailable.
    """
    if ml_prob is None:
        return round(model_prob * 0.70 + hist_rate * 0.30, 4)
    return round(model_prob * 0.50 + ml_prob * 0.30 + hist_rate * 0.20, 4)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        if load():
            r = predict_ensemble(8.5, 9.1, False)
            if r:
                print("Test prediction (proj=9.1, line=8.5, UNDER pick):")
                for k, v in r.items():
                    print(f"  {k}: {v}")
                print()
                print(ensemble_alert_line(8.5, 9.1, False))
            else:
                print("Ensemble returned None (below accuracy floor or no data)")
        else:
            print("No model.pkl found — run without args to train first")
    else:
        ok = train(force=True)
        if ok:
            print(f"\n✅ Ensemble guardado en {MODEL_FILE}")
            print("\n--- Test prediction ---")
            r = predict_ensemble(8.5, 9.1, False)
            if r:
                print(ensemble_alert_line(8.5, 9.1, False))
        else:
            print(f"❌ Entrenamiento no completado ({MIN_GAMES} partidos mínimo en {BACKTEST_CSV})")
