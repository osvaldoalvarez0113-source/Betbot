#!/usr/bin/env python3
"""
BetBot Pro — ML Model (Level 1A)
Trains a RandomForestClassifier on backtest_log.csv.

Target: 1 = UNDER wins (actual ≤ book_line), 0 = OVER wins
Features: park_factor, book_line, projection, model_edge, our_pick_side

Usage (standalone):
  python3 ml_model.py        # train and save model.pkl
  python3 ml_model.py test   # load model and run a test prediction

Integration in kelly_odds.py:
  from ml_model import predict_under_prob, ml_alert_line, load as ml_load
  ml_prob = predict_under_prob(book_line, projection, is_over_pick)
"""
import os, sys, csv, pickle

MODEL_FILE   = "model.pkl"
BACKTEST_CSV = "backtest_log.csv"
MIN_GAMES    = 100   # minimum rows needed to train

_ml_model     = None
_ml_trained   = False
_ml_n_samples = 0
_ml_accuracy  = 0.0


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_backtest_data():
    """
    Parse backtest_log.csv into (X, y) arrays.
    Skips PUSH rows. Returns ([], []) if file missing or too few rows.
    """
    rows = []
    if not os.path.isfile(BACKTEST_CSV):
        return [], []
    try:
        with open(BACKTEST_CSV, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                result   = row.get("result", "")
                if result not in ("WIN", "LOSS"):
                    continue
                our_pick = row.get("our_pick", "")
                try:
                    actual = float(row.get("actual", 0))
                    book   = float(row.get("book_line", 8.5))
                    proj   = float(row.get("projection", book))
                except (ValueError, TypeError):
                    continue
                if book <= 0:
                    continue
                # Target: 1 = UNDER wins
                y = 1 if actual <= book else 0
                # Feature vector
                park_f    = proj / book
                model_edge = abs(proj - book)
                is_over   = 1 if our_pick == "OVER" else 0
                x = [park_f, book, proj, model_edge, is_over]
                rows.append((x, y))
    except Exception as e:
        print(f"  ⚠️  _load_backtest_data: {e}")
        return [], []
    X = [r[0] for r in rows]
    y = [r[1] for r in rows]
    return X, y


# ── Training ──────────────────────────────────────────────────────────────────

def train(force: bool = False) -> bool:
    """
    Train RandomForestClassifier on backtest_log.csv.
    Requires ≥ MIN_GAMES (100) completed rows.
    Saves to model.pkl. Returns True on success.
    """
    global _ml_model, _ml_trained, _ml_n_samples, _ml_accuracy
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import accuracy_score
    except ImportError:
        print("  ⚠️  scikit-learn not installed — ML model disabled")
        print("       To install: pip install scikit-learn")
        return False

    X, y = _load_backtest_data()
    n = len(X)
    if n < MIN_GAMES:
        print(f"  ℹ️  ML: {n} partidos < {MIN_GAMES} mínimo — sin entrenamiento")
        return False

    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )
    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=6,
        min_samples_leaf=5,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    clf.fit(X_tr, y_tr)
    acc = accuracy_score(y_te, clf.predict(X_te))
    print(f"  🤖 ML entrenado — {n} partidos | "
          f"Precisión test: {acc:.1%} | "
          f"{'✅ útil' if acc >= 0.524 else '⚠️ por debajo de umbral'}")

    payload = {"model": clf, "n_samples": n, "accuracy": float(acc)}
    with open(MODEL_FILE, "wb") as f:
        pickle.dump(payload, f)

    _ml_model     = clf
    _ml_trained   = True
    _ml_n_samples = n
    _ml_accuracy  = acc
    return True


# ── Loading ───────────────────────────────────────────────────────────────────

def load() -> bool:
    """Load model.pkl into memory. Returns True if successful."""
    global _ml_model, _ml_trained, _ml_n_samples, _ml_accuracy
    if _ml_trained:
        return True
    if not os.path.isfile(MODEL_FILE):
        return False
    try:
        with open(MODEL_FILE, "rb") as f:
            saved = pickle.load(f)
        _ml_model     = saved["model"]
        _ml_trained   = True
        _ml_n_samples = saved.get("n_samples", 0)
        _ml_accuracy  = saved.get("accuracy", 0.0)
        print(f"  🤖 ML model cargado — {_ml_n_samples} partidos, "
              f"acc {_ml_accuracy:.1%}")
        return True
    except Exception as e:
        print(f"  ⚠️  ML load error: {e}")
        return False


# ── Prediction ────────────────────────────────────────────────────────────────

def predict_under_prob(
    book_line: float,
    projection: float,
    is_over_pick: bool,
) -> "float | None":
    """
    Return probability (0.0–1.0) that UNDER wins for this matchup.
    Returns None if model unavailable.
    """
    global _ml_model, _ml_trained
    if not _ml_trained:
        if not load():
            return None
    if _ml_model is None or _ml_accuracy < 0.524:
        return None   # model not beating random — don't use
    try:
        park_f     = projection / book_line if book_line > 0 else 1.0
        model_edge = abs(projection - book_line)
        is_over    = 1 if is_over_pick else 0
        x          = [[park_f, book_line, projection, model_edge, is_over]]
        proba      = _ml_model.predict_proba(x)[0]
        classes    = list(_ml_model.classes_)
        under_idx  = classes.index(1) if 1 in classes else 1
        return round(float(proba[under_idx]), 4)
    except Exception as e:
        print(f"  ⚠️  ML predict error: {e}")
        return None


def ml_alert_line(book_line: float, projection: float, is_over: bool) -> str:
    """Return formatted 🤖 ML Score line for inclusion in alerts. Empty if unavailable."""
    p = predict_under_prob(book_line, projection, is_over)
    if p is None:
        return ""
    side = "UNDER" if p >= 0.5 else "OVER"
    conf = p if p >= 0.5 else (1.0 - p)
    return (f"🤖 ML Score: {conf:.0%} {side}\n"
            f"   Basado en {_ml_n_samples} partidos históricos")


def blend_prob(model_prob: float, ml_prob: float,
               hist_rate: float = 0.526) -> float:
    """
    Final probability blend (Improvement 4 formula extended with ML):
      model × 0.50 + ml × 0.30 + historical × 0.20
    Falls back to 70/30 model/historical if ml_prob is None.
    """
    if ml_prob is None:
        return round(model_prob * 0.70 + hist_rate * 0.30, 4)
    return round(model_prob * 0.50 + ml_prob * 0.30 + hist_rate * 0.20, 4)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        if load():
            p = predict_under_prob(8.5, 9.1, False)
            print(f"Test (proj=9.1, line=8.5, UNDER pick): UNDER prob = {p}")
        else:
            print("No model.pkl found — run without args to train first")
    else:
        ok = train(force=True)
        if ok:
            print(f"✅ Modelo guardado en {MODEL_FILE}")
        else:
            print(f"❌ Entrenamiento no completado ({MIN_GAMES} partidos mínimo)")
