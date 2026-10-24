/* SmartBrew kiosk front-end (vanilla JS, no build step).
 * Talks to the Flask REST API in smartbrew/app.py and renders every screen:
 * welcome/login, menu, customise, checkout, brewing/receipt, profile,
 * technician maintenance, admin diagnostics, plus the demo control panel. */
/**
 * Shorthand for querySelector: find the first element that matches a CSS selector.
 * @param {string} s - CSS selector.
 * @param {ParentNode} [el=document] - Element to search inside.
 * @returns {Element|null} The first matching element, or null.
 */
const $ = (s, el = document) => el.querySelector(s);
/**
 * Shorthand for querySelectorAll: find all elements that match a CSS selector.
 * @param {string} s - CSS selector.
 * @param {ParentNode} [el=document] - Element to search inside.
 * @returns {Element[]} Array of matching elements.
 */
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const EMOJI = { "Espresso": "☕", "Americano": "🫖", "Cappuccino": "☁️", "Caffe Latte": "🥛", "Flat White": "🤍",
  "Mocha": "🍫", "Hot Chocolate": "🍫", "Chai Latte": "🌿" };
/**
 * Escape text for safe insertion into HTML (prevents script injection).
 * @param {*} s - Any value; null or undefined become an empty string.
 * @returns {string} HTML-escaped text.
 */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
/**
 * Format a number as Swedish kronor without decimals.
 * @param {number} n - Amount.
 * @returns {string} e.g. "42 kr".
 */
const kr = (n) => `${Number(n).toFixed(0)} kr`;

const S = { user: null, guest: false, machine: "operational", online: true, menu: null, view: "welcome" };

/**
 * Call the SmartBrew REST API with JSON in and JSON out.
 * @param {string} path - API path, e.g. "/api/menu".
 * @param {Object} [opts] - fetch options; `opts.body` is serialised to JSON.
 * @returns {Promise<Object>} Parsed JSON response. Throws an Error with `status` and `data` for non-2xx responses.
 */
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, credentials: "same-origin", ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) { const e = new Error(data.error || `HTTP ${res.status}`); e.status = res.status; e.data = data; throw e; }
  return data;
}
/**
 * Show a short notification message at the bottom of the screen.
 * @param {string} msg - Text to show.
 * @param {number} [ms=2600] - How long to show it, in milliseconds.
 * @returns {void}
 */
function toast(msg, ms = 2600) { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), ms); }
/**
 * Open the modal dialog with the given content.
 * @param {string} html - HTML content of the dialog.
 * @returns {void}
 */
function openModal(html) { $("#modalBox").innerHTML = html; $("#modal").classList.remove("hidden"); }
/**
 * Close and empty the modal dialog.
 * @returns {void}
 */
function closeModal() { $("#modal").classList.add("hidden"); $("#modalBox").innerHTML = ""; }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });

/* ----------------------------------------------------------- shell */
/**
 * Fetch the session and machine status from the server and update the header.
 * @returns {Promise<void>}
 */
async function refreshState() {
  const st = await api("/api/state");
  S.user = st.user; S.guest = st.guest; S.machine = st.machine_status; S.online = st.network_online;
  renderChrome();
}
/**
 * Render the header (status pills, user menu) and the warning banner from the current state.
 * @returns {void}
 */
function renderChrome() {
  const pills = [
    S.machine === "operational" ? `<span class="pill ok">● Operational</span>` : `<span class="pill bad">● Out of service</span>`,
    S.online ? `<span class="pill ok">Online</span>` : `<span class="pill warn">Offline mode</span>`,
  ];
  $("#statusPills").innerHTML = pills.join("");
  const ub = $("#userBox");
  if (S.user) {
    const role = S.user.role;
    ub.innerHTML = `<span>👤 ${esc(S.user.name)} <span class="muted">(${role})</span></span>
      ${role === "customer" ? `<button data-go="menu">Menu</button><button data-go="profile">Profile</button>` : ""}
      ${role === "technician" ? `<button data-go="tech">Maintenance</button><button data-go="menu">Menu</button>` : ""}
      ${role === "admin" ? `<button data-go="admin">Diagnostics</button><button data-go="tech">Maintenance</button><button data-go="menu">Menu</button>` : ""}
      <button id="logoutBtn">Log out</button>`;
  } else if (S.guest) {
    ub.innerHTML = `<span>Guest</span><button id="logoutBtn">Exit</button>`;
  } else ub.innerHTML = "";
  $$("[data-go]", ub).forEach((b) => (b.onclick = () => go(b.dataset.go)));
  const lb = $("#logoutBtn"); if (lb) lb.onclick = async () => { await api("/api/auth/logout", { method: "POST" }); await refreshState(); go("welcome"); };
  const banner = $("#banner");
  if (S.machine === "suspended") { banner.className = "banner bad"; banner.textContent = "⚠ A waste bin is full. Beverage preparation is suspended until a technician empties it."; }
  else if (!S.online) { banner.className = "banner warn"; banner.textContent = "Network unavailable – you can still order. Card/wallet payments are stored and settled when the connection returns; work badges work as normal."; }
  else banner.className = "banner hidden";
}
/**
 * Navigate to one of the kiosk screens.
 * @param {"welcome"|"menu"|"profile"|"tech"|"admin"} view - Screen to show.
 * @returns {void}
 */
function go(view) { S.view = view; ({ welcome: viewWelcome, menu: viewMenu, profile: viewProfile, tech: viewTech, admin: viewAdmin }[view])(); }

/* ----------------------------------------------------------- F1 + F2 welcome / login / register */
/**
 * F1/F2 - Render the welcome screen with the password login, card tap, sign-up and guest options.
 * @param {"login"|"card"|"register"} [tab="login"] - Which tab to open.
 * @returns {void}
 */
function viewWelcome(tab = "login") {
  $("#screen").innerHTML = `
  <div class="welcome">
    <div class="hero">
      <div><h1>Good ${greeting()}!</h1><p>Personalised coffee in seconds. Log in with your password or simply tap your card or badge.</p></div>
      <div><button class="btn ghost" style="background:#fff" id="guestBtn">Continue as guest →</button>
      <p class="small" style="opacity:.8;margin-top:10px">Guests can order and pay, but get no saved recipes or history-based suggestions.</p></div>
    </div>
    <div class="panel">
      <div class="tabs">
        <button class="tab ${tab === "login" ? "active" : ""}" data-tab="login">Log in</button>
        <button class="tab ${tab === "card" ? "active" : ""}" data-tab="card">Tap card</button>
        <button class="tab ${tab === "register" ? "active" : ""}" data-tab="register">Create account</button>
      </div>
      <div id="authBody"></div>
    </div>
  </div>`;
  $$(".tab").forEach((t) => (t.onclick = () => viewWelcome(t.dataset.tab)));
  $("#guestBtn").onclick = async () => { await api("/api/auth/guest", { method: "POST" }); await refreshState(); go("menu"); };
  const b = $("#authBody");
  if (tab === "login") {
    b.innerHTML = `<form id="f"><label>E-mail<input name="email" type="email" autocomplete="username" required></label>
      <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
      <div class="err" id="e"></div><button class="btn block">Log in</button></form>`;
    $("#f").onsubmit = async (ev) => { ev.preventDefault(); const fd = Object.fromEntries(new FormData(ev.target));
      try { await api("/api/auth/login", { method: "POST", body: { method: "password", ...fd } }); await afterLogin(); }
      catch (e) { $("#e").textContent = e.message; } };
  } else if (tab === "card") {
    b.innerHTML = `<div class="tapzone"><div class="big">📶</div>Hold your card or work badge near the reader</div>
      <label>Card UID (simulated NFC read)<input id="uid" placeholder="e.g. CARD-ALICE"></label>
      <div class="chips" id="quickCards"></div><div class="err" id="e"></div>
      <button class="btn block" id="tapBtn">Tap</button>`;
    api("/api/demo/state").then((d) => { $("#quickCards").innerHTML = d.cards.map((c) => `<button class="chip" data-uid="${esc(c.card_uid)}">${esc(c.card_uid)}</button>`).join("") + `<button class="chip" data-uid="UNKNOWN-99">unknown card</button>`;
      $$("#quickCards .chip").forEach((c) => (c.onclick = () => { $("#uid").value = c.dataset.uid; $("#tapBtn").click(); })); });
    $("#tapBtn").onclick = async () => { try { await api("/api/auth/login", { method: "POST", body: { method: "card", card_uid: $("#uid").value } }); await afterLogin(); }
      catch (e) { $("#e").textContent = e.message; } };
  } else {
    b.innerHTML = `<form id="f">
      <div class="row2"><label>Name<input name="name" required></label><label>E-mail<input name="email" type="email" required></label></div>
      <div class="row2"><label>Password (min 6)<input name="password" type="password" minlength="6" required></label>
      <label>Card UID (optional)<input name="card_uid" placeholder="link your NFC card"></label></div>
      <h3 style="margin-top:12px">Your default preferences</h3>
      ${prefFields({})}
      <div class="err" id="e"></div><button class="btn block">Create account</button></form>`;
    $("#f").onsubmit = async (ev) => { ev.preventDefault(); const fd = Object.fromEntries(new FormData(ev.target));
      const prefs = { milk: fd.milk, strength: fd.strength, temperature: fd.temperature, size: fd.size, sugar: fd.sugar, receipt: fd.receipt };
      try { await api("/api/auth/register", { method: "POST", body: { name: fd.name, email: fd.email, password: fd.password, card_uid: fd.card_uid, preferences: prefs } });
        toast("Account created – your preferences are saved"); await afterLogin(); }
      catch (e) { $("#e").textContent = e.message; } };
  }
}
/**
 * Pick a greeting word for the current time of day.
 * @returns {string} "morning", "afternoon" or "evening".
 */
function greeting() { const h = new Date().getHours(); return h < 12 ? "morning" : h < 18 ? "afternoon" : "evening"; }
/**
 * After a successful login, refresh the state and open the right screen for the user's role.
 * @returns {Promise<void>}
 */
async function afterLogin() { await refreshState(); toast(`Welcome, ${S.user.name.split(" ")[0]}!`);
  go(S.user.role === "technician" ? "tech" : S.user.role === "admin" ? "admin" : "menu"); }
/**
 * Build the preference form fields (milk, strength, temperature, size, sugar, receipt).
 * @param {Object} p - Current preferences used to pre-select values.
 * @returns {string} HTML for the form fields.
 */
function prefFields(p) {
  /**
   * Build a <select> element with one option pre-selected.
   * @param {string} name - Field name.
   * @param {Array<[string,string]>} opts - [value, label] pairs.
   * @param {*} cur - Value to pre-select.
   * @returns {string} HTML of the select element.
   */
  const sel = (name, opts, cur) => `<select name="${name}">${opts.map(([v, l]) => `<option value="${v}" ${String(cur) === String(v) ? "selected" : ""}>${l}</option>`).join("")}</select>`;
  return `<div class="row3">
    <label>Milk ${sel("milk", [["whole_milk", "Whole"], ["oat_milk", "Oat"], ["almond_milk", "Almond"]], p.milk || "whole_milk")}</label>
    <label>Strength ${sel("strength", [[1, "Mild"], [2, "Normal"], [3, "Strong"]], p.strength || 2)}</label>
    <label>Temperature ${sel("temperature", [["warm", "Warm"], ["hot", "Hot"], ["extra_hot", "Extra hot"]], p.temperature || "hot")}</label>
    <label>Size ${sel("size", [["S", "Small"], ["M", "Medium"], ["L", "Large"]], p.size || "M")}</label>
    <label>Sugar ${sel("sugar", [[0, "None"], [1, "×1"], [2, "×2"], [3, "×3"]], p.sugar ?? 0)}</label>
    <label>Receipt ${sel("receipt", [["email", "E-mail"], ["qr", "QR code"]], p.receipt || "email")}</label></div>`;
}

/* ----------------------------------------------------------- F3–F7 menu */
/**
 * F3-F7 - Render the main ordering screen: natural-language box, suggestions, saved drinks and menu grid.
 * @returns {Promise<void>}
 */
async function viewMenu() {
  if (!S.user && !S.guest) return go("welcome");
  const [menu, recs, recipes] = await Promise.all([api("/api/menu"), api("/api/recommendations"),
    S.user ? api("/api/recipes").catch(() => ({ recipes: [] })) : Promise.resolve(null)]);
  S.menu = menu;
  const recIds = new Set(recs.recommendations.map((r) => r.drink_id));
  const c = recs.context;
  const ctx = `${String(c.hour).padStart(2, "0")}:00 · ` + (c.weather ? `${c.weather.temp_c}°C ${esc(c.weather.condition)}` : `weather unavailable – using clock &amp; history`) +
    (c.personalised ? " · based on your order history" : " · log in for personal suggestions");
  $("#screen").innerHTML = `
    <div class="section"><h2>🗣 Just tell me what you want</h2>
      <form class="nl" id="nlForm"><input id="nlText" placeholder="e.g. “I need a big sweet coffee with no dairy”" autocomplete="off"><button class="btn">Go</button></form>
      <div class="nl-result" id="nlRes"></div></div>
    <div class="section"><h2>✨ Suggested for you</h2><div class="ctx">${ctx}</div>
      <div class="recs">${recs.recommendations.map((r) => `<div class="rec" data-id="${r.drink_id}"><b>${EMOJI[r.name] || "☕"} ${esc(r.name)}</b> · ${kr(r.price)}<div class="why">${r.reasons.map(esc).join(" · ")}</div></div>`).join("") || `<span class="muted">No suggestions available</span>`}</div></div>
    ${recipes ? `<div class="section"><h2>⭐ My saved drinks <span class="muted small">(${recipes.recipes.length}/${recipes.limit})</span></h2>
      <div class="saved">${recipes.recipes.map((r) => `<div class="item"><div><b>${esc(r.name)}</b><span class="small muted">${esc(r.drink_name)} · ${kr(r.price)}${r.available ? "" : " · <span style='color:var(--danger)'>unavailable</span>"}</span></div>
        <button class="btn sm" data-recipe="${r.id}" ${r.available ? "" : "disabled"}>Order</button><button class="x" title="Delete" data-del="${r.id}">✕</button></div>`).join("") || `<span class="muted small">Customise a drink and tap “Save as my drink” to order it in one touch next time.</span>`}</div></div>` : ""}
    <div class="section"><h2>Menu</h2><div class="grid">${menu.drinks.map((d) => `
      <div class="drink ${d.available ? "" : "soldout"}" data-id="${d.id}">
        ${!d.available ? `<span class="badge">Sold out</span>` : recIds.has(d.id) ? `<span class="badge rec">For you</span>` : ""}
        <div class="emoji">${EMOJI[d.name] || "☕"}</div><div class="name">${esc(d.name)}</div>
        <div class="desc">${d.available ? esc(d.description) : "Missing: " + d.missing.map(esc).join(", ")}</div>
        <div class="price">from ${kr(d.price)}</div></div>`).join("")}</div></div>`;
  $$(".drink").forEach((el) => (el.onclick = () => { const d = menu.drinks.find((x) => x.id == el.dataset.id); if (d.available) customise(d.id, d.default_options); else toast(`${d.name} is sold out`); }));
  $$(".rec").forEach((el) => (el.onclick = () => { const r = recs.recommendations.find((x) => x.drink_id == el.dataset.id); customise(r.drink_id, r.default_options); }));
  if (recipes) {
    $$("[data-recipe]").forEach((b) => (b.onclick = () => { const r = recipes.recipes.find((x) => x.id == b.dataset.recipe); checkout(r.drink_id, r.options, r.name); }));
    $$("[data-del]").forEach((b) => (b.onclick = async () => { await api(`/api/recipes/${b.dataset.del}`, { method: "DELETE" }); toast("Recipe deleted"); viewMenu(); }));
  }
  $("#nlForm").onsubmit = async (e) => { e.preventDefault();
    const out = $("#nlRes"); out.innerHTML = `<span class="muted small">Thinking…</span>`;
    try { const r = await api("/api/nl-order", { method: "POST", body: { text: $("#nlText").value } });
      if (!r.understood) { out.innerHTML = `<div class="err">${esc(r.message)}</div>`; return; }
      out.innerHTML = `<div class="panel"><b>${EMOJI[r.drink.name] || "☕"} ${esc(r.drink.name)}</b> – ${describe(r.options)} · <b>${kr(r.price)}</b>
        <div class="small muted" style="margin:4px 0 8px">Understood (${esc(r.source)}, ${Math.round(r.confidence * 100)}%): ${r.interpretation.map(esc).join(" · ")}</div>
        ${r.available ? "" : `<div class="err">Can't make it now: missing ${r.missing.map(esc).join(", ")}</div>`}
        <button class="btn sm green" id="nlOk" ${r.available ? "" : "disabled"}>Looks right – checkout</button> <button class="btn sm ghost" id="nlEdit">Adjust</button></div>`;
      $("#nlOk").onclick = () => checkout(r.drink.id, r.options);
      $("#nlEdit").onclick = () => customise(r.drink.id, r.options);
    } catch (err) { out.innerHTML = `<div class="err">${esc(err.message)}</div>`; } };
}
/**
 * Describe drink options in words, e.g. "large, oat milk, strong, hot".
 * @param {Object} o - Options (size, milk, strength, temperature, syrup, sugar).
 * @returns {string} HTML-escaped description.
 */
function describe(o) {
  const parts = [{ S: "small", M: "medium", L: "large" }[o.size]];
  if (o.milk !== "none") parts.push(o.milk.replace("_milk", " milk"));
  parts.push({ 1: "mild", 2: "normal strength", 3: "strong" }[o.strength], o.temperature.replace("_", " "));
  if (o.syrup !== "none") parts.push(o.syrup.replace("_syrup", " syrup"));
  if (o.sugar) parts.push(`sugar ×${o.sugar}`);
  return parts.map(esc).join(", ");
}

/* ----------------------------------------------------------- F3/F4 customise + F6 save recipe */
/**
 * F3/F4 - Open the customise dialog with a live price; out-of-stock options are disabled.
 * @param {number} drinkId - Drink to customise.
 * @param {Object} startOpts - Options to start from.
 * @returns {Promise<void>}
 */
async function customise(drinkId, startOpts) {
  const d = S.menu.drinks.find((x) => x.id === drinkId);
  const O = S.menu.options, stock = S.menu.option_stock;
  const opts = { ...d.default_options, ...startOpts };
  /**
   * Build a group of option buttons where one can be selected.
   * @param {string} key - Option name (e.g. "size").
   * @param {Array<[string,string]>} entries - [value, label] pairs.
   * @param {Function} [disabledFn] - Returns true for values that should be disabled.
   * @returns {string} HTML of the button group.
   */
  const seg = (key, entries, disabledFn = () => false) => `<div class="opt-group"><div class="lbl">${key[0].toUpperCase() + key.slice(1)}</div><div class="seg" data-key="${key}">
    ${entries.map(([v, l]) => `<button data-v="${v}" class="${String(opts[key]) === String(v) ? "on" : ""}" ${disabledFn(v) ? "disabled title='Out of stock'" : ""}>${l}</button>`).join("")}</div></div>`;
  const milkEntries = Object.entries(O.milk).filter(([v]) => !(d.milk_drink && v === "none"));
  openModal(`<div class="modal-head"><h2>${EMOJI[d.name] || "☕"} ${esc(d.name)}</h2><button class="close" onclick="closeModal()">×</button></div>
    <div class="muted small">${esc(d.description)}${d.milk_drink ? "" : " · adding milk gives a small splash"}</div>
    ${seg("size", [["S", "Small"], ["M", "Medium"], ["L", "Large +8"]])}
    ${seg("milk", milkEntries.map(([v, l]) => [v, l + (v === "oat_milk" || v === "almond_milk" ? " +5" : "")]), (v) => !stock.milk[v])}
    ${d.has_coffee ? seg("strength", [[1, "Mild"], [2, "Normal"], [3, "Strong +7"]]) : ""}
    ${seg("temperature", Object.entries(O.temperature))}
    ${seg("syrup", Object.entries(O.syrup).map(([v, l]) => [v, l + (v !== "none" ? " +5" : "")]), (v) => !stock.syrup[v])}
    ${seg("sugar", [[0, "None"], [1, "×1"], [2, "×2"], [3, "×3"]])}
    <div class="err" id="qErr"></div>
    <div class="total"><div><div class="small muted">Total</div><div class="p" id="qPrice">…</div></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">${S.user ? `<button class="btn ghost" id="saveBtn">Save as my drink</button>` : ""}<button class="btn green" id="buyBtn">Checkout</button></div></div>`);
  /**
   * Ask the server for the price and availability of the current options and update the dialog.
   * @returns {Promise<void>}
   */
  const update = async () => {
    try { const q = await api("/api/quote", { method: "POST", body: { drink_id: d.id, options: opts } });
      $("#qPrice").textContent = kr(q.price); $("#qErr").textContent = q.available ? "" : "Not enough " + q.missing.join(", ") + " for this combination";
      $("#buyBtn").disabled = !q.available; }
    catch (e) { $("#qErr").textContent = e.message; $("#buyBtn").disabled = true; } };
  $$(".seg").forEach((s) => s.addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b || b.disabled) return;
    opts[s.dataset.key] = isNaN(b.dataset.v) ? b.dataset.v : Number(b.dataset.v);
    $$("button", s).forEach((x) => x.classList.toggle("on", x === b)); update(); }));
  $("#buyBtn").onclick = () => checkout(d.id, { ...opts });
  if ($("#saveBtn")) $("#saveBtn").onclick = () => saveRecipe(d.id, { ...opts });
  update();
}
/**
 * F6 - Save the current drink under a name. If the 5-recipe limit is reached, ask whether to overwrite the oldest.
 * @param {number} drinkId - Drink id.
 * @param {Object} opts - Options to save.
 * @param {boolean} [overwrite=false] - Replace the oldest recipe when full.
 * @param {string|null} [presetName=null] - Name to use instead of asking the user.
 * @returns {Promise<void>}
 */
async function saveRecipe(drinkId, opts, overwrite = false, presetName = null) {
  const name = presetName ?? prompt("Name your drink (e.g. “Monday Fuel”)");
  if (!name) return;
  try { await api("/api/recipes", { method: "POST", body: { name, drink_id: drinkId, options: opts, overwrite_oldest: overwrite } });
    toast(`Saved “${name}” – it's now one tap away`); if (S.view === "menu") { closeModal(); viewMenu(); } }
  catch (e) {
    if (e.status === 409 && e.data.limit_reached) { if (confirm(e.message)) saveRecipe(drinkId, opts, true, name); }
    else toast(e.message);
  }
}

/* ----------------------------------------------------------- F8 checkout & payment */
/**
 * F8 - Open the payment dialog (card, digital wallet or work badge) and submit the order.
 * @param {number} drinkId - Drink id.
 * @param {Object} opts - Chosen options.
 * @param {string} [recipeName] - Saved-recipe name to display, if any.
 * @returns {Promise<void>}
 */
async function checkout(drinkId, opts, recipeName) {
  let q;
  try { q = await api("/api/quote", { method: "POST", body: { drink_id: drinkId, options: opts } }); } catch (e) { return toast(e.message); }
  const badge = S.user?.card_uid || "";
  openModal(`<div class="modal-head"><h2>Pay ${kr(q.price)}</h2><button class="close" onclick="closeModal()">×</button></div>
    <div class="small muted">${recipeName ? `⭐ ${esc(recipeName)} – ` : ""}${esc(q.drink.name)}: ${describe(q.options)}</div>
    <div class="tabs" style="margin-top:12px"><button class="tab active" data-m="card">💳 Card</button><button class="tab" data-m="wallet">📱 Digital wallet</button><button class="tab" data-m="badge">🪪 Work badge</button></div>
    <div id="payBody"></div><div class="err" id="pErr"></div>
    <button class="btn green block" id="payBtn">Pay ${kr(q.price)}</button>`);
  let method = "card";
  const bodyFor = {
    card: `<label>Card number<input id="cardNo" inputmode="numeric" placeholder="4242 4242 4242 4242" value="4242 4242 4242 4242"></label>`,
    wallet: `<div class="seg" id="wallets"><button class="on" data-w="apple_pay">Apple Pay</button><button data-w="google_pay">Google Pay</button><button data-w="swish">Swish</button></div>`,
    badge: `<div class="tapzone"><div class="big">🪪</div>Tap your prepaid work badge</div><label>Badge UID<input id="badgeUid" value="${esc(badge)}" placeholder="e.g. CARD-ALICE"></label>`,
  };
  /**
   * Switch the payment method tab and show its input fields.
   * @param {"card"|"wallet"|"badge"} m - Payment method.
   * @returns {void}
   */
  const setM = (m) => { method = m; $$(".tab[data-m]").forEach((t) => t.classList.toggle("active", t.dataset.m === m)); $("#payBody").innerHTML = bodyFor[m];
    if (m === "wallet") $$("#wallets button").forEach((b) => (b.onclick = () => $$("#wallets button").forEach((x) => x.classList.toggle("on", x === b)))); };
  $$(".tab[data-m]").forEach((t) => (t.onclick = () => setM(t.dataset.m)));
  setM("card");
  $("#payBtn").onclick = async () => {
    const payment = { method };
    if (method === "card") payment.card_number = $("#cardNo").value;
    if (method === "wallet") payment.wallet = $("#wallets .on").dataset.w;
    if (method === "badge") payment.badge_uid = $("#badgeUid").value;
    $("#payBtn").disabled = true; $("#pErr").textContent = "";
    try { const r = await api("/api/checkout", { method: "POST", body: { drink_id: drinkId, options: q.options, payment } }); brewing(r); }
    catch (e) { $("#pErr").textContent = e.message; $("#payBtn").disabled = false; }
  };
}

/* ----------------------------------------------------------- brewing + F9 receipts */
/**
 * F9 - Show the brewing progress, then the receipt options (e-mail or QR code).
 * @param {Object} r - Checkout response (order, brew_steps, receipt_default).
 * @returns {void}
 */
function brewing(r) {
  const o = r.order;
  openModal(`<div class="modal-head"><h2>Preparing your ${esc(o.drink_name)}</h2></div>
    <div class="small muted">Payment: ${esc(o.payment_label)} · ${o.payment_status === "approved" ? `✅ approved (${esc(o.transaction_id)})` : "🕓 stored offline, will settle when online"}</div>
    <div class="progress" style="margin-top:12px"><div id="prog"></div></div>
    <ul class="steps">${r.brew_steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
    <div id="rcpt" class="hidden"><h3>Get your receipt</h3>
      <div class="row2"><input id="rEmail" type="email" placeholder="you@example.com" value="${esc(S.user?.email || "")}"><button class="btn sm" id="rMail">✉ E-mail it</button></div>
      <button class="btn sm ghost" id="rQr" style="margin-top:8px">▦ Show QR code</button>
      <div id="rOut" style="margin-top:10px"></div>
      <button class="btn block" style="margin-top:12px" id="doneBtn">Done</button></div>`);
  const lis = $$(".steps li"); let i = 0;
  const tick = setInterval(() => { if (i < lis.length) { lis[i].classList.add("done"); i++; $("#prog").style.width = `${(100 * i) / lis.length}%`; }
    else { clearInterval(tick); $("#rcpt").classList.remove("hidden"); if (r.receipt_default === "qr") $("#rQr").click(); } }, 550);
  /**
   * Request a receipt for the current order and show the result.
   * @param {"email"|"qr"} method - How to deliver the receipt.
   * @returns {Promise<void>}
   */
  const get = async (method) => { try {
      const res = await api(`/api/orders/${o.id}/receipt`, { method: "POST", body: { method, email: $("#rEmail").value } });
      $("#rOut").innerHTML = method === "qr" ? `<div class="qr">${res.qr_svg}</div><div class="small muted">Scan to open your receipt · <a href="${esc(res.url)}" target="_blank">open link</a></div>`
        : `<div class="small">✅ Receipt sent to <b>${esc(res.sent_to)}</b> (see the outbox in the demo panel)</div><pre class="receipt">${esc(res.text)}</pre>`;
      refreshDemo(); } catch (e) { $("#rOut").innerHTML = `<div class="err">${esc(e.message)}</div>`; } };
  $("#rMail").onclick = () => get("email"); $("#rQr").onclick = () => get("qr");
  $("#doneBtn").onclick = async () => { closeModal(); await refreshState(); viewMenu(); refreshDemo(); };
}

/* ----------------------------------------------------------- F1 profile */
/**
 * F1 - Render the profile screen: edit name, card and preferences, and see recent orders.
 * @returns {Promise<void>}
 */
async function viewProfile() {
  const p = await api("/api/profile"); const u = p.user;
  $("#screen").innerHTML = `<div class="welcome"><div class="panel"><h2>My profile</h2><form id="pf">
      <div class="row2"><label>Name<input name="name" value="${esc(u.name)}"></label><label>Linked card UID<input name="card_uid" value="${esc(u.card_uid || "")}"></label></div>
      <div class="small muted">E-mail: ${esc(u.email)} · Badge balance: <b>${kr(u.badge_balance)}</b></div>
      <h3 style="margin-top:12px">Preferences</h3>${prefFields(u.preferences)}
      <button class="btn">Save changes</button></form></div>
    <div class="panel"><h2>Recent orders</h2>${p.recent_orders.length ? `<table><tr><th>#</th><th>Drink</th><th>Price</th><th>When</th></tr>
      ${p.recent_orders.map((o) => `<tr><td>${o.id}</td><td>${esc(o.drink_name)}</td><td>${kr(o.price)}</td><td>${esc(o.created_at.replace("T", " ").slice(0, 16))}</td></tr>`).join("")}</table>` : `<span class="muted">No orders yet.</span>`}</div></div>`;
  $("#pf").onsubmit = async (e) => { e.preventDefault(); const fd = Object.fromEntries(new FormData(e.target));
    try { await api("/api/profile", { method: "PUT", body: { name: fd.name, card_uid: fd.card_uid,
      preferences: { milk: fd.milk, strength: fd.strength, temperature: fd.temperature, size: fd.size, sugar: fd.sugar, receipt: fd.receipt } } });
      toast("Preferences saved"); await refreshState(); } catch (err) { toast(err.message); } };
}

/* ----------------------------------------------------------- F10 technician */
/**
 * F10 - Render the technician maintenance screen: waste bins, ingredient levels and open alerts.
 * @returns {Promise<void>}
 */
async function viewTech() {
  let d; try { d = await api("/api/tech/status"); } catch (e) { toast(e.message); return go("menu"); }
  /**
   * Build one row with a fill-level meter.
   * @param {string} label - Row label.
   * @param {number} pct - Fill percentage.
   * @param {string} state - ok, low, warning, empty or critical.
   * @param {string} extra - Text shown next to the meter.
   * @param {string} actions - HTML for the action buttons.
   * @returns {string} HTML of the row.
   */
  const bar = (label, pct, state, extra, actions) => `<div class="bar"><div>${esc(label)}</div><div class="meter ${state}"><div style="width:${Math.min(pct, 100)}%"></div></div><div class="small">${extra}</div><div>${actions}</div></div>`;
  $("#screen").innerHTML = `<h1>🔧 Maintenance</h1>
    <div class="ctx">Machine status: <b>${d.machine_status === "operational" ? "Operational" : "SUSPENDED – empty the full bin"}</b></div>
    <div class="panel section"><h2>Waste bins</h2><div class="bars">${d.bins.map((b) => bar(b.label, b.percent, b.state, `${b.level.toFixed(0)} / ${b.capacity} ${b.unit} (${b.percent}%)`,
      `<button class="btn sm ${b.state === "ok" ? "ghost" : "danger"}" data-empty="${b.name}">Empty &amp; confirm</button>`)).join("")}</div></div>
    <div class="panel section"><div style="display:flex;justify-content:space-between;align-items:center"><h2>Ingredients</h2><button class="btn sm" id="fillAll">Refill all &amp; reset trackers</button></div>
      <div class="bars">${d.ingredients.map((i) => bar(i.label, i.percent, i.state, `${i.level.toFixed(0)} / ${i.capacity} ${i.unit}`,
      `<input type="number" min="1" style="width:80px;padding:5px" placeholder="+${i.unit}" data-amt="${i.name}"> <button class="btn sm ghost" data-add="${i.name}">Add</button> <button class="btn sm" data-fill="${i.name}">Fill</button>`)).join("")}</div></div>
    <div class="panel"><h2>Open alerts</h2>${alertTable(d.open_alerts)}</div>`;
  $$("[data-empty]").forEach((b) => (b.onclick = async () => { const r = await api("/api/tech/empty-bin", { method: "POST", body: { bin: b.dataset.empty } });
    toast(r.machine_status === "operational" ? "Bin emptied – machine operational" : "Bin emptied"); await refreshState(); viewTech(); refreshDemo(); }));
  $$("[data-fill]").forEach((b) => (b.onclick = () => refill(b.dataset.fill)));
  $$("[data-add]").forEach((b) => (b.onclick = () => { const v = $(`[data-amt="${b.dataset.add}"]`).value; if (!v) return toast("Enter an amount"); refill(b.dataset.add, Number(v)); }));
  $("#fillAll").onclick = () => refill("all");
}
/**
 * F10 - Record a refill of one ingredient (a set amount or to capacity) or of all ingredients.
 * @param {string} ingredient - Ingredient name, or "all".
 * @param {number} [amount] - Amount to add; leave out to fill completely.
 * @returns {Promise<void>}
 */
async function refill(ingredient, amount) {
  try { await api("/api/tech/refill", { method: "POST", body: { ingredient, amount } }); toast("Refill recorded – inventory updated"); viewTech(); refreshDemo(); }
  catch (e) { toast(e.message); }
}
/**
 * Build the alerts/error-log table.
 * @param {Object[]} rows - Log entries.
 * @param {boolean} [admin=false] - Whether to show Resolve buttons.
 * @returns {string} HTML of the table.
 */
function alertTable(rows, admin = false) {
  if (!rows.length) return `<span class="muted">Nothing to report 🎉</span>`;
  return `<table><tr><th>Time</th><th>Severity</th><th>Component</th><th>Code</th><th>Message</th>${admin ? "<th></th>" : ""}</tr>
    ${rows.map((r) => `<tr style="${r.resolved ? "opacity:.5" : ""}"><td class="small">${esc(r.ts.replace("T", " ").slice(5, 19))}</td><td><span class="sev ${r.severity}">${r.severity}</span></td>
    <td>${esc(r.component)}</td><td><code>${esc(r.code)}</code></td><td>${esc(r.message)}</td>
    ${admin ? `<td>${r.resolved ? "resolved" : `<button class="btn sm ghost" data-resolve="${r.id}">Resolve</button>`}</td>` : ""}</tr>`).join("")}</table>`;
}

/* ----------------------------------------------------------- F10 admin diagnostics */
/**
 * F10 - Render the admin diagnostics dashboard: statistics and the filterable error history.
 * @param {Object} [filters] - Optional severity, component and open_only filters.
 * @returns {Promise<void>}
 */
async function viewAdmin(filters = {}) {
  let st, er;
  try { const qs = new URLSearchParams(filters).toString(); [st, er] = await Promise.all([api("/api/admin/stats"), api("/api/admin/errors?" + qs)]); }
  catch (e) { toast(e.message); return go("menu"); }
  const sum = er.summary;
  $("#screen").innerHTML = `<h1>📊 Diagnostics</h1>
    <div class="stats"><div class="stat"><div class="v">${st.orders}</div><div class="k">orders</div></div>
      <div class="stat"><div class="v">${kr(st.revenue)}</div><div class="k">revenue</div></div>
      <div class="stat"><div class="v">${st.pending_offline}</div><div class="k">offline payments pending</div></div>
      <div class="stat"><div class="v">${Object.values(sum.open_by_severity).reduce((a, b) => a + b, 0)}</div><div class="k">open issues</div></div>
      <div class="stat"><div class="v" style="font-size:15px">${st.top_drinks.slice(0, 3).map((t) => `${esc(t.drink_name)} (${t.n})`).join("<br>")}</div><div class="k">top drinks</div></div></div>
    <div class="panel section"><h2>Open issues by component</h2><div class="chips">${Object.entries(sum.open_by_component).map(([k, v]) => `<span class="chip">${esc(k)}: <b>${v}</b></span>`).join("") || `<span class="muted">none</span>`}</div></div>
    <div class="panel"><div style="display:flex;gap:8px;align-items:end;flex-wrap:wrap;margin-bottom:10px"><h2 style="flex:1">Error history</h2>
      <label>Severity<select id="fSev"><option value="">All</option>${["info", "warning", "error", "critical"].map((s) => `<option ${filters.severity === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>
      <label>Component<select id="fComp"><option value="">All</option>${["payment", "waste", "inventory", "security", "network", "weather", "maintenance", "heater", "grinder", "pump", "nfc"].map((s) => `<option ${filters.component === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>
      <label class="switch"><input type="checkbox" id="fOpen" ${filters.open_only ? "checked" : ""}> open only</label></div>
      ${alertTable(er.errors, true)}</div>`;
  /**
   * Re-render the admin dashboard using the selected filters.
   * @returns {Promise<void>}
   */
  const apply = () => viewAdmin({ ...($("#fSev").value && { severity: $("#fSev").value }), ...($("#fComp").value && { component: $("#fComp").value }), ...($("#fOpen").checked && { open_only: "1" }) });
  ["#fSev", "#fComp", "#fOpen"].forEach((s) => ($(s).onchange = apply));
  $$("[data-resolve]").forEach((b) => (b.onclick = async () => { await api(`/api/admin/errors/${b.dataset.resolve}/resolve`, { method: "POST" }); apply(); }));
}

/* ----------------------------------------------------------- demo panel */
/**
 * Reload the demo control panel (settings, sensors, cards and e-mail outbox) from the server.
 * @returns {Promise<void>}
 */
async function refreshDemo() {
  const d = await api("/api/demo/state"); const s = d.settings;
  $("#dNetwork").checked = s.network_online === "1"; $("#dWeather").checked = s.weather_service_online === "1";
  $("#dTemp").value = s.weather_temp_c; $("#dCond").value = s.weather_condition; $("#dHour").value = s.hour_override;
  $("#dPending").textContent = d.pending_offline ? `${d.pending_offline} offline payment(s) waiting to settle` : "";
  $("#dBins").innerHTML = d.bins.map((b) => `<label>${esc(b.label)} – <b>${b.percent}%</b> <span class="sev ${b.state === "ok" ? "info" : b.state}">${b.state}</span>
    <input type="range" min="0" max="100" value="${Math.min(100, b.percent)}" data-bin="${b.name}"></label>`).join("");
  $$("[data-bin]").forEach((r) => (r.onchange = async () => { await api("/api/demo/bin", { method: "POST", body: { bin: r.dataset.bin, percent: Number(r.value) } }); await afterDemoChange(); }));
  const cur = $("#dIng").value;
  $("#dIng").innerHTML = d.ingredients.map((i) => `<option value="${i.name}">${esc(i.label)} (${i.level.toFixed(0)} ${i.unit})</option>`).join("");
  if (cur) $("#dIng").value = cur;
  $("#dCards").innerHTML = "<b>NFC cards / badges:</b> " + d.cards.map((c) => `<code>${esc(c.card_uid)}</code> ${esc(c.role)}${c.badge_balance ? ` (${kr(c.badge_balance)})` : ""}`).join(" · ");
  $("#dOutbox").innerHTML = d.outbox.length ? d.outbox.map((m) => `<div class="mail"><b>To:</b> ${esc(m.recipient)}<br><b>${esc(m.subject)}</b><pre>${esc(m.body)}</pre></div>`).join("") : "No e-mails yet.";
}
/**
 * After a simulated change, refresh the header, the demo panel and the current screen.
 * @returns {Promise<void>}
 */
async function afterDemoChange() { await refreshState(); await refreshDemo(); if (["menu", "tech", "admin"].includes(S.view)) go(S.view); }
for (let h = 6; h <= 22; h++) $("#dHour").insertAdjacentHTML("beforeend", `<option value="${h}">${String(h).padStart(2, "0")}:00</option>`);
/**
 * Change a simulated setting (network, weather, time) and report any offline payments that were settled.
 * @param {Object} body - Settings to change.
 * @returns {Promise<void>}
 */
const setSetting = async (body) => { const r = await api("/api/demo/settings", { method: "POST", body }); if (r.settled) toast(`Network back – ${r.settled} offline payment(s) settled`); await afterDemoChange(); };
$("#dNetwork").onchange = (e) => setSetting({ network_online: e.target.checked });
$("#dWeather").onchange = (e) => setSetting({ weather_service_online: e.target.checked });
$("#dTemp").onchange = (e) => setSetting({ weather_temp_c: e.target.value });
$("#dCond").onchange = (e) => setSetting({ weather_condition: e.target.value });
$("#dHour").onchange = (e) => setSetting({ hour_override: e.target.value });
$("#dIngEmpty").onclick = async () => { await api("/api/demo/ingredient", { method: "POST", body: { name: $("#dIng").value, level: 0 } }); toast("Ingredient sensor now reads empty"); await afterDemoChange(); };
$$("[data-fault]").forEach((b) => (b.onclick = async () => { const r = await api("/api/demo/fault", { method: "POST", body: { component: b.dataset.fault } }); toast(`Fault injected: ${r.code}`); await afterDemoChange(); }));
$("#dReset").onclick = async () => { if (!confirm("Reset all demo data?")) return; await api("/api/demo/reset", { method: "POST" }); await refreshState(); await refreshDemo(); go("welcome"); toast("Demo data reset"); };

/* Bootstrap: load the session, machine state and demo panel, then open the right screen for the logged-in role. */
(async () => { await refreshState(); await refreshDemo();
  go(S.user ? (S.user.role === "technician" ? "tech" : S.user.role === "admin" ? "admin" : "menu") : S.guest ? "menu" : "welcome"); })();
