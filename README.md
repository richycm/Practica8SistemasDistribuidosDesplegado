# Práctica 8: Microservicios — PixelVault, tienda de videojuegos

**Alumno:** Ricardo Carmona Martínez
**Materia:** Sistemas Distribuidos
**Grupo:** 7CM1

PixelVault es una tienda de videojuegos digital (catálogo, carrito, cartera, biblioteca y reseñas)
construida como un conjunto de microservicios independientes en Python + Flask, cada uno con su propia
base de datos y su propio contenedor Docker. Se despliega en la nube con Render.

---

## Arquitectura

```text
                        Navegador (HTML + CSS + JS)
                                   |
                              HTTP / JSON
                                   |
                     +-------------v--------------+
                     |        API Gateway         |  :5000
                     |  enrutamiento, agregación, |
                     |  Saga de compra, /status   |
                     +--+------+------+------+--+-+
                        |      |      |      |  |
          +-------------+  +---+  +---+  +---+  +-------------+
          v                v      v      v                    v
     +---------+    +--------+ +-------+ +------------+  +----------+
     |Catálogo |    |Carrito | | Pagos | | Biblioteca |<-| Reseñas  |
     |  :5001  |    | :5002  | | :5003 | |   :5004    |  |  :5005   |
     +----+----+    +---+----+ +---+---+ +-----+------+  +----+-----+
          |             |          |           |              |
       [catalog]     [cart]   [payments]   [library]      [reviews]
          (cada servicio tiene su propia base de datos / esquema)
```

| Servicio | Puerto | Responsabilidad | Datos |
|---|---|---|---|
| `api-gateway` | 5000 | Punto de entrada único. Enruta, combina respuestas y orquesta la Saga de compra | sin estado (bitácora de sagas en memoria) |
| `catalog-service` | 5001 | 26 juegos reales de Steam: precios en MXN, descuentos, géneros, búsqueda y portada oficial | `games` |
| `cart-service` | 5002 | Juegos en el carrito de cada usuario | `cart_items` |
| `payment-service` | 5003 | Cartera virtual: saldo, recargas, cargos y reembolsos idempotentes | `wallets`, `transactions` |
| `library-service` | 5004 | Juegos que posee cada usuario y tiempo de juego | `owned_games` |
| `review-service` | 5005 | Reseñas; consulta directo a Biblioteca para verificar que el usuario tiene el juego | `reviews` |
| `frontend` | 8080 | Tienda web (Nginx en local, sitio estático en Render) | — |

### Saga de compra (orquestada por el gateway)

1. Leer el carrito (Carrito), consultar precios vigentes (Catálogo) y verificar la biblioteca (Biblioteca).
2. **Cobrar** la orden (Pagos). Si no hay saldo: `402` y la saga se aborta sin cambios.
3. **Entregar** los juegos (Biblioteca). Si falla, se **compensa**: se quitan los juegos de esa orden y se **reembolsa** el pago.
4. Vaciar el carrito (no crítico: si falla, la compra sigue siendo válida).

Los cargos y reembolsos se identifican con el `order_id`, por lo que un reintento nunca cobra dos veces.
Desde el carrito se puede **simular la caída de Pagos o de Biblioteca** para ver la compensación.

### Tolerancia a fallos

- Todas las llamadas entre servicios tienen *timeout* y los errores se devuelven como `503` indicando qué servicio falló.
- Si el servicio de **Reseñas** se cae, la tienda sigue funcionando sin calificaciones (degradación elegante).
- `GET /api/status` consulta `/health` de todos los servicios en paralelo; el frontend lo usa al iniciar para despertarlos (en el plan gratuito de Render se duermen).

---

## Ejecución local

### Opción A: Docker Compose (recomendada)

```bash
docker compose up --build
```

- Tienda: http://localhost:8080
- Estado de los servicios: http://localhost:5000/api/status
- Detener: `docker compose down` (agregar `-v` para borrar los datos)

### Opción B: sin Docker

```bash
pip install -r requirements.txt
iniciar.bat
```

Abre cada servicio en su ventana y la tienda en http://localhost:5000 (en este modo el gateway también sirve el frontend).
Sin `DATABASE_URL`, cada servicio usa su propio archivo SQLite en `<servicio>/data/`.

---

## Despliegue en Render

El archivo `render.yaml` es un *Blueprint* que crea todo de una vez: una base de datos PostgreSQL,
los 6 servicios web (Docker) y el frontend como sitio estático.

1. Subir esta carpeta a un repositorio de GitHub (el `render.yaml` debe quedar en la raíz).
2. En https://dashboard.render.com: **New > Blueprint**, elegir el repositorio y presionar **Apply**.
3. Esperar a que todos los servicios queden en *Live*.
4. Revisar que las URL reales de cada servicio coincidan con las variables `*_URL` del gateway, `LIBRARY_URL` de reseñas
   y `API_URL` del frontend. Si Render agregó un sufijo a algún nombre, corregirlo en **Environment** y volver a desplegar.

En Render todos los servicios comparten una base PostgreSQL, pero **cada uno trabaja en su propio esquema**
(`catalog`, `cart`, `payments`, `library`, `reviews`), así que ninguno puede leer las tablas de otro.

---

## API del gateway

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/status` | Estado y latencia de cada microservicio |
| GET | `/api/games?q=&genre=` | Catálogo con resumen de reseñas |
| GET | `/api/games/<id>` | Detalle del juego con sus reseñas |
| GET | `/api/genres` | Géneros y número de juegos |
| GET | `/api/users/<u>/profile` | Saldo, juegos poseídos y carrito |
| GET / POST | `/api/users/<u>/cart` | Ver carrito / agregar `{game_id}` |
| DELETE | `/api/users/<u>/cart/<id>` | Quitar del carrito |
| POST | `/api/users/<u>/checkout` | Saga de compra `{simulate_failure: "" \| "payments" \| "library"}` |
| GET | `/api/users/<u>/library` | Biblioteca con datos del catálogo |
| POST | `/api/users/<u>/library/<id>/play` | Registrar una sesión de juego |
| GET | `/api/users/<u>/wallet` | Saldo y movimientos |
| POST | `/api/users/<u>/wallet/deposit` | Recargar `{amount_cents}` |
| POST | `/api/games/<id>/reviews` | Publicar reseña `{user_id, recommended, body}` |
| GET | `/api/sagas` | Últimas sagas de compra con sus pasos |

---

## Estructura

```text
Codigo/
├── api-gateway/        app.py, Dockerfile, requirements.txt
├── catalog-service/    app.py, db.py, games_seed.json, actualizar_catalogo.py, Dockerfile
├── cart-service/       app.py, db.py, Dockerfile, requirements.txt
├── payment-service/    app.py, db.py, Dockerfile, requirements.txt
├── library-service/    app.py, db.py, Dockerfile, requirements.txt
├── review-service/     app.py, db.py, Dockerfile, requirements.txt
├── frontend/           index.html, styles.css, app.js, config.js, nginx.conf, Dockerfile
├── docker-compose.yml  Ejecución local con contenedores
├── render.yaml         Despliegue en Render (Blueprint)
├── iniciar.bat         Ejecución local sin Docker
└── requirements.txt
```

## Catálogo con datos reales de Steam

`catalog-service/games_seed.json` se genera con `python catalog-service/actualizar_catalogo.py`, que consulta la API pública
de la tienda de Steam (`store.steampowered.com/api/appdetails?appids=<id>&cc=mx&l=spanish`): nombre, desarrolladora,
precio en MXN, descuento, año, descripción en español y portada oficial. El servicio lee ese archivo al arrancar,
así que no depende de Steam en tiempo de ejecución. Las portadas se cargan desde el CDN de Steam; si una falla,
se muestra una portada de colores generada con CSS.

Proyecto escolar sin fines comerciales: los datos e imágenes pertenecen a sus respectivos dueños y las compras son simuladas.
