"""SQLite persistence layer: schema, seed data and small helpers."""
import json
import sqlite3
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    card_uid TEXT UNIQUE,
    role TEXT NOT NULL DEFAULT 'customer',          -- customer | technician | admin
    badge_balance REAL NOT NULL DEFAULT 0,          -- prepaid work-badge balance (kr)
    preferences TEXT NOT NULL DEFAULT '{}',         -- JSON: milk, strength, temperature, sugar, receipt
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingredients (
    name TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    unit TEXT NOT NULL,
    level REAL NOT NULL,
    capacity REAL NOT NULL,
    low_threshold REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS drinks (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    base_price REAL NOT NULL,
    recipe TEXT NOT NULL,          -- JSON {ingredient: amount}; "milk" is resolved to the chosen milk
    caffeine INTEGER NOT NULL      -- 0/1, used by the recommender
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    drink_id INTEGER NOT NULL,
    drink_name TEXT NOT NULL,
    options TEXT NOT NULL,
    price REAL NOT NULL,
    payment_method TEXT NOT NULL,
    payment_label TEXT,
    payment_status TEXT NOT NULL,   -- approved | pending_offline
    transaction_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
    token TEXT PRIMARY KEY,
    order_id INTEGER NOT NULL,
    method TEXT NOT NULL,           -- email | qr
    destination TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    drink_id INTEGER NOT NULL,
    options TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, name)
);
CREATE TABLE IF NOT EXISTS waste_bins (
    name TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    unit TEXT NOT NULL,
    level REAL NOT NULL,
    capacity REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS error_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    severity TEXT NOT NULL,         -- info | warning | error | critical
    component TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS offline_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    method TEXT NOT NULL,
    token TEXT NOT NULL,            -- tokenised payment reference, never the raw card number
    amount REAL NOT NULL,
    created_at TEXT NOT NULL,
    settled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

INGREDIENTS = [
    # name, label, unit, level, capacity, low threshold
    ("coffee_beans", "Coffee beans", "g", 1000, 1000, 150),
    ("water", "Water", "ml", 8000, 8000, 1000),
    ("whole_milk", "Whole milk", "ml", 3000, 3000, 400),
    ("oat_milk", "Oat milk", "ml", 2000, 2000, 300),
    ("almond_milk", "Almond milk", "ml", 1500, 1500, 300),
    ("chocolate", "Chocolate powder", "g", 600, 600, 80),
    ("chai", "Chai tea mix", "g", 300, 300, 40),
    ("vanilla_syrup", "Vanilla syrup", "ml", 400, 400, 60),
    ("caramel_syrup", "Caramel syrup", "ml", 400, 400, 60),
    ("hazelnut_syrup", "Hazelnut syrup", "ml", 400, 400, 60),
    ("sugar", "Sugar", "g", 600, 600, 60),
    ("cups", "Cups", "pcs", 80, 80, 10),
]

DRINKS = [
    (1, "Espresso", "Coffee", "A short, intense shot of coffee.", 28, {"coffee_beans": 18, "water": 40}, 1),
    (2, "Americano", "Coffee", "Espresso topped up with hot water.", 32, {"coffee_beans": 18, "water": 200}, 1),
    (3, "Cappuccino", "Milk coffee", "Espresso with steamed milk and thick foam.", 39, {"coffee_beans": 18, "water": 40, "milk": 150}, 1),
    (4, "Caffe Latte", "Milk coffee", "Espresso with lots of silky steamed milk.", 42, {"coffee_beans": 18, "water": 40, "milk": 250}, 1),
    (5, "Flat White", "Milk coffee", "Double ristretto with velvety micro-foam.", 42, {"coffee_beans": 22, "water": 40, "milk": 120}, 1),
    (6, "Mocha", "Milk coffee", "Espresso, chocolate and steamed milk.", 45, {"coffee_beans": 18, "water": 40, "milk": 200, "chocolate": 25}, 1),
    (7, "Hot Chocolate", "No coffee", "Rich chocolate with steamed milk.", 36, {"chocolate": 35, "milk": 250}, 0),
    (8, "Chai Latte", "No coffee", "Spiced black-tea chai with steamed milk.", 40, {"chai": 8, "water": 100, "milk": 180}, 1),
]

WASTE_BINS = [
    ("grounds", "Coffee grounds bin", "g", 0, 600),
    ("drip_tray", "Drip tray / waste water", "ml", 0, 1200),
]

DEFAULT_SETTINGS = {
    "network_online": "1",
    "weather_service_online": "1",
    "weather_temp_c": "6",
    "weather_condition": "Rainy",
    "hour_override": "",          # empty = use real clock
    "machine_status": "operational",
}


def now_iso():
    """Return the current local time as an ISO-8601 string (seconds precision).

    Args:
        None.

    Returns:
        str: Timestamp such as ``"2026-10-24T19:40:13"``.
    """
    return datetime.now().isoformat(timespec="seconds")


def connect(path):
    """Open a SQLite connection whose rows behave like dictionaries.

    Args:
        path (str): File path of the SQLite database.

    Returns:
        sqlite3.Connection: Connection with ``row_factory = sqlite3.Row`` and
        foreign keys enabled.
    """
    conn = sqlite3.connect(path, detect_types=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path, reset=False):
    """Create the database schema and load seed data if the database is empty.

    Args:
        path (str): File path of the SQLite database (created if missing).
        reset (bool): If True, drop every table first so the demo starts fresh.

    Returns:
        None.
    """
    conn = connect(path)
    if reset:
        for (tbl,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM drinks").fetchone()[0] == 0:
        _seed(conn)
    conn.commit()
    conn.close()


def _seed(conn):
    """Insert the initial demo data: ingredients, drinks, waste bins, settings,
    three users (customer, technician, admin) and some order history.

    Args:
        conn (sqlite3.Connection): Open connection to an empty database.

    Returns:
        None.
    """
    conn.executemany("INSERT INTO ingredients VALUES (?,?,?,?,?,?)", INGREDIENTS)
    conn.executemany(
        "INSERT INTO drinks VALUES (?,?,?,?,?,?,?)",
        [(i, n, c, d, p, json.dumps(r), caf) for (i, n, c, d, p, r, caf) in DRINKS],
    )
    conn.executemany("INSERT INTO waste_bins VALUES (?,?,?,?,?)", WASTE_BINS)
    conn.executemany("INSERT INTO settings VALUES (?,?)", DEFAULT_SETTINGS.items())

    ts = now_iso()
    users = [
        ("Alice Andersson", "alice@smartbrew.se", "coffee123", "CARD-ALICE", "customer", 150,
         {"milk": "oat_milk", "strength": 2, "temperature": "hot", "sugar": 0, "size": "M", "receipt": "email"}),
        ("Tom Technician", "tech@smartbrew.se", "tech123", "TECH-01", "technician", 0, {}),
        ("Ada Admin", "admin@smartbrew.se", "admin123", "ADMIN-01", "admin", 0, {}),
    ]
    for name, email, pw, card, role, bal, prefs in users:
        conn.execute(
            "INSERT INTO users (name,email,password_hash,card_uid,role,badge_balance,preferences,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (name, email, generate_password_hash(pw), card, role, bal, json.dumps(prefs), ts),
        )
    # Order history for Alice so the recommender has something to learn from.
    base = datetime.now() - timedelta(days=10)
    history = [4, 4, 5, 4, 6, 4, 5]
    for i, drink_id in enumerate(history):
        name = next(d[1] for d in DRINKS if d[0] == drink_id)
        conn.execute(
            "INSERT INTO orders (user_id,drink_id,drink_name,options,price,payment_method,payment_status,transaction_id,created_at)"
            " VALUES (1,?,?,?,?,?,?,?,?)",
            (drink_id, name, json.dumps({"milk": "oat_milk"}), 47, "badge", "approved", f"SEED-{i}",
             (base + timedelta(days=i)).isoformat(timespec="seconds")),
        )
    # A few anonymous orders give a popularity signal.
    for i, drink_id in enumerate([2, 2, 3, 1, 3, 7]):
        name = next(d[1] for d in DRINKS if d[0] == drink_id)
        conn.execute(
            "INSERT INTO orders (user_id,drink_id,drink_name,options,price,payment_method,payment_status,transaction_id,created_at)"
            " VALUES (NULL,?,?,?,?,?,?,?,?)",
            (drink_id, name, "{}", 35, "card", "approved", f"SEEDG-{i}", (base + timedelta(hours=i)).isoformat(timespec="seconds")),
        )


# ---------- settings helpers ----------
def get_setting(conn, key):
    """Read one machine/demo setting (e.g. network status, weather values).

    Args:
        conn (sqlite3.Connection): Open database connection.
        key (str): Setting name, e.g. ``"network_online"``.

    Returns:
        str | None: Stored value, or the default value if it is not stored.
    """
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else DEFAULT_SETTINGS.get(key)


def set_setting(conn, key, value):
    """Create or overwrite a machine/demo setting.

    Args:
        conn (sqlite3.Connection): Open database connection.
        key (str): Setting name.
        value (Any): New value; stored as a string.

    Returns:
        None.
    """
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def log_event(conn, severity, component, code, message):
    """Write an event to the diagnostic error log (R14). Events with severity
    ``info`` are audit records and are stored as already resolved.

    Args:
        conn (sqlite3.Connection): Open database connection.
        severity (str): One of ``info``, ``warning``, ``error``, ``critical``.
        component (str): Machine part that raised it, e.g. ``"payment"``.
        code (str): Short machine-readable code, e.g. ``"WST-FULL"``.
        message (str): Human-readable description.

    Returns:
        None.
    """
    conn.execute(
        "INSERT INTO error_log (ts,severity,component,code,message,resolved) VALUES (?,?,?,?,?,?)",
        (now_iso(), severity, component, code, message, 1 if severity == "info" else 0),  # info = audit trail, nothing to fix
    )
