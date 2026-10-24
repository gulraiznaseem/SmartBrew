"""SmartBrew Flask application – REST API + kiosk front-end."""
import json
import os
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, g, jsonify, request, send_from_directory, session, abort
from werkzeug.security import check_password_hash, generate_password_hash

from . import beverages, maintenance, nl_order, payments, recommender
from .db import connect, get_setting, init_db, log_event, now_iso, set_setting

MAX_FAILED_LOGINS = 3
LOCKOUT_SECONDS = 60
MAX_SAVED_RECIPES = 5
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def create_app(db_path=None, reset=False):
    """Application factory: create the Flask app, prepare the database and
    register every route of the REST API and the kiosk page.

    Args:
        db_path (str | None): SQLite file; defaults to env ``SMARTBREW_DB`` or
            ``smartbrew.db``.
        reset (bool): If True, wipe and re-seed the database.

    Returns:
        flask.Flask: The configured application.
    """
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
    app.config["SECRET_KEY"] = os.environ.get("SMARTBREW_SECRET", "dev-only-secret-change-me")
    app.config["DB_PATH"] = db_path or os.environ.get("SMARTBREW_DB", "smartbrew.db")
    init_db(app.config["DB_PATH"], reset=reset)

    # ------------------------------------------------------------------ plumbing
    def db():
        """Return the database connection for the current request, opening it on
        first use.

        Args:
            None.

        Returns:
            sqlite3.Connection: Connection stored on ``flask.g``.
        """
        if "db" not in g:
            g.db = connect(app.config["DB_PATH"])
        return g.db

    @app.teardown_appcontext
    def close_db(exc):
        """Commit (if no error occurred) and close the request's database
        connection when the request ends.

        Args:
            exc (BaseException | None): Error raised during the request, if any.

        Returns:
            None.
        """
        conn = g.pop("db", None)
        if conn is not None:
            if exc is None:
                conn.commit()
            conn.close()

    def current_user():
        """Load the logged-in user from the session.

        Args:
            None.

        Returns:
            dict | None: The user row, or None if nobody is logged in.
        """
        uid = session.get("user_id")
        if not uid:
            return None
        row = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(row) if row else None

    def prefs_of(user):
        """Decode a user's saved preferences.

        Args:
            user (dict | None): User row, or None.

        Returns:
            dict: Preferences (empty for a guest).
        """
        return json.loads(user["preferences"]) if user else {}

    def public_user(u):
        """Convert a user row to the safe JSON shape sent to the browser (no
        password hash).

        Args:
            u (dict | None): User row, or None.

        Returns:
            dict | None: id, name, email, role, card_uid, badge_balance, preferences.
        """
        return None if not u else {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"],
                                   "card_uid": u["card_uid"], "badge_balance": u["badge_balance"],
                                   "preferences": prefs_of(u)}

    def err(message, status=400, **extra):
        """Build a JSON error response.

        Args:
            message (str): Error message shown to the user.
            status (int): HTTP status code (default 400).
            **extra: Additional fields to include in the JSON body.

        Returns:
            tuple[flask.Response, int]: JSON body and status code.
        """
        return jsonify({"error": message, **extra}), status

    def require_role(*roles):
        """Decorator factory that protects a route by login and role (R02).
        Forbidden attempts are logged as security warnings.

        Args:
            *roles (str): Allowed roles; if empty, any logged-in user is allowed.

        Returns:
            Callable: A decorator. The wrapped view receives the user as its first argument.
        """
        def deco(fn):
            """Wrap a view function with the role check.

            Args:
                fn (Callable): The view function to protect.

            Returns:
                Callable: The protected view.
            """
            @wraps(fn)
            def wrapper(*a, **kw):
                """Run the role check, then call the protected view.

                Args:
                    *a, **kw: Route arguments passed on to the view.

                Returns:
                    flask.Response: The view's response, or a 401 or 403 JSON error.
                """
                u = current_user()
                if not u:
                    return err("Please log in first", 401)
                if roles and u["role"] not in roles:
                    log_event(db(), "warning", "security", "SEC-FORBIDDEN",
                              f"{u['email']} ({u['role']}) tried to access {request.path}")
                    db().commit()
                    return err("You do not have access to this function", 403)
                return fn(u, *a, **kw)
            return wrapper
        return deco

    def body():
        """Read the JSON body of the current request.

        Args:
            None.

        Returns:
            dict: Parsed JSON, or an empty dict if the body is missing or invalid.
        """
        return request.get_json(silent=True) or {}

    # ------------------------------------------------------------------ pages
    @app.get("/")
    def index():
        """GET / - serve the kiosk single-page user interface.

        Args:
            None.

        Returns:
            flask.Response: ``static/index.html``.
        """
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/api/state")
    def state():
        """GET /api/state - session and machine status used by the UI header.

        Args:
            None.

        Returns:
            flask.Response: JSON with user, guest, machine_status and network_online.
        """
        conn = db()
        return jsonify({
            "user": public_user(current_user()),
            "guest": session.get("guest", False),
            "machine_status": maintenance.machine_status(conn),
            "network_online": get_setting(conn, "network_online") == "1",
        })

    # ------------------------------------------------------------------ F1 accounts & preferences
    @app.post("/api/auth/register")
    def register():
        """POST /api/auth/register - create a customer account with preferences (F1, R01).

        Args:
            JSON body: name (str), email (str), password (str, min 6),
            card_uid (str, optional), preferences (dict, optional).

        Returns:
            flask.Response: 201 with the new user (who is logged in automatically);
            400 for invalid input; 409 if the e-mail or card is already used.
        """
        d, conn = body(), db()
        name, email, pw = (d.get("name") or "").strip(), (d.get("email") or "").strip().lower(), d.get("password") or ""
        card = (d.get("card_uid") or "").strip() or None
        if len(name) < 2:
            return err("Please enter your name")
        if "@" not in email or "." not in email.split("@")[-1]:
            return err("Please enter a valid e-mail address")
        if len(pw) < 6:
            return err("Password must be at least 6 characters")
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            return err("An account with that e-mail already exists", 409)
        if card and conn.execute("SELECT 1 FROM users WHERE card_uid=?", (card,)).fetchone():
            return err("That card is already linked to another account", 409)
        prefs = sanitise_prefs(d.get("preferences") or {})
        cur = conn.execute(
            "INSERT INTO users (name,email,password_hash,card_uid,role,badge_balance,preferences,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (name, email, generate_password_hash(pw), card, "customer", 0, json.dumps(prefs), now_iso()))
        session.clear()
        session["user_id"] = cur.lastrowid
        conn.commit()
        return jsonify({"user": public_user(current_user())}), 201

    def sanitise_prefs(p):
        """Keep only valid preference values (milk, strength, temperature, size,
        sugar, receipt) and drop everything else.

        Args:
            p (dict): Preferences sent by the client.

        Returns:
            dict: Cleaned preferences.
        """
        out = {}
        if p.get("milk") in beverages.MILKS:
            out["milk"] = p["milk"]
        if str(p.get("strength", "")) in ("1", "2", "3"):
            out["strength"] = int(p["strength"])
        if p.get("temperature") in beverages.TEMPERATURES:
            out["temperature"] = p["temperature"]
        if p.get("size") in beverages.SIZES:
            out["size"] = p["size"]
        if str(p.get("sugar", "")) in ("0", "1", "2", "3"):
            out["sugar"] = int(p["sugar"])
        if p.get("receipt") in ("email", "qr"):
            out["receipt"] = p["receipt"]
        return out

    @app.get("/api/profile")
    @require_role()
    def get_profile(u):
        """GET /api/profile - the logged-in user's profile and last 10 orders (F1).

        Args:
            u (dict): Logged-in user (injected by ``require_role``).

        Returns:
            flask.Response: JSON with user and recent_orders.
        """
        orders = [dict(r) for r in db().execute(
            "SELECT id, drink_name, price, payment_status, created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (u["id"],))]
        return jsonify({"user": public_user(u), "recent_orders": orders})

    @app.put("/api/profile")
    @require_role()
    def update_profile(u):
        """PUT /api/profile - update name, linked card and preferences (F1).

        Args:
            u (dict): Logged-in user (injected by ``require_role``).
            JSON body: name, card_uid, preferences (all optional).

        Returns:
            flask.Response: The updated user; 409 if the card is linked to someone else.
        """
        d, conn = body(), db()
        prefs = {**prefs_of(u), **sanitise_prefs(d.get("preferences") or {})}
        name = (d.get("name") or u["name"]).strip()
        card = d.get("card_uid", u["card_uid"])
        card = (card or "").strip() or None
        if card and conn.execute("SELECT 1 FROM users WHERE card_uid=? AND id<>?", (card, u["id"])).fetchone():
            return err("That card is already linked to another account", 409)
        conn.execute("UPDATE users SET name=?, card_uid=?, preferences=? WHERE id=?", (name, card, json.dumps(prefs), u["id"]))
        conn.commit()
        return jsonify({"user": public_user(current_user())})

    # ------------------------------------------------------------------ F2 authentication
    @app.post("/api/auth/login")
    def login():
        """POST /api/auth/login - log in by password or NFC card tap (F2, R02).
        Three wrong passwords lock the account for 60 seconds.

        Args:
            JSON body: method (``"password"`` or ``"card"``), then email and
            password, or card_uid.

        Returns:
            flask.Response: The user on success; 401 for wrong credentials or an
            unknown card; 423 while the account is locked.
        """
        d, conn = body(), db()
        method = d.get("method", "password")
        if method == "card":
            uid = (d.get("card_uid") or "").strip()
            row = conn.execute("SELECT * FROM users WHERE card_uid=?", (uid,)).fetchone()
            if not row:
                log_event(conn, "warning", "security", "AUTH-CARD", f"Unknown card tapped ({uid or 'empty'})")
                conn.commit()
                return err("Card not recognised – please re-tap or log in with your password", 401)
            user = row
        else:
            email = (d.get("email") or "").strip().lower()
            row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if row and row["locked_until"] and datetime.fromisoformat(row["locked_until"]) > datetime.now():
                secs = int((datetime.fromisoformat(row["locked_until"]) - datetime.now()).total_seconds()) + 1
                return err(f"Account temporarily locked after too many attempts. Try again in {secs}s.", 423, locked_seconds=secs)
            if not row or not check_password_hash(row["password_hash"], d.get("password") or ""):
                if row:
                    fails = row["failed_logins"] + 1
                    locked = (datetime.now() + timedelta(seconds=LOCKOUT_SECONDS)).isoformat() if fails >= MAX_FAILED_LOGINS else None
                    conn.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?",
                                 (0 if locked else fails, locked, row["id"]))
                    if locked:
                        log_event(conn, "warning", "security", "AUTH-LOCK", f"{email} locked for {LOCKOUT_SECONDS}s after {fails} failed logins")
                conn.commit()
                return err("Wrong e-mail or password", 401)
            user = row
        conn.execute("UPDATE users SET failed_logins=0, locked_until=NULL WHERE id=?", (user["id"],))
        conn.commit()
        session.clear()
        session["user_id"] = user["id"]
        return jsonify({"user": public_user(current_user())})

    @app.post("/api/auth/guest")
    def guest():
        """POST /api/auth/guest - continue without an account.

        Args:
            None.

        Returns:
            flask.Response: ``{"guest": true}``.
        """
        session.clear()
        session["guest"] = True
        return jsonify({"guest": True})

    @app.post("/api/auth/logout")
    def logout():
        """POST /api/auth/logout - end the session.

        Args:
            None.

        Returns:
            flask.Response: ``{"ok": true}``.
        """
        session.clear()
        return jsonify({"ok": True})

    # ------------------------------------------------------------------ F3 menu / customise, F4 availability
    @app.get("/api/menu")
    def get_menu():
        """GET /api/menu - the drink menu with availability and options (F3, F4).

        Args:
            None (uses the session user's preferences for defaults).

        Returns:
            flask.Response: JSON from :func:`beverages.menu` plus machine_status.
        """
        conn = db()
        data = beverages.menu(conn, prefs_of(current_user()))
        data["machine_status"] = maintenance.machine_status(conn)
        return jsonify(data)

    @app.post("/api/quote")
    def get_quote():
        """POST /api/quote - price and availability of a customised drink (F3, F4).

        Args:
            JSON body: drink_id (int), options (dict).

        Returns:
            flask.Response: JSON from :func:`beverages.quote`; 400 for invalid options.
        """
        d = body()
        try:
            return jsonify(beverages.quote(db(), int(d.get("drink_id", 0)), d.get("options")))
        except (beverages.OptionError, ValueError) as e:
            return err(str(e))

    # ------------------------------------------------------------------ F5 recommendations
    @app.get("/api/recommendations")
    def get_recs():
        """GET /api/recommendations - personalised drink suggestions (F5, R05).

        Args:
            None (uses the session user, if any).

        Returns:
            flask.Response: JSON from :func:`recommender.recommend`.
        """
        u = current_user()
        conn = db()
        res = recommender.recommend(conn, u, prefs_of(u))
        conn.commit()
        return jsonify(res)

    # ------------------------------------------------------------------ F6 custom recipes
    @app.get("/api/recipes")
    @require_role("customer", "technician", "admin")
    def list_recipes(u):
        """GET /api/recipes - the user's saved custom drinks with current price and
        availability (F6).

        Args:
            u (dict): Logged-in user (injected by ``require_role``).

        Returns:
            flask.Response: JSON with recipes (list) and limit (int).
        """
        conn = db()
        out = []
        for r in conn.execute("SELECT * FROM recipes WHERE user_id=? ORDER BY created_at DESC, id DESC", (u["id"],)):
            opts = json.loads(r["options"])
            q = beverages.quote(conn, r["drink_id"], opts)
            out.append({"id": r["id"], "name": r["name"], "drink_id": r["drink_id"], "drink_name": q["drink"]["name"],
                        "options": opts, "price": q["price"], "available": q["available"], "missing": q["missing"]})
        return jsonify({"recipes": out, "limit": MAX_SAVED_RECIPES})

    @app.post("/api/recipes")
    @require_role("customer", "technician", "admin")
    def save_recipe(u):
        """POST /api/recipes - name and save a custom drink (F6, R07). Saving with
        an existing name updates that recipe. When the limit of 5 is reached,
        the client must confirm overwriting the oldest one.

        Args:
            u (dict): Logged-in user (injected by ``require_role``).
            JSON body: name (str, 2-30 chars), drink_id (int), options (dict),
            overwrite_oldest (bool, optional).

        Returns:
            flask.Response: 201 when created, 200 when updated, 400 for invalid
            input, 409 with ``limit_reached`` when the limit is reached.
        """
        d, conn = body(), db()
        name = (d.get("name") or "").strip()
        if not 2 <= len(name) <= 30:
            return err("Recipe name must be 2–30 characters")
        try:
            drink = beverages.get_drink(conn, int(d.get("drink_id", 0)))
            opts = beverages.normalise_options(drink, d.get("options"))
        except (beverages.OptionError, ValueError) as e:
            return err(str(e))
        existing = conn.execute("SELECT id FROM recipes WHERE user_id=? AND name=?", (u["id"], name)).fetchone()
        if existing:
            conn.execute("UPDATE recipes SET drink_id=?, options=?, created_at=? WHERE id=?",
                         (drink["id"], json.dumps(opts), now_iso(), existing["id"]))
            conn.commit()
            return jsonify({"saved": True, "updated": True, "id": existing["id"]})
        count = conn.execute("SELECT COUNT(*) FROM recipes WHERE user_id=?", (u["id"],)).fetchone()[0]
        if count >= MAX_SAVED_RECIPES:
            oldest = conn.execute("SELECT id, name FROM recipes WHERE user_id=? ORDER BY created_at, id LIMIT 1", (u["id"],)).fetchone()
            if not d.get("overwrite_oldest"):
                return err(f"You can save at most {MAX_SAVED_RECIPES} recipes. Overwrite “{oldest['name']}”?", 409,
                           limit_reached=True, oldest=oldest["name"])
            conn.execute("DELETE FROM recipes WHERE id=?", (oldest["id"],))
        cur = conn.execute("INSERT INTO recipes (user_id,name,drink_id,options,created_at) VALUES (?,?,?,?,?)",
                           (u["id"], name, drink["id"], json.dumps(opts), now_iso()))
        conn.commit()
        return jsonify({"saved": True, "id": cur.lastrowid}), 201

    @app.delete("/api/recipes/<int:rid>")
    @require_role("customer", "technician", "admin")
    def delete_recipe(u, rid):
        """DELETE /api/recipes/<rid> - delete one of the user's saved drinks (F6).

        Args:
            u (dict): Logged-in user (injected by ``require_role``).
            rid (int): Recipe id from the URL.

        Returns:
            flask.Response: ``{"deleted": true}``, or 404 if not found.
        """
        n = db().execute("DELETE FROM recipes WHERE id=? AND user_id=?", (rid, u["id"])).rowcount
        return jsonify({"deleted": bool(n)}) if n else err("Recipe not found", 404)

    # ------------------------------------------------------------------ F7 natural-language request
    @app.post("/api/nl-order")
    def nl():
        """POST /api/nl-order - interpret a natural-language drink request (F7, R17).

        Args:
            JSON body: text (str), e.g. "a large oat latte with vanilla".

        Returns:
            flask.Response: JSON from :func:`nl_order.interpret`; 400 if the text is empty.
        """
        try:
            return jsonify(nl_order.interpret(db(), body().get("text", ""), prefs_of(current_user())))
        except beverages.OptionError as e:
            return err(str(e))

    # ------------------------------------------------------------------ F8 ordering + payment
    @app.post("/api/checkout")
    def checkout():
        """POST /api/checkout - pay for and brew a drink (F8; R08, R09, R16).
        Checks that the machine is operational and the drink is in stock,
        authorises the payment, records the order (queuing it if offline),
        consumes the ingredients and adds the waste to the bins.

        Args:
            JSON body: drink_id (int), options (dict), payment (dict with method
            and card_number, wallet or badge_uid).

        Returns:
            flask.Response: 201 with order, brew_steps, receipt_default and
            machine_status. Errors: 401 (not logged in or guest), 409 (sold out),
            402 (payment refused), 503 (machine suspended).
        """
        d, conn = body(), db()
        u = current_user()
        if not u and not session.get("guest"):
            return err("Log in or continue as guest first", 401)
        if maintenance.machine_status(conn) == "suspended":
            return err("Machine temporarily out of service – a waste bin is full. A technician has been alerted.", 503)
        try:
            q = beverages.quote(conn, int(d.get("drink_id", 0)), d.get("options"))
        except (beverages.OptionError, ValueError) as e:
            return err(str(e))
        if not q["available"]:
            return err("Sorry, this drink is sold out: missing " + ", ".join(q["missing"]), 409, missing=q["missing"])
        pay = d.get("payment") or {}
        method = pay.get("method")
        try:
            status, txn, label = payments.authorise(conn, method, q["price"], pay)
        except payments.PaymentError as e:
            conn.commit()
            return err(str(e), 402, code=e.code)
        cur = conn.execute(
            "INSERT INTO orders (user_id,drink_id,drink_name,options,price,payment_method,payment_label,payment_status,transaction_id,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (u["id"] if u else None, q["drink"]["id"], q["drink"]["name"], json.dumps(q["options"]), q["price"],
             method, label, status, txn, now_iso()))
        order_id = cur.lastrowid
        if status == "pending_offline":
            conn.execute("INSERT INTO offline_queue (order_id,method,token,amount,created_at) VALUES (?,?,?,?,?)",
                         (order_id, method, txn, q["price"], now_iso()))
        beverages.consume(conn, q["consumption"])
        maintenance.check_low_stock(conn, q["consumption"])
        maintenance.record_brew_waste(conn, q["consumption"])
        conn.commit()
        return jsonify({"order": load_order(order_id),
                        "brew_steps": brew_steps(q),
                        "receipt_default": prefs_of(u).get("receipt") if u else None,
                        "machine_status": maintenance.machine_status(conn)}), 201

    def brew_steps(q):
        """Describe the brewing steps shown in the progress animation.

        Args:
            q (dict): Quote of the ordered drink (options and consumption).

        Returns:
            list[str]: Human-readable steps, ending with "ready".
        """
        o = q["options"]
        steps = []
        if q["consumption"].get("coffee_beans"):
            steps.append(f"Grinding {q['consumption']['coffee_beans']:.0f} g of beans")
            steps.append("Extracting espresso at 9 bar")
        if q["consumption"].get("chai"):
            steps.append("Brewing spiced chai")
        if q["consumption"].get("chocolate"):
            steps.append("Mixing chocolate")
        milk = next((m for m in ("whole_milk", "oat_milk", "almond_milk") if m in q["consumption"]), None)
        if milk:
            steps.append(f"Steaming {q['consumption'][milk]:.0f} ml {beverages.MILKS[milk].lower()} to {beverages.TEMPERATURES[o['temperature']].split('(')[1].rstrip(')')}")
        if o["syrup"] != "none":
            steps.append(f"Adding {beverages.SYRUPS[o['syrup']].lower()} syrup")
        if o["sugar"]:
            steps.append(f"Adding sugar ×{o['sugar']}")
        steps.append("Your drink is ready – enjoy!")
        return steps

    def load_order(order_id):
        """Load one order with its options decoded.

        Args:
            order_id (int): Id of the order.

        Returns:
            dict | None: The order, or None if it does not exist.
        """
        r = db().execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not r:
            return None
        o = dict(r)
        o["options"] = json.loads(o["options"])
        o["payment_label"] = o.get("payment_label") or o["payment_method"]
        return o

    # ------------------------------------------------------------------ F9 receipts
    @app.post("/api/orders/<int:oid>/receipt")
    def receipt(oid):
        """POST /api/orders/<oid>/receipt - send a receipt by e-mail or return a
        QR code (F9, R10). Only the order's owner (or anyone, for a guest order)
        may request it.

        Args:
            oid (int): Order id from the URL.
            JSON body: method (``"email"`` or ``"qr"``), email (optional).

        Returns:
            flask.Response: JSON from :func:`payments.issue_receipt`; 404, 403 or
            400 on error.
        """
        d, conn = body(), db()
        order = load_order(oid)
        if not order:
            return err("Order not found", 404)
        u = current_user()
        if order["user_id"] and (not u or u["id"] != order["user_id"]):
            return err("This is not your order", 403)
        email = d.get("email") or (u["email"] if u else None)
        try:
            res = payments.issue_receipt(conn, order, d.get("method"), email, request.host_url)
        except payments.PaymentError as e:
            return err(str(e))
        conn.commit()
        return jsonify(res)

    @app.get("/receipt/<token>")
    def receipt_page(token):
        """GET /receipt/<token> - the web page a receipt QR code links to.

        Args:
            token (str): Receipt token from the URL.

        Returns:
            str: An HTML page with the receipt; 404 if the token is unknown.
        """
        r = db().execute("SELECT order_id FROM receipts WHERE token=?", (token,)).fetchone()
        if not r:
            abort(404)
        text = payments.receipt_text(load_order(r["order_id"]))
        esc = text.replace("&", "&amp;").replace("<", "&lt;")
        return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                f"<title>SmartBrew receipt</title><body style='font-family:system-ui;background:#f6f1ea;padding:24px'>"
                f"<pre style='background:#fff;padding:20px;border-radius:12px;max-width:520px;white-space:pre-wrap'>{esc}</pre>")

    @app.get("/api/orders")
    @require_role()
    def my_orders(u):
        """GET /api/orders - the logged-in user's last 20 orders.

        Args:
            u (dict): Logged-in user (injected by ``require_role``).

        Returns:
            flask.Response: JSON with orders (list).
        """
        return jsonify({"orders": [load_order(r["id"]) for r in db().execute(
            "SELECT id FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 20", (u["id"],))]})

    # ------------------------------------------------------------------ F10 technician / admin
    @app.get("/api/tech/status")
    @require_role("technician", "admin")
    def tech_status(u):
        """GET /api/tech/status - maintenance dashboard data (F10). Technician or
        admin only.

        Args:
            u (dict): Logged-in technician or admin.

        Returns:
            flask.Response: JSON with ingredients, bins, machine_status and open_alerts.
        """
        conn = db()
        return jsonify({"ingredients": maintenance.ingredient_status(conn), "bins": maintenance.bins(conn),
                        "machine_status": maintenance.machine_status(conn),
                        "open_alerts": maintenance.error_log(conn, include_resolved=False, limit=10)})

    @app.post("/api/tech/refill")
    @require_role("technician", "admin")
    def tech_refill(u):
        """POST /api/tech/refill - record a refill of one ingredient or of all of
        them (F10, R11).

        Args:
            u (dict): Logged-in technician or admin.
            JSON body: ingredient (str or ``"all"``), amount (float, optional;
            omit to fill to capacity).

        Returns:
            flask.Response: JSON with the refill result and the updated
            ingredients; 400 for an invalid ingredient or amount.
        """
        d, conn = body(), db()
        try:
            if d.get("ingredient") == "all":
                res = maintenance.refill_all(conn, u["name"])
            else:
                res = maintenance.refill(conn, d.get("ingredient"), d.get("amount"), u["name"])
        except ValueError as e:
            return err(str(e))
        conn.commit()
        return jsonify({"result": res, "ingredients": maintenance.ingredient_status(conn)})

    @app.post("/api/tech/empty-bin")
    @require_role("technician", "admin")
    def tech_empty(u):
        """POST /api/tech/empty-bin - confirm that a waste bin has been emptied
        (F10, R12, R13). Resumes the machine if it was suspended.

        Args:
            u (dict): Logged-in technician or admin.
            JSON body: bin (``"grounds"`` or ``"drip_tray"``).

        Returns:
            flask.Response: JSON with bins and machine_status; 400 for an unknown bin.
        """
        conn = db()
        try:
            res = maintenance.empty_bin(conn, body().get("bin"), u["name"])
        except ValueError as e:
            return err(str(e))
        conn.execute("UPDATE error_log SET resolved=1 WHERE component='waste' AND resolved=0 AND code IN ('WST-WARN','WST-FULL')")
        conn.commit()
        return jsonify({"bins": res, "machine_status": maintenance.machine_status(conn)})

    @app.get("/api/admin/errors")
    @require_role("admin")
    def admin_errors(u):
        """GET /api/admin/errors - the error history with filters (F10, R14). Admin only.

        Args:
            u (dict): Logged-in admin.
            Query string: severity, component, open_only=1 (all optional).

        Returns:
            flask.Response: JSON with errors (list) and summary (counts).
        """
        conn = db()
        a = request.args
        return jsonify({"errors": maintenance.error_log(conn, a.get("severity") or None, a.get("component") or None,
                                                        a.get("open_only") != "1"),
                        "summary": maintenance.error_summary(conn)})

    @app.post("/api/admin/errors/<int:eid>/resolve")
    @require_role("admin")
    def admin_resolve(u, eid):
        """POST /api/admin/errors/<eid>/resolve - mark an issue as resolved. Admin only.

        Args:
            u (dict): Logged-in admin.
            eid (int): Log entry id from the URL.

        Returns:
            flask.Response: ``{"resolved": bool}``.
        """
        n = db().execute("UPDATE error_log SET resolved=1 WHERE id=?", (eid,)).rowcount
        return jsonify({"resolved": bool(n)})

    @app.get("/api/admin/stats")
    @require_role("admin")
    def admin_stats(u):
        """GET /api/admin/stats - sales and status figures for the admin dashboard.

        Args:
            u (dict): Logged-in admin.

        Returns:
            flask.Response: JSON with orders, revenue, top_drinks, pending_offline
            and machine_status.
        """
        conn = db()
        r = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(price),0) revenue FROM orders").fetchone()
        top = [dict(x) for x in conn.execute("SELECT drink_name, COUNT(*) n FROM orders GROUP BY drink_name ORDER BY n DESC LIMIT 5")]
        pending = conn.execute("SELECT COUNT(*) FROM offline_queue WHERE settled=0").fetchone()[0]
        return jsonify({"orders": r["n"], "revenue": r["revenue"], "top_drinks": top, "pending_offline": pending,
                        "machine_status": maintenance.machine_status(conn)})

    # ------------------------------------------------------------------ demo / simulation panel
    @app.get("/api/demo/state")
    def demo_state():
        """GET /api/demo/state - data for the demo control panel (simulated sensors,
        settings, e-mail outbox and test cards).

        Args:
            None.

        Returns:
            flask.Response: JSON with settings, bins, ingredients, outbox,
            pending_offline, test_cards and cards.
        """
        conn = db()
        return jsonify({
            "settings": {k: get_setting(conn, k) for k in ("network_online", "weather_service_online", "weather_temp_c",
                                                           "weather_condition", "hour_override", "machine_status")},
            "bins": maintenance.bins(conn),
            "ingredients": maintenance.ingredient_status(conn),
            "outbox": [dict(r) for r in conn.execute("SELECT * FROM outbox ORDER BY id DESC LIMIT 5")],
            "pending_offline": conn.execute("SELECT COUNT(*) FROM offline_queue WHERE settled=0").fetchone()[0],
            "test_cards": {"approve": "4242 4242 4242 4242", "decline": "4000 0000 0000 0002"},
            "cards": [dict(r) for r in conn.execute("SELECT name, role, card_uid, badge_balance FROM users WHERE card_uid IS NOT NULL")],
        })

    @app.post("/api/demo/settings")
    def demo_settings():
        """POST /api/demo/settings - change the simulated network, weather service,
        weather and time of day. When the network comes back, offline payments
        are settled automatically.

        Args:
            JSON body: any of network_online (bool), weather_service_online (bool),
            weather_temp_c, weather_condition, hour_override.

        Returns:
            flask.Response: ``{"ok": true, "settled": int}``.
        """
        d, conn = body(), db()
        was_online = get_setting(conn, "network_online") == "1"
        for k in ("network_online", "weather_service_online", "weather_temp_c", "weather_condition", "hour_override"):
            if k in d:
                v = d[k]
                if isinstance(v, bool):
                    v = "1" if v else "0"
                set_setting(conn, k, "" if v is None else v)
        settled = 0
        now_online = get_setting(conn, "network_online") == "1"
        if not was_online and now_online:
            settled = payments.settle_offline_queue(conn)
        elif was_online and not now_online:
            log_event(conn, "error", "network", "NET-DOWN", "Network connection lost – switching to offline mode")
        conn.commit()
        return jsonify({"ok": True, "settled": settled})

    @app.post("/api/demo/bin")
    def demo_bin():
        """POST /api/demo/bin - set a simulated waste-bin sensor to a fill
        percentage and re-evaluate the shutdown rule.

        Args:
            JSON body: bin (str), percent (0-100).

        Returns:
            flask.Response: JSON with bins and machine_status; 400 for an unknown bin.
        """
        d, conn = body(), db()
        row = conn.execute("SELECT capacity FROM waste_bins WHERE name=?", (d.get("bin"),)).fetchone()
        if not row:
            return err("Unknown bin")
        conn.execute("UPDATE waste_bins SET level=? WHERE name=?", (row["capacity"] * float(d.get("percent", 0)) / 100, d["bin"]))
        maintenance.evaluate_bins(conn)
        conn.commit()
        return jsonify({"bins": maintenance.bins(conn), "machine_status": maintenance.machine_status(conn)})

    @app.post("/api/demo/ingredient")
    def demo_ingredient():
        """POST /api/demo/ingredient - set a simulated ingredient level (e.g. 0 to
        show the sold-out lock).

        Args:
            JSON body: name (str), level (float).

        Returns:
            flask.Response: JSON with ingredients; 400 for an unknown ingredient.
        """
        d, conn = body(), db()
        n = conn.execute("UPDATE ingredients SET level=? WHERE name=?", (float(d.get("level", 0)), d.get("name"))).rowcount
        if not n:
            return err("Unknown ingredient")
        if float(d.get("level", 0)) == 0:
            label = conn.execute("SELECT label FROM ingredients WHERE name=?", (d.get("name"),)).fetchone()["label"]
            log_event(conn, "error", "inventory", "INV-EMPTY", f"Sensor reports {label} empty")
        conn.commit()
        return jsonify({"ingredients": maintenance.ingredient_status(conn)})

    @app.post("/api/demo/fault")
    def demo_fault():
        """POST /api/demo/fault - inject a simulated hardware fault into the error log.

        Args:
            JSON body: component (``heater``, ``grinder``, ``pump`` or ``nfc``).

        Returns:
            flask.Response: JSON with severity, code and message; 400 if unknown.
        """
        conn = db()
        try:
            res = maintenance.simulate_fault(conn, body().get("component"))
        except ValueError as e:
            return err(str(e))
        conn.commit()
        return jsonify(res)

    @app.post("/api/demo/reset")
    def demo_reset():
        """POST /api/demo/reset - wipe and re-seed the database and log everyone out.

        Args:
            None.

        Returns:
            flask.Response: ``{"ok": true}``.
        """
        g.pop("db").close() if "db" in g else None
        init_db(app.config["DB_PATH"], reset=True)
        session.clear()
        return jsonify({"ok": True})

    return app


def main():
    """Command-line entry point: parse ``--host``, ``--port`` and ``--reset``
    and start the web server.

    Args:
        None (reads command-line arguments).

    Returns:
        None. Runs until stopped with Ctrl+C.
    """
    import argparse
    p = argparse.ArgumentParser(description="Run the SmartBrew prototype")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5001)
    p.add_argument("--reset", action="store_true", help="wipe and re-seed the database")
    a = p.parse_args()
    app = create_app(reset=a.reset)
    print(f"\n  SmartBrew is running →  http://{a.host}:{a.port}\n")
    app.run(host=a.host, port=a.port, debug=False)


if __name__ == "__main__":
    main()
