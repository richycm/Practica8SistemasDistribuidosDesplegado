// PixelVault - cliente web. Solo habla con el API Gateway.
const API = (window.PV_API || "").replace(/\/$/, "");

const state = {
  user: null,
  balance: 0,
  owned: new Set(),
  cart: new Set(),
  games: [],
  genres: [],
  filter: { q: "", genre: "", sort: "recent" },
  view: "store",
  lastSaga: null,
};

const $ = (sel) => document.querySelector(sel);
const app = $("#app");

// ── Utilidades ───────────────────────────────────────────────────────────────

const fmt = new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN" });
const money = (cents) => (cents === 0 ? "Gratis" : fmt.format(cents / 100));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fecha = (iso) => new Date(iso).toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });
const horas = (min) => (min < 60 ? `${min} min` : `${(min / 60).toFixed(1)} h`);

async function api(method, path, body) {
  let res;
  try {
    res = await fetch(API + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw Object.assign(new Error("No hay conexión con el API Gateway"), { status: 0 });
  }
  let data = {};
  try { data = await res.json(); } catch { /* respuesta vacia */ }
  if (!res.ok) throw Object.assign(new Error(data.error || `Error HTTP ${res.status}`), { status: res.status, data });
  return data;
}

function toast(msg, type = "") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

const fail = (e) => toast(e.data?.service ? `${e.message} [${e.data.service}]` : e.message, "error");

// Portada oficial de Steam; si no carga, queda la portada de colores generada con CSS.
function cover(g, cls = "", big = false) {
  const header = g.header_image || "";
  const src = big && header ? header.replace("header.jpg", "capsule_616x353.jpg") : header;
  return `<div class="cover ${cls} ${src ? "has-img" : ""}" style="--c1:${esc(g.color1 || "#333")};--c2:${esc(g.color2 || "#555")}">
    ${src ? `<img src="${esc(src)}" alt="${esc(g.title)}" loading="lazy" data-fallback="${esc(header)}">` : ""}
    <div class="cover-genre">${esc(g.genre || "")}</div>
    <div class="cover-title">${esc(g.title)}</div>
  </div>`;
}

function priceTag(g) {
  if (g.discount_pct > 0) {
    return `<div class="price"><span class="disc">-${g.discount_pct}%</span>
      <span class="vals"><span class="old">${money(g.price_cents)}</span><span class="now">${money(g.final_price_cents)}</span></span></div>`;
  }
  return `<div class="price plain"><span class="vals"><span class="now">${money(g.final_price_cents)}</span></span></div>`;
}

function reviewLabel(r) {
  if (!r || !r.total) return `<span class="rev none">Sin reseñas</span>`;
  const cls = r.positive_pct >= 70 ? "good" : r.positive_pct >= 40 ? "mixed" : "bad";
  return `<span class="rev ${cls}" title="${r.positive_pct}% de ${r.total} reseñas">${esc(r.label)} (${r.total})</span>`;
}

function actionButton(g, small = true) {
  const s = small ? "small" : "";
  if (state.owned.has(g.id)) return `<span class="owned-badge">En tu biblioteca</span>`;
  if (state.cart.has(g.id)) return `<button class="btn ${s}" data-go="cart">En el carrito</button>`;
  const label = g.final_price_cents === 0 ? "Obtener" : small ? "Agregar" : "Agregar al carrito";
  return `<button class="btn buy ${s}" data-add="${esc(g.id)}">${label}</button>`;
}

// ── Arranque: despertar servicios ────────────────────────────────────────────

async function wake() {
  const list = $("#wake-list");
  const msg = $("#wake-msg");
  const inicio = Date.now();
  for (let intento = 1; intento <= 8; intento++) {
    try {
      msg.innerHTML = `<span class="spinner"></span> Despertando microservicios (intento ${intento})...`;
      const st = await api("GET", "/api/status");
      list.innerHTML = st.services.map((s) =>
        `<li><span>${esc(s.name)}</span><span class="status ${s.status}">${s.status === "ok" ? `OK ${s.latency_ms} ms` : "SIN RESPUESTA"}</span></li>`).join("");
      if (st.status === "ok") {
        msg.textContent = `Todos los servicios responden (${((Date.now() - inicio) / 1000).toFixed(1)} s)`;
        await new Promise((r) => setTimeout(r, 500));
        $("#wake").classList.add("hidden");
        return;
      }
    } catch {
      list.innerHTML = `<li><span>API Gateway</span><span class="status down">DESPERTANDO</span></li>`;
    }
    await new Promise((r) => setTimeout(r, 4000));
  }
  msg.textContent = "Algunos servicios no respondieron. La tienda funcionará con lo disponible.";
  setTimeout(() => $("#wake").classList.add("hidden"), 1800);
}

// ── Sesion ───────────────────────────────────────────────────────────────────

function getSavedUser() {
  try { return localStorage.getItem("pv_user"); } catch { return null; }
}

function saveUser(u) {
  try { u ? localStorage.setItem("pv_user", u) : localStorage.removeItem("pv_user"); } catch { /* sin storage */ }
}

async function login(user) {
  state.user = user.trim().toLowerCase();
  try {
    await refreshProfile();
  } catch (e) {
    state.user = null;
    saveUser(null);
    $("#login").classList.remove("hidden");
    return fail(e);
  }
  saveUser(state.user);
  $("#login").classList.add("hidden");
  $("#username").textContent = state.user;
  await loadCatalog();
  show(state.view);
}

async function refreshProfile() {
  const p = await api("GET", `/api/users/${state.user}/profile`);
  state.balance = p.balance_cents;
  state.owned = new Set(p.owned);
  state.cart = new Set(p.cart);
  $("#balance").textContent = fmt.format(p.balance_cents / 100);
  $("#cart-count").textContent = p.cart.length;
}

async function loadCatalog() {
  const [g, gen] = await Promise.all([api("GET", "/api/games"), api("GET", "/api/genres")]);
  state.games = g.games;
  state.genres = gen.genres;
  if (g.reviews_unavailable) toast("El servicio de Reseñas no responde: se muestra el catálogo sin calificaciones.", "error");
}

// ── Navegacion ───────────────────────────────────────────────────────────────

const views = { store: renderStore, library: renderLibrary, cart: renderCart, wallet: renderWallet, monitor: renderMonitor };

function show(view) {
  state.view = view;
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  views[view]();
}

// ── Tienda ───────────────────────────────────────────────────────────────────

function filteredGames() {
  const { q, genre, sort } = state.filter;
  let list = state.games.filter((g) =>
    (!genre || g.genre === genre) &&
    (!q || (g.title + " " + g.developer + " " + g.tags.join(" ")).toLowerCase().includes(q.toLowerCase())));
  const sorters = {
    recent: (a, b) => b.release_year - a.release_year,
    cheap: (a, b) => a.final_price_cents - b.final_price_cents,
    discount: (a, b) => b.discount_pct - a.discount_pct,
    rating: (a, b) => (b.reviews?.positive_pct ?? -1) - (a.reviews?.positive_pct ?? -1),
  };
  return list.sort(sorters[sort]);
}

function renderStore() {
  const hero = [...state.games].sort((a, b) => b.discount_pct - a.discount_pct)[0];
  const { q, genre, sort } = state.filter;
  app.innerHTML = `
    ${hero ? `<section class="hero" data-open="${esc(hero.id)}">
      ${cover(hero, "hero-cover", true)}
      <div class="hero-info">
        <span class="tag-label">Oferta destacada</span>
        <h2>${esc(hero.title)}</h2>
        <div class="desc">${esc(hero.description)}</div>
        <div class="tags">${hero.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
        <div class="card-foot">${priceTag(hero)}${actionButton(hero, false)}</div>
      </div>
    </section>` : ""}
    <div class="toolbar">
      <input id="search" placeholder="Buscar por título, desarrolladora o etiqueta" value="${esc(q)}">
      <select id="sort">
        <option value="recent">Más recientes</option>
        <option value="cheap">Precio: menor a mayor</option>
        <option value="discount">Mayor descuento</option>
        <option value="rating">Mejor calificados</option>
      </select>
    </div>
    <div class="chips">
      <button class="chip ${!genre ? "active" : ""}" data-genre="">Todos</button>
      ${state.genres.map((g) => `<button class="chip ${genre === g.genre ? "active" : ""}" data-genre="${esc(g.genre)}">${esc(g.genre)} (${g.total})</button>`).join("")}
    </div>
    <div id="grid" class="grid"></div>`;
  $("#sort").value = sort;
  $("#search").addEventListener("input", (e) => { state.filter.q = e.target.value; renderGrid(); });
  $("#sort").addEventListener("change", (e) => { state.filter.sort = e.target.value; renderGrid(); });
  renderGrid();
}

function renderGrid() {
  const list = filteredGames();
  $("#grid").innerHTML = list.length ? list.map((g) => `
    <article class="card" data-open="${esc(g.id)}">
      ${cover(g)}
      <div class="card-body">
        <div class="card-title">${esc(g.title)}</div>
        <div class="card-meta"><span class="muted">${esc(g.developer)} · ${g.release_year}</span>${reviewLabel(g.reviews)}</div>
        <div class="tags">${g.tags.slice(0, 3).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
        <div class="card-foot">${priceTag(g)}${actionButton(g)}</div>
      </div>
    </article>`).join("") : `<div class="empty">No hay juegos que coincidan con la búsqueda.</div>`;
}

async function addToCart(id) {
  try {
    const c = await api("POST", `/api/users/${state.user}/cart`, { game_id: id });
    state.cart = new Set(c.items.map((i) => i.id));
    $("#cart-count").textContent = c.count;
    const g = state.games.find((x) => x.id === id);
    toast(`${g ? g.title : id} se agregó al carrito`, "ok");
    rerender();
  } catch (e) { fail(e); }
}

function rerender() {
  if (!$("#modal").classList.contains("hidden") && state.openGame) openGame(state.openGame, true);
  views[state.view]();
}

// ── Detalle de juego y resenas ───────────────────────────────────────────────

async function openGame(id, keepScroll = false) {
  state.openGame = id;
  try {
    const g = await api("GET", `/api/games/${id}`);
    const rv = g.reviews;
    const mine = rv?.reviews.find((r) => r.user_id === state.user);
    const owns = state.owned.has(id);
    $("#modal-body").innerHTML = `
      ${cover(g, "hero-cover", true)}
      <div class="modal-inner">
        <div>
          <h2>${esc(g.title)}</h2>
          <p>${esc(g.description)}</p>
          <div class="tags">${g.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
          <h3 style="margin-top:22px">Reseñas de jugadores ${rv ? `· ${reviewLabel(rv.summary)}` : ""}</h3>
          ${rv === null ? `<p class="muted">El servicio de Reseñas no está disponible en este momento.</p>` : ""}
          ${owns && rv ? `
            <form class="review review-form" id="review-form">
              <strong>${mine ? "Editar tu reseña" : "Escribe una reseña"}</strong>
              <div class="radio-row">
                <label><input type="radio" name="rec" value="1" ${!mine || mine.recommended ? "checked" : ""}> Lo recomiendo</label>
                <label><input type="radio" name="rec" value="0" ${mine && !mine.recommended ? "checked" : ""}> No lo recomiendo</label>
              </div>
              <textarea name="body" placeholder="¿Qué te pareció?" maxlength="1000" required>${esc(mine?.body || "")}</textarea>
              <button class="btn primary small" type="submit">Publicar reseña</button>
            </form>` : ""}
          ${!owns && rv ? `<p class="muted small">Solo quienes tienen el juego en su biblioteca pueden reseñarlo (lo verifica el servicio de Reseñas con el de Biblioteca).</p>` : ""}
          ${rv ? rv.reviews.map((r) => `
            <div class="review">
              <div class="who">${esc(r.user_id)} · <span class="${r.recommended ? "pos" : "neg"}">${r.recommended ? "Recomendado" : "No recomendado"}</span></div>
              <div class="muted small">${horas(r.minutes_played)} jugadas al reseñar · ${fecha(r.created_at)}</div>
              <p style="margin:8px 0 0">${esc(r.body)}</p>
            </div>`).join("") || `<p class="muted">Todavía nadie lo reseña.</p>` : ""}
        </div>
        <aside class="detail-side">
          <div>${priceTag(g)}</div>
          <div>${actionButton(g, false)}</div>
          <div><span class="k">Desarrolladora:</span> ${esc(g.developer)}</div>
          <div><span class="k">Género:</span> ${esc(g.genre)}</div>
          <div><span class="k">Lanzamiento:</span> ${g.release_year}</div>
          <a class="steam-link" href="${esc(g.steam_url)}" target="_blank" rel="noopener">Ver en la tienda de Steam</a>
        </aside>
      </div>`;
    $("#modal").classList.remove("hidden");
    if (!keepScroll) $("#modal").scrollTop = 0;
    const form = $("#review-form");
    if (form) form.addEventListener("submit", (e) => submitReview(e, id));
  } catch (e) { fail(e); }
}

async function submitReview(e, id) {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    await api("POST", `/api/games/${id}/reviews`, { user_id: state.user, recommended: f.get("rec") === "1", body: f.get("body") });
    toast("Reseña publicada", "ok");
    await loadCatalog();
    openGame(id, true);
    views[state.view]();
  } catch (err) { fail(err); }
}

function closeModal() {
  $("#modal").classList.add("hidden");
  state.openGame = null;
}

// ── Carrito y Saga de compra ─────────────────────────────────────────────────

async function renderCart() {
  app.innerHTML = `<div class="empty"><span class="spinner"></span> Cargando carrito...</div>`;
  let c;
  try { c = await api("GET", `/api/users/${state.user}/cart`); } catch (e) { app.innerHTML = ""; return fail(e); }
  app.innerHTML = `
    <h1>Tu carrito</h1>
    <div class="two-col">
      <div>
        <div class="panel">
          ${c.items.length ? c.items.map((g) => `
            <div class="row">
              ${cover(g, "mini")}
              <div class="grow"><div class="title">${esc(g.title)}</div><div class="muted small">${esc(g.developer)}</div></div>
              ${priceTag(g)}
              <button class="link" data-remove="${esc(g.id)}">Quitar</button>
            </div>`).join("") : `<div class="empty">Tu carrito está vacío.<br><br><button class="btn primary" data-go="store">Ir a la tienda</button></div>`}
        </div>
        <div id="saga-result">${state.lastSaga ? sagaHtml(state.lastSaga) : ""}</div>
      </div>
      <aside class="panel">
        <h3>Resumen</h3>
        <div class="summary-line"><span>Subtotal</span><span>${fmt.format(c.subtotal_cents / 100)}</span></div>
        <div class="summary-line"><span>Descuentos</span><span class="pos">-${fmt.format((c.subtotal_cents - c.total_cents) / 100)}</span></div>
        <div class="summary-line total"><span>Total</span><span>${fmt.format(c.total_cents / 100)}</span></div>
        <div class="summary-line"><span class="muted">Tu saldo</span><span>${fmt.format(state.balance / 100)}</span></div>
        <label class="muted small" for="simulate" style="display:block;margin:14px 0 6px">Prueba de tolerancia a fallos</label>
        <select id="simulate" style="width:100%">
          <option value="">Compra normal</option>
          <option value="payments">Simular caída de Pagos</option>
          <option value="library">Simular caída de Biblioteca (se compensa)</option>
        </select>
        <button id="checkout" class="btn buy" style="width:100%;margin-top:14px" ${c.items.length ? "" : "disabled"}>Comprar</button>
      </aside>
    </div>`;
  const btn = $("#checkout");
  if (btn) btn.addEventListener("click", checkout);
}

async function checkout() {
  const btn = $("#checkout");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Procesando...`;
  const simulate_failure = $("#simulate").value;
  let saga;
  try {
    saga = await api("POST", `/api/users/${state.user}/checkout`, { simulate_failure });
    toast(`Compra completada: ${saga.order_id}`, "ok");
  } catch (e) {
    saga = e.data?.order_id ? e.data : null;
    fail(e);
  }
  state.lastSaga = saga;
  await refreshProfile().catch(fail);
  renderCart();
}

const KINDS = { consulta: "consulta", accion: "acción", compensacion: "compensación" };

function sagaHtml(s) {
  const titulo = { COMPLETADA: "Compra completada", COMPENSADA: "Compra revertida (Saga compensada)",
    ABORTADA: "Compra abortada", REQUIERE_REVISION: "Compensación incompleta" }[s.status] || s.status;
  return `<div class="panel">
    <h3>Saga ${esc(s.order_id)} <span class="status ${esc(s.status)}">${esc(s.status)}</span></h3>
    <p class="muted small">${titulo}${s.error ? ` · ${esc(s.error)}` : ""} · ${s.total_ms} ms en total</p>
    <ul class="saga">${s.steps.map((p) => `
      <li><span class="dot ${p.status === "error" ? "error" : p.kind === "compensacion" ? "comp" : "ok"}"></span>
        <div><div>${esc(p.step)}</div><div class="svc">${esc(p.service)} · ${esc(KINDS[p.kind] || p.kind)}</div>
          ${p.detail ? `<div class="det">HTTP ${p.http}: ${esc(p.detail)}</div>` : ""}</div>
        <span class="muted small">${p.ms} ms</span></li>`).join("")}</ul>
  </div>`;
}

// ── Biblioteca ───────────────────────────────────────────────────────────────

async function renderLibrary() {
  app.innerHTML = `<div class="empty"><span class="spinner"></span> Cargando biblioteca...</div>`;
  let lib;
  try { lib = await api("GET", `/api/users/${state.user}/library`); } catch (e) { app.innerHTML = ""; return fail(e); }
  app.innerHTML = `
    <h1>Biblioteca</h1>
    <p class="muted">${lib.count} juegos · ${horas(lib.total_minutes)} jugadas en total</p>
    <div class="panel">
      ${lib.games.length ? lib.games.map((g) => `
        <div class="row">
          ${cover(g, "mini")}
          <div class="grow">
            <div class="title">${esc(g.title)}</div>
            <div class="muted small">${horas(g.minutes_played)} jugadas · ${g.last_played ? `última vez ${fecha(g.last_played)}` : "nunca jugado"}</div>
            <div class="muted small">Orden ${esc(g.order_id)} · adquirido ${fecha(g.acquired_at)}</div>
          </div>
          <button class="btn small" data-open="${esc(g.game_id)}">Reseñar</button>
          <button class="btn primary small" data-play="${esc(g.game_id)}">Jugar</button>
        </div>`).join("") : `<div class="empty">Aún no tienes juegos.<br><br><button class="btn primary" data-go="store">Explorar la tienda</button></div>`}
    </div>`;
}

async function play(id) {
  try {
    const r = await api("POST", `/api/users/${state.user}/library/${id}/play`);
    toast(`Sesión de juego registrada: ${r.session_minutes} min`, "ok");
    renderLibrary();
  } catch (e) { fail(e); }
}

// ── Cartera ──────────────────────────────────────────────────────────────────

async function renderWallet() {
  app.innerHTML = `<div class="empty"><span class="spinner"></span> Cargando cartera...</div>`;
  let w;
  try { w = await api("GET", `/api/users/${state.user}/wallet`); } catch (e) { app.innerHTML = ""; return fail(e); }
  app.innerHTML = `
    <h1>Cartera</h1>
    <div class="two-col">
      <div class="panel">
        <h3>Movimientos</h3>
        <table>
          <thead><tr><th>Fecha</th><th>Tipo</th><th>Concepto</th><th class="num">Monto</th><th class="num">Saldo</th></tr></thead>
          <tbody>${w.transactions.map((t) => `
            <tr><td class="small">${fecha(t.created_at)}</td><td>${esc(t.kind)}</td>
              <td class="small">${esc(t.description)}${t.order_id ? `<div class="muted">${esc(t.order_id)}</div>` : ""}</td>
              <td class="num ${t.amount_cents >= 0 ? "pos" : "neg"}">${fmt.format(t.amount_cents / 100)}</td>
              <td class="num">${fmt.format(t.balance_after / 100)}</td></tr>`).join("")}</tbody>
        </table>
      </div>
      <aside class="panel">
        <h3>Saldo disponible</h3>
        <div class="big-balance">${fmt.format(w.balance_cents / 100)}</div>
        <p class="muted small">Agregar fondos (simulado)</p>
        <div class="deposit-btns">
          ${[100, 250, 500, 1000].map((m) => `<button class="btn" data-deposit="${m * 100}">+ ${fmt.format(m)}</button>`).join("")}
        </div>
      </aside>
    </div>`;
}

async function deposit(cents) {
  try {
    await api("POST", `/api/users/${state.user}/wallet/deposit`, { amount_cents: cents });
    await refreshProfile();
    toast(`Se agregaron ${fmt.format(cents / 100)} a tu cartera`, "ok");
    renderWallet();
  } catch (e) { fail(e); }
}

// ── Monitor de servicios ─────────────────────────────────────────────────────

async function renderMonitor() {
  app.innerHTML = `<div class="empty"><span class="spinner"></span> Consultando servicios...</div>`;
  let st, sg;
  try {
    [st, sg] = await Promise.all([api("GET", "/api/status"), api("GET", "/api/sagas")]);
  } catch (e) { app.innerHTML = ""; return fail(e); }
  const node = (s) => `<div class="node"><div class="n-name">${esc(s.name)}</div>
    <div><span class="status ${s.status}">${s.status === "ok" ? "EN LÍNEA" : "CAÍDO"}</span></div>
    <div class="n-lat">${s.latency_ms} ms</div></div>`;
  app.innerHTML = `
    <h1>Estado de los microservicios</h1>
    <p class="muted">El gateway consulta <code>/health</code> de cada servicio en paralelo · ${fecha(st.checked_at)}</p>
    <div class="panel">
      <div class="arch">
        <div class="node gw"><div class="n-name">Cliente web (este navegador)</div></div>
        <div class="arrow-row">HTTP / JSON</div>
        <div class="node gw"><div class="n-name">API Gateway</div><div><span class="status ok">EN LÍNEA</span></div>
          <div class="n-lat">Enrutamiento · agregación · orquestación de la Saga</div></div>
        <div class="arrow-row">HTTP / JSON</div>
        ${st.services.map(node).join("")}
      </div>
      <div style="margin-top:16px;text-align:right"><button class="btn small" data-go="monitor">Actualizar</button></div>
    </div>
    <h2>Últimas sagas de compra</h2>
    ${sg.sagas.length ? sg.sagas.map(sagaHtml).join("") : `<div class="panel empty">Aún no se ha hecho ninguna compra desde que arrancó el gateway.</div>`}`;
}

// ── Eventos globales ─────────────────────────────────────────────────────────

document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-add],[data-go],[data-open],[data-remove],[data-play],[data-deposit],[data-genre],[data-view]");
  if (!t) return;
  if (t.dataset.add) { e.stopPropagation(); return addToCart(t.dataset.add); }
  if (t.dataset.go) { closeModal(); return show(t.dataset.go); }
  if (t.dataset.remove) {
    return api("DELETE", `/api/users/${state.user}/cart/${t.dataset.remove}`)
      .then(refreshProfile).then(renderCart).catch(fail);
  }
  if (t.dataset.play) return play(t.dataset.play);
  if (t.dataset.deposit) return deposit(Number(t.dataset.deposit));
  if (t.dataset.genre !== undefined && t.classList.contains("chip")) { state.filter.genre = t.dataset.genre; return renderStore(); }
  if (t.dataset.view) return show(t.dataset.view);
  if (t.dataset.open) return openGame(t.dataset.open);
});

// Si una portada de Steam no existe, se prueba la imagen alternativa y luego la portada de colores.
document.addEventListener("error", (e) => {
  const img = e.target;
  if (img.tagName !== "IMG" || !img.closest(".cover")) return;
  if (img.dataset.fallback && img.getAttribute("src") !== img.dataset.fallback) {
    img.src = img.dataset.fallback;
  } else {
    img.closest(".cover").classList.remove("has-img");
    img.remove();
  }
}, true);

$("#modal-close").addEventListener("click", closeModal);
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

$("#login-form").addEventListener("submit", (e) => {
  e.preventDefault();
  login($("#login-user").value);
});

$("#logout").addEventListener("click", () => {
  saveUser(null);
  state.user = null;
  state.lastSaga = null;
  app.innerHTML = "";
  $("#login").classList.remove("hidden");
});

(async function start() {
  await wake();
  const saved = getSavedUser();
  if (saved) login(saved);
  else $("#login").classList.remove("hidden");
})();
