"""Microservicio de Resenas.

Solo puede resenar quien posee el juego. Para comprobarlo, este servicio le
pregunta directamente al microservicio de Biblioteca (comunicacion servicio a
servicio, sin pasar por el gateway).
"""
import os
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from sqlalchemy import text

from db import make_engine, rows

app = Flask(__name__)
CORS(app)
engine = make_engine("reviews")

LIBRARY_URL = os.getenv("LIBRARY_URL", "http://127.0.0.1:5004").rstrip("/")
if not LIBRARY_URL.startswith("http"):
    LIBRARY_URL = "https://" + LIBRARY_URL
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", 30))


def init_db():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS reviews (
                user_id TEXT NOT NULL,
                game_id TEXT NOT NULL,
                recommended INTEGER NOT NULL,
                body TEXT NOT NULL,
                minutes_played INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, game_id)
            )
        """))


init_db()


def summary_of(reviews):
    total = len(reviews)
    positivas = sum(1 for r in reviews if r["recommended"])
    pct = round(positivas * 100 / total) if total else None
    if pct is None:
        etiqueta = "Sin reseñas"
    elif pct >= 80:
        etiqueta = "Muy positivas"
    elif pct >= 60:
        etiqueta = "Mayormente positivas"
    elif pct >= 40:
        etiqueta = "Variadas"
    else:
        etiqueta = "Negativas"
    return {"total": total, "positive": positivas, "positive_pct": pct, "label": etiqueta}


@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return jsonify({"status": "ok", "service": "reviews"})


@app.get("/reviews/<game_id>")
def by_game(game_id):
    with engine.connect() as conn:
        data = rows(conn.execute(text("SELECT * FROM reviews WHERE game_id = :g ORDER BY created_at DESC"),
                                 {"g": game_id}))
    for r in data:
        r["recommended"] = bool(r["recommended"])
    return jsonify({"game_id": game_id, "reviews": data, "summary": summary_of(data)})


@app.get("/summary")
def summary():
    """Resumen de varias resenas a la vez para pintar el catalogo en una sola llamada."""
    ids = [i for i in request.args.get("ids", "").split(",") if i]
    with engine.connect() as conn:
        data = rows(conn.execute(text("SELECT game_id, recommended FROM reviews")))
    result = {}
    for gid in ids or sorted({r["game_id"] for r in data}):
        result[gid] = summary_of([r for r in data if r["game_id"] == gid])
    return jsonify({"summary": result})


@app.post("/reviews/<game_id>")
def create(game_id):
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "").strip()
    body = (data.get("body") or "").strip()
    if not user_id or "recommended" not in data:
        return jsonify({"error": "Se requieren user_id y recommended"}), 400
    if len(body) < 5 or len(body) > 1000:
        return jsonify({"error": "La reseña debe tener entre 5 y 1000 caracteres"}), 400

    # Comunicacion directa con el microservicio de Biblioteca.
    try:
        lib = requests.get(f"{LIBRARY_URL}/library/{user_id}", timeout=HTTP_TIMEOUT)
        lib.raise_for_status()
    except requests.RequestException as e:
        return jsonify({"error": "No se pudo verificar la biblioteca del usuario",
                        "service": "library", "detail": str(e)}), 503
    juego = next((g for g in lib.json()["games"] if g["game_id"] == game_id), None)
    if not juego:
        return jsonify({"error": "Solo puedes reseñar juegos que están en tu biblioteca"}), 403

    review = {"u": user_id, "g": game_id, "r": 1 if data["recommended"] else 0, "b": body,
              "m": juego["minutes_played"], "t": datetime.now(timezone.utc).isoformat()}
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO reviews (user_id, game_id, recommended, body, minutes_played, created_at)
            VALUES (:u, :g, :r, :b, :m, :t)
            ON CONFLICT (user_id, game_id) DO UPDATE SET
                recommended = excluded.recommended, body = excluded.body,
                minutes_played = excluded.minutes_played, created_at = excluded.created_at
        """), review)
    return jsonify({"saved": True, "game_id": game_id, "user_id": user_id}), 201


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5005)))
