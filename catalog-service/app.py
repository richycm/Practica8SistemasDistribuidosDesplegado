"""Microservicio de Catalogo.

Es la unica fuente de verdad de los juegos: titulo, genero, precio y descuento.
Los demas servicios solo guardan el id del juego.

Los juegos son reales: games_seed.json se genera con actualizar_catalogo.py a partir de la API publica
de la tienda de Steam (precios en MXN, descripciones en espanol y portada oficial).
"""
import json
import os
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS
from sqlalchemy import inspect, text

from db import make_engine, one, rows

app = Flask(__name__)
CORS(app)
engine = make_engine("catalog")

SEED = json.loads(Path(__file__).with_name("games_seed.json").read_text(encoding="utf8"))
COLUMNAS = ["id", "steam_appid", "title", "developer", "genre", "price_cents", "discount_pct",
            "release_year", "color1", "color2", "tags", "description", "header_image"]


def init_db():
    # Si la tabla viene de una version anterior (sin portada de Steam), se recrea: es solo datos semilla.
    if inspect(engine).has_table("games"):
        actuales = {c["name"] for c in inspect(engine).get_columns("games")}
        if "header_image" not in actuales:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE games"))
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS games (
                id TEXT PRIMARY KEY,
                steam_appid INTEGER NOT NULL,
                title TEXT NOT NULL,
                developer TEXT NOT NULL,
                genre TEXT NOT NULL,
                price_cents INTEGER NOT NULL,
                discount_pct INTEGER NOT NULL DEFAULT 0,
                release_year INTEGER NOT NULL,
                color1 TEXT NOT NULL,
                color2 TEXT NOT NULL,
                tags TEXT NOT NULL,
                description TEXT NOT NULL,
                header_image TEXT NOT NULL
            )
        """))
        # Upsert: si se regenera el JSON con precios nuevos, el catalogo se actualiza al reiniciar.
        for g in SEED["games"]:
            datos = {**g, "tags": ",".join(g["tags"])}
            conn.execute(text(f"""
                INSERT INTO games ({", ".join(COLUMNAS)})
                VALUES ({", ".join(":" + c for c in COLUMNAS)})
                ON CONFLICT (id) DO UPDATE SET
                    {", ".join(f"{c} = excluded.{c}" for c in COLUMNAS if c != "id")}
            """), {c: datos[c] for c in COLUMNAS})


def to_json(g):
    g = dict(g)
    g["tags"] = [t for t in g["tags"].split(",") if t]
    g["final_price_cents"] = g["price_cents"] * (100 - g["discount_pct"]) // 100
    g["steam_url"] = f"https://store.steampowered.com/app/{g['steam_appid']}/"
    return g


init_db()


@app.get("/health")
def health():
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM games")).scalar()
    return jsonify({"status": "ok", "service": "catalog", "games": total})


@app.get("/games")
def list_games():
    q = request.args.get("q", "").strip().lower()
    genre = request.args.get("genre", "").strip()
    ids = [i for i in request.args.get("ids", "").split(",") if i]

    with engine.connect() as conn:
        games = [to_json(g) for g in rows(conn.execute(text("SELECT * FROM games ORDER BY release_year DESC, title")))]

    if ids:
        games = [g for g in games if g["id"] in ids]
    if genre:
        games = [g for g in games if g["genre"] == genre]
    if q:
        games = [g for g in games if q in g["title"].lower() or q in g["developer"].lower()
                 or any(q in t.lower() for t in g["tags"])]
    return jsonify({"games": games, "count": len(games), "source": SEED["source"],
                    "fetched_at": SEED["fetched_at"]})


@app.get("/games/<game_id>")
def get_game(game_id):
    with engine.connect() as conn:
        g = one(conn.execute(text("SELECT * FROM games WHERE id = :id"), {"id": game_id}))
    if not g:
        return jsonify({"error": "Juego no encontrado"}), 404
    return jsonify(to_json(g))


@app.get("/genres")
def genres():
    with engine.connect() as conn:
        data = rows(conn.execute(text("SELECT genre, COUNT(*) AS total FROM games GROUP BY genre ORDER BY genre")))
    return jsonify({"genres": data})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5001)))
