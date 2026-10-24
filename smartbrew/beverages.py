"""Menu, customisation, pricing and ingredient availability (R03, R04, R06)."""
import json

MILKS = {"none": "No milk", "whole_milk": "Whole milk", "oat_milk": "Oat milk", "almond_milk": "Almond milk"}
SYRUPS = {"none": "No syrup", "vanilla_syrup": "Vanilla", "caramel_syrup": "Caramel", "hazelnut_syrup": "Hazelnut"}
SIZES = {"S": 0.75, "M": 1.0, "L": 1.35}
SIZE_PRICE = {"S": -5, "M": 0, "L": 8}
STRENGTH_FACTOR = {1: 0.8, 2: 1.0, 3: 1.5}
TEMPERATURES = {"warm": "Warm (60°C)", "hot": "Hot (70°C)", "extra_hot": "Extra hot (80°C)"}
PLANT_MILK_PRICE = 5
SYRUP_PRICE = 5
EXTRA_SHOT_PRICE = 7
MILK_SPLASH_ML = 30
SYRUP_ML = 15
SUGAR_G_PER_LEVEL = 5


class OptionError(ValueError):
    """Raised when a drink id or a customisation option is invalid (shown to the user as HTTP 400)."""


def drink_rows(conn):
    """Load every drink of the menu with its recipe decoded from JSON.

    Args:
        conn (sqlite3.Connection): Open database connection.

    Returns:
        list[dict]: One dict per drink (id, name, category, description,
        base_price, recipe, caffeine), ordered by id.
    """
    rows = conn.execute("SELECT * FROM drinks ORDER BY id").fetchall()
    return [{**dict(r), "recipe": json.loads(r["recipe"])} for r in rows]


def get_drink(conn, drink_id):
    """Load a single drink by id.

    Args:
        conn (sqlite3.Connection): Open database connection.
        drink_id (int): Id of the drink.

    Returns:
        dict: The drink, with ``recipe`` decoded to a dict.

    Raises:
        OptionError: If no drink has that id.
    """
    r = conn.execute("SELECT * FROM drinks WHERE id=?", (drink_id,)).fetchone()
    if not r:
        raise OptionError(f"Unknown drink id {drink_id}")
    return {**dict(r), "recipe": json.loads(r["recipe"])}


def is_milk_drink(drink):
    """Tell whether a drink's base recipe contains milk.

    Args:
        drink (dict): Drink as returned by :func:`get_drink`.

    Returns:
        bool: True if the recipe contains a ``"milk"`` ingredient.
    """
    return "milk" in drink["recipe"]


def default_options(drink, prefs=None):
    """Build the default customisation for a drink, filled in from the user's
    saved preferences when possible (F1 + F3).

    Args:
        drink (dict): Drink as returned by :func:`get_drink`.
        prefs (dict | None): Saved user preferences (milk, strength, size,
            temperature, sugar), or None for a guest.

    Returns:
        dict: Options with keys size, milk, strength, temperature, syrup, sugar.
    """
    prefs = prefs or {}
    milk = "whole_milk" if is_milk_drink(drink) else "none"
    if is_milk_drink(drink) and prefs.get("milk") in MILKS and prefs.get("milk") != "none":
        milk = prefs["milk"]
    return {
        "size": prefs.get("size", "M") if prefs.get("size") in SIZES else "M",
        "milk": milk,
        "strength": int(prefs.get("strength", 2)) if int(prefs.get("strength", 2)) in STRENGTH_FACTOR else 2,
        "temperature": prefs.get("temperature", "hot") if prefs.get("temperature") in TEMPERATURES else "hot",
        "syrup": "none",
        "sugar": int(prefs.get("sugar", 0)) if 0 <= int(prefs.get("sugar", 0)) <= 3 else 0,
    }


def normalise_options(drink, options):
    """Merge user-chosen options onto the defaults and validate every value (R04).

    Args:
        drink (dict): Drink being customised.
        options (dict | None): Partial options chosen by the user.

    Returns:
        dict: A complete, valid options dict.

    Raises:
        OptionError: If a value is unknown or the combination is invalid
            (e.g. a latte with no milk).
    """
    opts = default_options(drink)
    opts.update({k: v for k, v in (options or {}).items() if v is not None})
    try:
        opts["strength"] = int(opts["strength"])
        opts["sugar"] = int(opts["sugar"])
    except (TypeError, ValueError):
        raise OptionError("strength and sugar must be integers")
    if opts["size"] not in SIZES:
        raise OptionError("size must be S, M or L")
    if opts["milk"] not in MILKS:
        raise OptionError("unknown milk type")
    if is_milk_drink(drink) and opts["milk"] == "none":
        raise OptionError(f"{drink['name']} needs milk – choose whole, oat or almond")
    if opts["syrup"] not in SYRUPS:
        raise OptionError("unknown syrup")
    if opts["strength"] not in STRENGTH_FACTOR:
        raise OptionError("strength must be 1, 2 or 3")
    if opts["temperature"] not in TEMPERATURES:
        raise OptionError("unknown temperature")
    if not 0 <= opts["sugar"] <= 3:
        raise OptionError("sugar must be 0-3")
    return {k: opts[k] for k in ("size", "milk", "strength", "temperature", "syrup", "sugar")}


def consumption(drink, opts):
    """Calculate exactly how much of each ingredient a configured drink uses.
    Size scales liquids, strength scales the coffee dose, and syrup, sugar
    and a cup are added.

    Args:
        drink (dict): Drink definition.
        opts (dict): Validated options from :func:`normalise_options`.

    Returns:
        dict[str, float]: Ingredient name -> amount (g, ml or pcs).
    """
    size = SIZES[opts["size"]]
    need = {}
    for ing, amount in drink["recipe"].items():
        if ing == "coffee_beans":
            need[ing] = amount * STRENGTH_FACTOR[opts["strength"]]
        elif ing == "milk":
            need[opts["milk"]] = amount * size
        else:
            need[ing] = amount * size
    if not is_milk_drink(drink) and opts["milk"] != "none":
        need[opts["milk"]] = need.get(opts["milk"], 0) + MILK_SPLASH_ML * size
    if opts["syrup"] != "none":
        need[opts["syrup"]] = SYRUP_ML * size
    if opts["sugar"]:
        need["sugar"] = SUGAR_G_PER_LEVEL * opts["sugar"]
    need["cups"] = 1
    return {k: round(v, 1) for k, v in need.items()}


def price(drink, opts):
    """Calculate the price of a configured drink: base price plus size, plant
    milk, syrup and extra-shot surcharges.

    Args:
        drink (dict): Drink definition.
        opts (dict): Validated options.

    Returns:
        float: Price in Swedish kronor.
    """
    p = drink["base_price"] + SIZE_PRICE[opts["size"]]
    if opts["milk"] in ("oat_milk", "almond_milk"):
        p += PLANT_MILK_PRICE
    if opts["syrup"] != "none":
        p += SYRUP_PRICE
    if opts["strength"] == 3 and "coffee_beans" in drink["recipe"]:
        p += EXTRA_SHOT_PRICE
    return float(p)


def stock(conn):
    """Read the current inventory level of every ingredient.

    Args:
        conn (sqlite3.Connection): Open database connection.

    Returns:
        dict[str, dict]: Ingredient name -> row (label, unit, level, capacity,
        low_threshold).
    """
    return {r["name"]: dict(r) for r in conn.execute("SELECT * FROM ingredients").fetchall()}


def missing_ingredients(conn, need, stock_map=None):
    """List the ingredients that are not in stock in the required amount (R06).

    Args:
        conn (sqlite3.Connection): Open database connection.
        need (dict[str, float]): Required amounts from :func:`consumption`.
        stock_map (dict | None): Pre-loaded stock to avoid another query.

    Returns:
        list[str]: Labels of the missing ingredients (empty if all are available).
    """
    stock_map = stock_map or stock(conn)
    return [stock_map[i]["label"] for i, amt in need.items() if stock_map.get(i, {"level": 0})["level"] < amt]


def quote(conn, drink_id, options):
    """Validate a customised drink and return its price, ingredient use and
    availability. Used for the live price on the customise screen.

    Args:
        conn (sqlite3.Connection): Open database connection.
        drink_id (int): Id of the chosen drink.
        options (dict | None): User-chosen options.

    Returns:
        dict: drink, options, price, consumption, available (bool), missing (list).

    Raises:
        OptionError: If the drink or options are invalid.
    """
    drink = get_drink(conn, drink_id)
    opts = normalise_options(drink, options)
    need = consumption(drink, opts)
    missing = missing_ingredients(conn, need)
    return {
        "drink": {"id": drink["id"], "name": drink["name"]},
        "options": opts,
        "price": price(drink, opts),
        "consumption": need,
        "available": not missing,
        "missing": missing,
    }


def menu(conn, prefs=None):
    """Build the menu shown on the touchscreen, with sold-out flags (R03, R06).
    If the default milk is missing, the other milks are tried before a drink
    is marked as sold out.

    Args:
        conn (sqlite3.Connection): Open database connection.
        prefs (dict | None): Logged-in user's preferences, used for defaults.

    Returns:
        dict: ``drinks`` (list), ``options`` (all option labels) and
        ``option_stock`` (which milks/syrups can still be chosen).
    """
    s = stock(conn)
    items = []
    for d in drink_rows(conn):
        opts = default_options(d, prefs)
        need = consumption(d, opts)
        missing = missing_ingredients(conn, need, s)
        if missing and is_milk_drink(d):
            # try any other milk before declaring the drink sold out
            for alt in ("whole_milk", "oat_milk", "almond_milk"):
                alt_opts = {**opts, "milk": alt}
                if not missing_ingredients(conn, consumption(d, alt_opts), s):
                    opts, missing = alt_opts, []
                    break
        items.append({
            "id": d["id"], "name": d["name"], "category": d["category"], "description": d["description"],
            "base_price": d["base_price"], "milk_drink": is_milk_drink(d), "has_coffee": "coffee_beans" in d["recipe"],
            "available": not missing, "missing": missing, "default_options": opts, "price": price(d, opts),
        })
    option_stock = {
        "milk": {m: (m == "none" or s[m]["level"] >= 50) for m in MILKS},
        "syrup": {y: (y == "none" or s[y]["level"] >= SYRUP_ML * 1.35) for y in SYRUPS},
    }
    return {
        "drinks": items,
        "options": {"milk": MILKS, "syrup": SYRUPS, "size": list(SIZES), "temperature": TEMPERATURES,
                    "strength": {1: "Mild", 2: "Normal", 3: "Strong (+shot)"}, "sugar": [0, 1, 2, 3]},
        "option_stock": option_stock,
    }


def consume(conn, need):
    """Subtract the ingredients used by a brewed drink from the inventory.

    Args:
        conn (sqlite3.Connection): Open database connection.
        need (dict[str, float]): Amounts from :func:`consumption`.

    Returns:
        None.
    """
    for ing, amt in need.items():
        conn.execute("UPDATE ingredients SET level = MAX(level - ?, 0) WHERE name=?", (amt, ing))
