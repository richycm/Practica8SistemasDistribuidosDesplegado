"""Microservicio de Pagos (cartera virtual).

Maneja el saldo de cada usuario y todas sus transacciones. Los cargos y los
reembolsos son idempotentes: se identifican por order_id, de modo que si el
gateway reintenta una peticion nunca se cobra (ni se devuelve) dos veces.
"""
import os
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_cors import CORS
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from db import make_engine, one, rows

app = Flask(__name__)
CORS(app)
engine = make_engine("payments")

BONO_BIENVENIDA = 1500_00   # 1,500.00 MXN
DEPOSITO_MAXIMO = 5000_00   # por operacion


def now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS wallets (
                user_id TEXT PRIMARY KEY,
                balance_cents INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                order_id TEXT,
                kind TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                description TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (order_id, kind)
            )
        """))


init_db()


def record(conn, user_id, kind, amount, balance_after, description, order_id=None):
    tx = {"id": uuid.uuid4().hex[:12], "user_id": user_id, "order_id": order_id, "kind": kind,
          "amount_cents": amount, "balance_after": balance_after, "description": description,
          "created_at": now()}
    conn.execute(text("""
        INSERT INTO transactions (id, user_id, order_id, kind, amount_cents, balance_after, description, created_at)
        VALUES (:id, :user_id, :order_id, :kind, :amount_cents, :balance_after, :description, :created_at)
    """), tx)
    return tx


def get_or_create_wallet(conn, user_id):
    w = one(conn.execute(text("SELECT * FROM wallets WHERE user_id = :u"), {"u": user_id}))
    if w:
        return w
    res = conn.execute(text("""
        INSERT INTO wallets (user_id, balance_cents, created_at) VALUES (:u, :b, :t)
        ON CONFLICT (user_id) DO NOTHING
    """), {"u": user_id, "b": BONO_BIENVENIDA, "t": now()})
    if res.rowcount:
        record(conn, user_id, "BONO", BONO_BIENVENIDA, BONO_BIENVENIDA, "Bono de bienvenida")
    return one(conn.execute(text("SELECT * FROM wallets WHERE user_id = :u"), {"u": user_id}))


def balance(conn, user_id):
    return conn.execute(text("SELECT balance_cents FROM wallets WHERE user_id = :u"), {"u": user_id}).scalar()


@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return jsonify({"status": "ok", "service": "payments"})


@app.get("/wallets/<user_id>")
def get_wallet(user_id):
    with engine.begin() as conn:
        w = get_or_create_wallet(conn, user_id)
    return jsonify(w)


@app.get("/wallets/<user_id>/transactions")
def transactions(user_id):
    limit = min(int(request.args.get("limit", 30)), 100)
    with engine.connect() as conn:
        txs = rows(conn.execute(text("""
            SELECT * FROM transactions WHERE user_id = :u ORDER BY created_at DESC LIMIT :l
        """), {"u": user_id, "l": limit}))
    return jsonify({"transactions": txs})


@app.post("/wallets/<user_id>/deposit")
def deposit(user_id):
    amount = int((request.get_json(silent=True) or {}).get("amount_cents", 0))
    if amount <= 0 or amount > DEPOSITO_MAXIMO:
        return jsonify({"error": f"El monto debe estar entre 0.01 y {DEPOSITO_MAXIMO // 100} MXN"}), 400
    with engine.begin() as conn:
        get_or_create_wallet(conn, user_id)
        conn.execute(text("UPDATE wallets SET balance_cents = balance_cents + :a WHERE user_id = :u"),
                     {"a": amount, "u": user_id})
        new_balance = balance(conn, user_id)
        tx = record(conn, user_id, "DEPOSITO", amount, new_balance, "Recarga de saldo")
    return jsonify({"balance_cents": new_balance, "transaction": tx}), 201


@app.post("/charges")
def charge():
    """Paso de la Saga: cobrar una orden. Idempotente por order_id."""
    data = request.get_json(silent=True) or {}
    user_id, order_id = data.get("user_id"), data.get("order_id")
    amount = int(data.get("amount_cents", -1))
    if not user_id or not order_id or amount < 0:
        return jsonify({"error": "Se requieren user_id, order_id y amount_cents"}), 400

    if data.get("simulate_failure"):
        return jsonify({"error": "Falla simulada: el procesador de pagos no respondió"}), 503

    with engine.connect() as conn:
        previo = one(conn.execute(text("SELECT * FROM transactions WHERE order_id = :o AND kind = 'CARGO'"),
                                  {"o": order_id}))
    if previo:
        return jsonify({"charged": True, "idempotent_replay": True, "transaction": previo,
                        "balance_cents": previo["balance_after"]})

    try:
        with engine.begin() as conn:
            get_or_create_wallet(conn, user_id)
            # El WHERE hace la verificacion de saldo y el descuento en una sola operacion atomica.
            res = conn.execute(text("""
                UPDATE wallets SET balance_cents = balance_cents - :a
                WHERE user_id = :u AND balance_cents >= :a
            """), {"a": amount, "u": user_id})
            if res.rowcount == 0:
                actual = balance(conn, user_id)
                return jsonify({"error": "Saldo insuficiente", "balance_cents": actual,
                                "required_cents": amount}), 402
            new_balance = balance(conn, user_id)
            tx = record(conn, user_id, "CARGO", -amount, new_balance,
                        data.get("description", "Compra en la tienda"), order_id)
    except IntegrityError:
        # Otra peticion con el mismo order_id gano la carrera: se devuelve ese cargo.
        with engine.connect() as conn:
            tx = one(conn.execute(text("SELECT * FROM transactions WHERE order_id = :o AND kind = 'CARGO'"),
                                  {"o": order_id}))
        return jsonify({"charged": True, "idempotent_replay": True, "transaction": tx,
                        "balance_cents": tx["balance_after"]})

    return jsonify({"charged": True, "transaction": tx, "balance_cents": new_balance}), 201


@app.post("/refunds")
def refund():
    """Compensacion de la Saga: devuelve el cargo de una orden. Idempotente."""
    order_id = (request.get_json(silent=True) or {}).get("order_id")
    if not order_id:
        return jsonify({"error": "Falta order_id"}), 400

    try:
        with engine.begin() as conn:
            cargo = one(conn.execute(text("SELECT * FROM transactions WHERE order_id = :o AND kind = 'CARGO'"),
                                     {"o": order_id}))
            if not cargo:
                return jsonify({"refunded": False, "reason": "La orden no tenía cargo"}), 200
            previo = one(conn.execute(text("SELECT * FROM transactions WHERE order_id = :o AND kind = 'REEMBOLSO'"),
                                      {"o": order_id}))
            if previo:
                return jsonify({"refunded": True, "idempotent_replay": True, "transaction": previo})
            amount = -cargo["amount_cents"]
            conn.execute(text("UPDATE wallets SET balance_cents = balance_cents + :a WHERE user_id = :u"),
                         {"a": amount, "u": cargo["user_id"]})
            new_balance = balance(conn, cargo["user_id"])
            tx = record(conn, cargo["user_id"], "REEMBOLSO", amount, new_balance,
                        "Reembolso automático (compensación)", order_id)
    except IntegrityError:
        return jsonify({"refunded": True, "idempotent_replay": True})

    return jsonify({"refunded": True, "transaction": tx, "balance_cents": new_balance}), 201


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5003)))
