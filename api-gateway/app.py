"""API Gateway de PixelVault.

Es el unico punto de entrada para el frontend. Se encarga de:
- Enrutar cada peticion al microservicio que corresponde.
- Combinar respuestas de varios servicios (por ejemplo, juegos + resenas).
- Orquestar la Saga de compra: cobrar -> entregar juegos -> vaciar carrito,
  con compensaciones (reembolso) si un paso falla.
- Despertar a los servicios (en el plan gratuito de Render se duermen).
"""
import os
import re
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)


def service_url(env, default):
    url = os.getenv(env, default).strip().rstrip("/")
    return url if url.startswith("http") else "https://" + url


SERVICES = {
    "catalog":  service_url("CATALOG_URL",  "http://127.0.0.1:5001"),
    "cart":     service_url("CART_URL",     "http://127.0.0.1:5002"),
    "payments": service_url("PAYMENTS_URL", "http://127.0.0.1:5003"),
    "library":  service_url("LIBRARY_URL",  "http://127.0.0.1:5004"),
    "reviews":  service_url("REVIEWS_URL",  "http://127.0.0.1:5005"),
}
NOMBRES = {"catalog": "Catálogo", "cart": "Carrito", "payments": "Pagos",
           "library": "Biblioteca", "reviews": "Reseñas"}

CONNECT_TIMEOUT = float(os.getenv("CONNECT_TIMEOUT", 10))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", 30))
USER_RE = re.compile(r"^[a-z0-9_-]{3,20}$")

pool = ThreadPoolExecutor(max_workers=10)
sagas = deque(maxlen=25)
sagas_lock = threading.Lock()


# ── Comunicacion con los microservicios ─────────────────────────────────────

class ServiceError(Exception):
    def __init__(self, service, status, body):
        super().__init__(body.get("error", "error"))
        self.service, self.status, self.body = service, status, body


def call(service, method, path, **kwargs):
    url = SERVICES[service] + path
    try:
        r = requests.request(method, url, timeout=(CONNECT_TIMEOUT, HTTP_TIMEOUT), **kwargs)
    except requests.RequestException as e:
        raise ServiceError(service, 503, {"error": f"El servicio de {NOMBRES[service]} no responde",
                                          "detail": type(e).__name__})
    try:
        body = r.json()
    except ValueError:
        body = {"error": f"Respuesta inválida de {NOMBRES[service]} (HTTP {r.status_code})"}
    if r.status_code >= 400:
        raise ServiceError(service, r.status_code, body)
    return body


@app.errorhandler(ServiceError)
def on_service_error(e):
    # Los errores de negocio (4xx) se pasan tal cual; las caidas se reportan como 503.
    status = e.status if 400 <= e.status < 500 else 503
    return jsonify({**e.body, "service": e.service}), status


def check_user(user_id):
    user_id = user_id.lower()
    if not USER_RE.match(user_id):
        raise ServiceError("gateway", 400, {"error": "Usuario inválido: 3 a 20 caracteres (a-z, 0-9, - y _)"})
    return user_id


def games_by_id(ids):
    if not ids:
        return {}
    data = call("catalog", "GET", "/games", params={"ids": ",".join(ids)})
    return {g["id"]: g for g in data["games"]}


# ── Estado de los servicios ─────────────────────────────────────────────────

def ping(name):
    start = time.perf_counter()
    try:
        r = requests.get(SERVICES[name] + "/health", timeout=(CONNECT_TIMEOUT, HTTP_TIMEOUT))
        ok = r.status_code == 200 and r.json().get("status") == "ok"
        status = "ok" if ok else "error"
    except (requests.RequestException, ValueError):
        status = "down"
    return {"service": name, "name": NOMBRES[name], "status": status,
            "latency_ms": round((time.perf_counter() - start) * 1000)}


def status_all():
    results = list(pool.map(ping, SERVICES))
    healthy = all(r["status"] == "ok" for r in results)
    return {"status": "ok" if healthy else "degraded", "gateway": "ok", "services": results,
            "checked_at": datetime.now(timezone.utc).isoformat()}


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "gateway"})


@app.get("/api/status")
def api_status():
    """Consulta /health de todos los servicios en paralelo. Tambien sirve para despertarlos."""
    return jsonify(status_all())


# ── Catalogo y resenas ──────────────────────────────────────────────────────

@app.get("/api/games")
def api_games():
    data = call("catalog", "GET", "/games", params=request.args)
    try:
        ids = ",".join(g["id"] for g in data["games"])
        summary = call("reviews", "GET", "/summary", params={"ids": ids})["summary"]
        degraded = False
    except ServiceError:
        # Degradacion elegante: si Resenas se cae, la tienda sigue funcionando.
        summary, degraded = {}, True
    for g in data["games"]:
        g["reviews"] = summary.get(g["id"])
    data["reviews_unavailable"] = degraded
    return jsonify(data)


@app.get("/api/genres")
def api_genres():
    return jsonify(call("catalog", "GET", "/genres"))


@app.get("/api/games/<game_id>")
def api_game(game_id):
    game = call("catalog", "GET", f"/games/{game_id}")
    try:
        game["reviews"] = call("reviews", "GET", f"/reviews/{game_id}")
    except ServiceError:
        game["reviews"] = None
    return jsonify(game)


@app.post("/api/games/<game_id>/reviews")
def api_review(game_id):
    data = request.get_json(silent=True) or {}
    data["user_id"] = check_user(data.get("user_id", ""))
    return jsonify(call("reviews", "POST", f"/reviews/{game_id}", json=data)), 201


# ── Usuario: perfil, carrito, biblioteca y cartera ──────────────────────────

@app.get("/api/users/<user_id>/profile")
def api_profile(user_id):
    user_id = check_user(user_id)
    wallet = pool.submit(call, "payments", "GET", f"/wallets/{user_id}")
    library = pool.submit(call, "library", "GET", f"/library/{user_id}")
    cart = pool.submit(call, "cart", "GET", f"/cart/{user_id}")
    return jsonify({"user_id": user_id,
                    "balance_cents": wallet.result()["balance_cents"],
                    "owned": [g["game_id"] for g in library.result()["games"]],
                    "cart": [i["game_id"] for i in cart.result()["items"]]})


def cart_view(user_id, cart):
    games = games_by_id([i["game_id"] for i in cart["items"]])
    items = [{**games[i["game_id"]], "added_at": i["added_at"]} for i in cart["items"] if i["game_id"] in games]
    return {"user_id": user_id, "items": items, "count": len(items),
            "subtotal_cents": sum(g["price_cents"] for g in items),
            "total_cents": sum(g["final_price_cents"] for g in items)}


@app.get("/api/users/<user_id>/cart")
def api_cart(user_id):
    user_id = check_user(user_id)
    return jsonify(cart_view(user_id, call("cart", "GET", f"/cart/{user_id}")))


@app.post("/api/users/<user_id>/cart")
def api_cart_add(user_id):
    user_id = check_user(user_id)
    game_id = (request.get_json(silent=True) or {}).get("game_id", "")
    call("catalog", "GET", f"/games/{game_id}")  # 404 si el juego no existe
    if call("library", "GET", f"/library/{user_id}/owns/{game_id}")["owns"]:
        return jsonify({"error": "Ya tienes este juego en tu biblioteca"}), 409
    cart = call("cart", "POST", f"/cart/{user_id}/items", json={"game_id": game_id})
    return jsonify(cart_view(user_id, cart)), 201


@app.delete("/api/users/<user_id>/cart/<game_id>")
def api_cart_remove(user_id, game_id):
    user_id = check_user(user_id)
    return jsonify(cart_view(user_id, call("cart", "DELETE", f"/cart/{user_id}/items/{game_id}")))


@app.get("/api/users/<user_id>/library")
def api_library(user_id):
    user_id = check_user(user_id)
    lib = call("library", "GET", f"/library/{user_id}")
    games = games_by_id([g["game_id"] for g in lib["games"]])
    lib["games"] = [{**games.get(g["game_id"], {"title": g["game_id"]}), **g} for g in lib["games"]]
    lib["total_minutes"] = sum(g["minutes_played"] for g in lib["games"])
    return jsonify(lib)


@app.post("/api/users/<user_id>/library/<game_id>/play")
def api_play(user_id, game_id):
    user_id = check_user(user_id)
    return jsonify(call("library", "POST", f"/library/{user_id}/play/{game_id}"))


@app.get("/api/users/<user_id>/wallet")
def api_wallet(user_id):
    user_id = check_user(user_id)
    wallet = call("payments", "GET", f"/wallets/{user_id}")
    wallet["transactions"] = call("payments", "GET", f"/wallets/{user_id}/transactions")["transactions"]
    return jsonify(wallet)


@app.post("/api/users/<user_id>/wallet/deposit")
def api_deposit(user_id):
    user_id = check_user(user_id)
    return jsonify(call("payments", "POST", f"/wallets/{user_id}/deposit",
                        json=request.get_json(silent=True) or {})), 201


# ── Saga de compra ──────────────────────────────────────────────────────────

class Saga:
    def __init__(self, user_id):
        self.order_id = "ORD-" + uuid.uuid4().hex[:10].upper()
        self.user_id = user_id
        self.steps = []
        self.status = "EN_CURSO"
        self.started = time.perf_counter()
        self.created_at = datetime.now(timezone.utc).isoformat()

    def run(self, name, service, fn, kind="accion"):
        """Ejecuta un paso, mide su tiempo y lo registra en la bitacora de la saga."""
        start = time.perf_counter()
        try:
            result = fn()
            self.steps.append({"step": name, "service": service, "kind": kind, "status": "ok",
                               "ms": round((time.perf_counter() - start) * 1000)})
            return result
        except ServiceError as e:
            self.steps.append({"step": name, "service": service, "kind": kind, "status": "error",
                               "http": e.status, "detail": e.body.get("error"),
                               "ms": round((time.perf_counter() - start) * 1000)})
            raise

    def finish(self, status, **extra):
        self.status = status
        record = {"order_id": self.order_id, "user_id": self.user_id, "status": status,
                  "steps": self.steps, "created_at": self.created_at,
                  "total_ms": round((time.perf_counter() - self.started) * 1000), **extra}
        with sagas_lock:
            sagas.appendleft(record)
        return record


@app.post("/api/users/<user_id>/checkout")
def api_checkout(user_id):
    user_id = check_user(user_id)
    simulate = (request.get_json(silent=True) or {}).get("simulate_failure", "")
    saga = Saga(user_id)

    # Paso 1: leer el carrito y los precios vigentes (consultas, sin efectos).
    try:
        cart = saga.run("Leer carrito", "cart", lambda: call("cart", "GET", f"/cart/{user_id}"), "consulta")
        ids = [i["game_id"] for i in cart["items"]]
        if not ids:
            return jsonify(saga.finish("ABORTADA", error="El carrito está vacío")), 400
        games = saga.run("Consultar precios", "catalog", lambda: games_by_id(ids), "consulta")
        owned = saga.run("Verificar biblioteca", "library",
                         lambda: call("library", "GET", f"/library/{user_id}"), "consulta")
    except ServiceError as e:
        return jsonify(saga.finish("ABORTADA", error=e.body.get("error"))), 503

    repetidos = [g["game_id"] for g in owned["games"] if g["game_id"] in ids]
    if repetidos:
        return jsonify(saga.finish("ABORTADA", error="Ya tienes algunos de estos juegos", owned=repetidos)), 409
    total = sum(games[i]["final_price_cents"] for i in ids if i in games)
    titles = ", ".join(games[i]["title"] for i in ids if i in games)

    # Paso 2: cobrar. Si falla, no hubo cambios y la saga simplemente se aborta.
    try:
        charge = saga.run("Cobrar orden", "payments", lambda: call("payments", "POST", "/charges", json={
            "user_id": user_id, "order_id": saga.order_id, "amount_cents": total,
            "description": f"Compra: {titles}"[:200], "simulate_failure": simulate == "payments"}))
    except ServiceError as e:
        if e.status >= 500:
            # No sabemos si el cargo alcanzo a aplicarse (p. ej. un timeout): se reembolsa por si acaso.
            compensate(saga, refund=True, revoke=False)
        return jsonify(saga.finish("ABORTADA", error=e.body.get("error"), total_cents=total)), \
            (402 if e.status == 402 else 503)

    # Paso 3: entregar los juegos. Si falla, se compensa: se quitan y se reembolsa.
    try:
        saga.run("Agregar a biblioteca", "library", lambda: call(
            "library", "POST", f"/library/{user_id}/grant",
            json={"order_id": saga.order_id, "game_ids": ids, "simulate_failure": simulate == "library"}))
    except ServiceError as e:
        ok = compensate(saga, refund=True, revoke=True)
        return jsonify(saga.finish("COMPENSADA" if ok else "REQUIERE_REVISION",
                                   error=e.body.get("error"), total_cents=total)), 503

    # Paso 4: vaciar el carrito. No es critico: si falla, la compra ya es valida.
    try:
        saga.run("Vaciar carrito", "cart", lambda: call("cart", "DELETE", f"/cart/{user_id}",
                                                       json={"game_ids": ids}))
    except ServiceError:
        pass

    return jsonify(saga.finish("COMPLETADA", total_cents=total, games=ids,
                               balance_cents=charge["balance_cents"])), 201


def compensate(saga, refund, revoke):
    ok = True
    if revoke:
        try:
            saga.run("Compensar: quitar juegos", "library",
                     lambda: call("library", "POST", "/library/revoke", json={"order_id": saga.order_id}),
                     "compensacion")
        except ServiceError:
            ok = False
    if refund:
        try:
            saga.run("Compensar: reembolsar pago", "payments",
                     lambda: call("payments", "POST", "/refunds", json={"order_id": saga.order_id}),
                     "compensacion")
        except ServiceError:
            ok = False
    return ok


@app.get("/api/sagas")
def api_sagas():
    with sagas_lock:
        return jsonify({"sagas": list(sagas)})


# ── Frontend (solo en ejecucion local sin Nginx) ────────────────────────────

FRONTEND_DIR = os.path.abspath(os.getenv("FRONTEND_DIR",
                                         os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")))

if os.path.isdir(FRONTEND_DIR):
    @app.get("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.get("/<path:filename>")
    def static_files(filename):
        return send_from_directory(FRONTEND_DIR, filename)
else:
    @app.get("/")
    def index():
        return jsonify({"service": "PixelVault API Gateway", "status": "/api/status"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), threaded=True)
