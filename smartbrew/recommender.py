"""Context-aware drink recommendations (R05 / US04 / US05).

Score = personal history + time of day + weather + saved preferences + overall popularity.
Sold-out drinks are never recommended. If the external weather service is unreachable,
the system falls back to clock + history only (alternative flow in the use-case model).
"""
from collections import Counter
from datetime import datetime

from . import beverages
from .db import get_setting, log_event

MORNING = {"Espresso", "Americano", "Cappuccino", "Flat White"}
AFTERNOON = {"Caffe Latte", "Flat White", "Mocha", "Chai Latte"}
EVENING = {"Hot Chocolate", "Chai Latte"}            # low/no caffeine late in the day
COLD_WEATHER = {"Hot Chocolate", "Mocha", "Chai Latte", "Caffe Latte"}
WARM_WEATHER = {"Espresso", "Americano"}


def current_hour(conn):
    """Return the hour used for time-of-day recommendations (the demo override
    if set, otherwise the real clock).

    Args:
        conn (sqlite3.Connection): Open database connection.

    Returns:
        int: Hour of day, 0-23.
    """
    override = get_setting(conn, "hour_override")
    return int(override) if override not in (None, "") else datetime.now().hour


def fetch_weather(conn):
    """Simulated call to the External Weather Service. If the network or the
    service is down, a warning is logged and None is returned so the
    recommender can fall back to clock and history only.

    Args:
        conn (sqlite3.Connection): Open database connection.

    Returns:
        dict | None: ``{"temp_c": float, "condition": str}`` or None if unavailable.
    """
    if get_setting(conn, "network_online") != "1" or get_setting(conn, "weather_service_online") != "1":
        log_event(conn, "warning", "weather", "WX-503", "External weather service unavailable – using clock and history only")
        return None
    return {"temp_c": float(get_setting(conn, "weather_temp_c")), "condition": get_setting(conn, "weather_condition")}


def recommend(conn, user=None, prefs=None, limit=3):
    """Score every available drink and return the best suggestions with reasons (R05).
    Score = order history + time of day + weather + preferences + popularity.

    Args:
        conn (sqlite3.Connection): Open database connection.
        user (dict | None): Logged-in user, or None for a guest.
        prefs (dict | None): The user's saved preferences.
        limit (int): How many suggestions to return (default 3).

    Returns:
        dict: ``context`` (hour, weather, personalised, weather_fallback) and
        ``recommendations`` (list of drink_id, name, price, score, reasons,
        default_options), best first.
    """
    hour = current_hour(conn)
    weather = fetch_weather(conn)
    menu = beverages.menu(conn, prefs)
    history = Counter()
    if user:
        for r in conn.execute("SELECT drink_name FROM orders WHERE user_id=?", (user["id"],)):
            history[r["drink_name"]] += 1
    popularity = Counter(r["drink_name"] for r in conn.execute("SELECT drink_name FROM orders"))
    top_pop = max(popularity.values(), default=1)

    scored = []
    for d in menu["drinks"]:
        if not d["available"]:
            continue
        score, reasons = 0.0, []
        n = history.get(d["name"], 0)
        if n:
            score += min(n, 5) * 1.5
            reasons.append(f"you ordered it {n}×")
        if hour < 11 and d["name"] in MORNING:
            score += 2; reasons.append("great morning pick")
        elif 11 <= hour < 17 and d["name"] in AFTERNOON:
            score += 2; reasons.append("afternoon favourite")
        elif hour >= 17 and d["name"] in EVENING:
            score += 2.5; reasons.append("low caffeine for the evening")
        if hour >= 17 and d["has_coffee"] and d["name"] not in EVENING:
            score -= 1
        if weather:
            if weather["temp_c"] < 10 and d["name"] in COLD_WEATHER:
                score += 2; reasons.append(f"warming on a {weather['temp_c']:.0f}°C {weather['condition'].lower()} day")
            elif weather["temp_c"] > 20 and d["name"] in WARM_WEATHER:
                score += 1.5; reasons.append(f"light choice for {weather['temp_c']:.0f}°C")
        if prefs and prefs.get("milk") in ("oat_milk", "almond_milk") and d["milk_drink"]:
            score += 0.5; reasons.append("matches your plant-milk preference")
        score += popularity.get(d["name"], 0) / top_pop
        if popularity.get(d["name"], 0) == top_pop:
            reasons.append("most popular on this machine")
        scored.append({"drink_id": d["id"], "name": d["name"], "price": d["price"], "score": round(score, 2),
                       "reasons": reasons or ["popular choice"], "default_options": d["default_options"]})
    scored.sort(key=lambda x: -x["score"])
    return {
        "context": {"hour": hour, "weather": weather, "personalised": bool(user),
                    "weather_fallback": weather is None},
        "recommendations": scored[:limit],
    }
