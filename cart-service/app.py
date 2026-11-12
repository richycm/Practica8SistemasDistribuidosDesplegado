"""Microservicio de Carrito.

Solo guarda que juegos tiene cada usuario en su carrito. No conoce precios:
el precio vigente siempre se consulta al Catalogo en el momento de pagar.
"""
import os
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_cors import CORS
from sqlalchemy import text

from db import make_engine, rows

app = Flask(__name__)
CORS(app)
engine = make_engine("cart")

MAX_ITEMS = 20


def init_db():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cart_items (
                user_id TEXT NOT NULL,
                game_id TEXT NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (user_id, game_id)
            )
        """))


init_db()


def items_of(conn, user_id):
    return rows(conn.execute(
        text("SELECT game_id, added_at FROM cart_items WHERE user_id = :u ORDER BY added_at"),
        {"u": user_id}))


@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return jsonify({"status": "ok", "service": "cart"})


@app.get("/cart/<user_id>")
def get_cart(user_id):
    with engine.connect() as conn:
        items = items_of(conn, user_id)
    return jsonify({"user_id": user_id, "items": items, "count": len(items)})


@app.post("/cart/<user_id>/items")
def add_item(user_id):
    game_id = (request.get_json(silent=True) or {}).get("game_id")
    if not game_id:
        return jsonify({"error": "Falta game_id"}), 400

    with engine.begin() as conn:
        if len(items_of(conn, user_id)) >= MAX_ITEMS:
            return jsonify({"error": f"El carrito admite máximo {MAX_ITEMS} juegos"}), 409
        result = conn.execute(text("""
            INSERT INTO cart_items (user_id, game_id, added_at) VALUES (:u, :g, :t)
            ON CONFLICT (user_id, game_id) DO NOTHING
        """), {"u": user_id, "g": game_id, "t": datetime.now(timezone.utc).isoformat()})
        items = items_of(conn, user_id)

    status = 201 if result.rowcount else 200
    return jsonify({"user_id": user_id, "items": items, "count": len(items),
                    "already_in_cart": not result.rowcount}), status


@app.delete("/cart/<user_id>/items/<game_id>")
def remove_item(user_id, game_id):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM cart_items WHERE user_id = :u AND game_id = :g"),
                     {"u": user_id, "g": game_id})
        items = items_of(conn, user_id)
    return jsonify({"user_id": user_id, "items": items, "count": len(items)})


@app.delete("/cart/<user_id>")
def clear_cart(user_id):
    """Vacia el carrito. Si se envian game_ids, solo quita esos (los que se compraron)."""
    game_ids = (request.get_json(silent=True) or {}).get("game_ids")
    with engine.begin() as conn:
        if game_ids:
            for g in game_ids:
                conn.execute(text("DELETE FROM cart_items WHERE user_id = :u AND game_id = :g"),
                             {"u": user_id, "g": g})
        else:
            conn.execute(text("DELETE FROM cart_items WHERE user_id = :u"), {"u": user_id})
        items = items_of(conn, user_id)
    return jsonify({"user_id": user_id, "items": items, "count": len(items)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5002)))
