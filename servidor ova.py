# servidor_ova.py — corre en Railway. Reemplaza el escaneo automático de
# kelly_odds.py (que dependía de The Odds API) por un reloj propio que solo
# usa datos gratis (MLB Stats API) y guarda todo en una base local.
# Sin Telegram: OVA lee estos resultados directo por HTTP cuando la abres.

import json
import os
import sqlite3
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, request
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler

from ova_formula import juegos_de_hoy, lineup_confirmado, analizar_juego

# ---------- configuración ----------
# Railway: monta un Volume en /data para que esto NO se borre en cada deploy.
# Settings del servicio -> Volumes -> Add Volume -> mount path: /data
DB_PATH = os.environ.get("DB_PATH", "/data/ova.db")
INTERVALO_MINUTOS = int(os.environ.get("INTERVALO_MINUTOS", "20"))
ORIGEN_PERMITIDO = os.environ.get(
    "ORIGEN_PERMITIDO", "https://osvaldoalvarez0113-source.github.io"
)

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": ORIGEN_PERMITIDO}})

_lock = threading.Lock()


def db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS analisis (
            gamePk INTEGER PRIMARY KEY,
            fecha TEXT NOT NULL,
            datos TEXT NOT NULL,
            actualizado TEXT NOT NULL
        )"""
    )
    return conn


def guardar(fecha, resultado):
    with _lock:
        conn = db()
        conn.execute(
            "INSERT INTO analisis (gamePk, fecha, datos, actualizado) VALUES (?,?,?,?) "
            "ON CONFLICT(gamePk) DO UPDATE SET datos=excluded.datos, actualizado=excluded.actualizado",
            (resultado["gamePk"], fecha, json.dumps(resultado, ensure_ascii=False),
             datetime.utcnow().isoformat()),
        )
        conn.commit()
        conn.close()


def leer_fecha(fecha):
    with _lock:
        conn = db()
        filas = conn.execute(
            "SELECT datos FROM analisis WHERE fecha=? ORDER BY gamePk", (fecha,)
        ).fetchall()
        conn.close()
    return [json.loads(f[0]) for f in filas]


# ---------- el trabajo que corre solo ----------

def correr_ciclo():
    fecha = datetime.now().strftime("%Y-%m-%d")
    season = datetime.now().year
    try:
        juegos = juegos_de_hoy(fecha)
    except Exception as e:
        print(f"[{datetime.now()}] Error jalando calendario: {e}")
        return

    procesados = 0
    for juego in juegos:
        if not lineup_confirmado(juego):
            continue
        try:
            resultado = analizar_juego(juego, season)
            if resultado.get("estado") == "listo":
                resultado["fecha"] = fecha
                guardar(fecha, resultado)
                procesados += 1
        except Exception as e:
            print(f"[{datetime.now()}] Error en juego {juego.get('gamePk')}: {e}")

    print(f"[{datetime.now()}] Ciclo listo — {procesados} juegos actualizados de {len(juegos)}.")


scheduler = BackgroundScheduler()
scheduler.add_job(correr_ciclo, "interval", minutes=INTERVALO_MINUTOS, next_run_time=datetime.now())
scheduler.start()


# ---------- API que consume OVA ----------

@app.route("/api/analisis-hoy")
def analisis_hoy():
    fecha = request.args.get("fecha") or datetime.now().strftime("%Y-%m-%d")
    return jsonify({"fecha": fecha, "juegos": leer_fecha(fecha)})


@app.route("/api/estado")
def estado():
    return jsonify({
        "ok": True,
        "hora_servidor": datetime.utcnow().isoformat(),
        "intervalo_minutos": INTERVALO_MINUTOS,
    })


@app.route("/api/forzar")
def forzar():
    """Dispara un ciclo ya mismo, sin esperar al reloj — útil para probar."""
    threading.Thread(target=correr_ciclo).start()
    return jsonify({"ok": True, "mensaje": "Ciclo disparado en segundo plano."})


if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=puerto)
