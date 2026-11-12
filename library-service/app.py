"""Microservicio de Biblioteca.

Registra que juegos posee cada usuario y su tiempo de juego. Es el ultimo paso
de la Saga de compra: si falla, el gateway compensa reembolsando el pago.
"""
import os
import random
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_cors import CORS
from sqlalchemy import text

from db import make_engine, one, rows

app = Flask(__name__)
CORS(app)
engine = make_engine("library")


def now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS owned_games (
                user_id TEXT NOT NULL,
                game_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                minutes_played INTEGER NOT NULL DEFAULT 0,
                last_played TEXT,
                PRIMARY KEY (user_id, game_id)
            )
        """))


init_db()


@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return jsonify({"status": "ok", "service": "library"})


@app.get("/library/<user_id>")
def get_library(user_id):
    with engine.connect() as conn:
        games = rows(conn.execute(text("""
            SELECT * FROM owned_games WHERE user_id = :u ORDER BY acquired_at DESC
        """), {"u": user_id}))
    return jsonify({"user_id": user_id, "games": games, "count": len(games)})


@app.get("/library/<user_id>/owns/<game_id>")
def owns(user_id, game_id):
    with engine.connect() as conn:
        g = one(conn.execute(text("SELECT game_id FROM owned_games WHERE user_id = :u AND game_id = :g"),
                             {"u": user_id, "g": game_id}))
    return jsonify({"user_id": user_id, "game_id": game_id, "owns": g is not None})


@app.post("/library/<user_id>/grant")
def grant(user_id):
    """Paso de la Saga: entregar los juegos comprados. Todo o nada."""
    data = request.get_json(silent=True) or {}
    order_id, game_ids = data.get("order_id"), data.get("game_ids") or []
    if not order_id or not game_ids:
        return jsonify({"error": "Se requieren order_id y game_ids"}), 400

    if data.get("simulate_failure"):
        return jsonify({"error": "Falla simulada: la biblioteca no pudo registrar los juegos"}), 503

    with engine.begin() as conn:
        ya = rows(conn.execute(text("SELECT game_id, order_id FROM owned_games WHERE user_id = :u"),
                               {"u": user_id}))
        mismos = [g["game_id"] for g in ya if g["order_id"] == order_id]
        if mismos:
            # Reintento de la misma orden: ya se habia entregado.
            return jsonify({"granted": mismos, "idempotent_replay": True})
        repetidos = [g["game_id"] for g in ya if g["game_id"] in game_ids]
        if repetidos:
            return jsonify({"error": "El usuario ya posee algunos juegos", "owned": repetidos}), 409
        for g in game_ids:
            conn.execute(text("""
                INSERT INTO owned_games (user_id, game_id, order_id, acquired_at)
                VALUES (:u, :g, :o, :t)
            """), {"u": user_id, "g": g, "o": order_id, "t": now()})
    return jsonify({"granted": game_ids}), 201


@app.post("/library/revoke")
def revoke():
    """Compensacion: quita los juegos de una orden (por si se alcanzaron a escribir)."""
    order_id = (request.get_json(silent=True) or {}).get("order_id")
    if not order_id:
        return jsonify({"error": "Falta order_id"}), 400
    with engine.begin() as conn:
        res = conn.execute(text("DELETE FROM owned_games WHERE order_id = :o"), {"o": order_id})
    return jsonify({"revoked": res.rowcount})


@app.post("/library/<user_id>/play/<game_id>")
def play(user_id, game_id):
    """Simula una sesion de juego: suma entre 15 y 120 minutos."""
    minutes = random.randint(15, 120)
    with engine.begin() as conn:
        res = conn.execute(text("""
            UPDATE owned_games SET minutes_played = minutes_played + :m, last_played = :t
            WHERE user_id = :u AND game_id = :g
        """), {"m": minutes, "t": now(), "u": user_id, "g": game_id})
        if res.rowcount == 0:
            return jsonify({"error": "No tienes este juego"}), 404
        g = one(conn.execute(text("SELECT * FROM owned_games WHERE user_id = :u AND game_id = :g"),
                             {"u": user_id, "g": game_id}))
    return jsonify({"session_minutes": minutes, "game": g})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5004)))
