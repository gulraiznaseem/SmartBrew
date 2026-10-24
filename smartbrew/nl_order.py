"""Natural-language drink requests (R17 / US12).

Runs fully on-device: a deterministic intent/slot parser turns a sentence such as
"I need a big sweet coffee with no dairy" into a validated recipe. If a local LLM server
(Ollama) is configured via SMARTBREW_LLM_URL, it is tried first and its JSON answer is
validated against the same option schema; any failure falls back to the rule-based parser.
Nothing leaves the machine either way.
"""
import json
import os
import re
import urllib.request

from . import beverages

DRINK_NAMES = {
    "flat white": 5, "hot chocolate": 7, "chai": 8, "espresso": 1, "americano": 2,
    "cappuccino": 3, "latte": 4, "mocha": 6, "cocoa": 7,
}


def _has(text, *words):
    """Check whether any of the given words or phrases appear as whole words.

    Args:
        text (str): Lower-case text to search.
        *words (str): Words or phrases to look for.

    Returns:
        bool: True if at least one of them is found.
    """
    return any(re.search(r"\b" + re.escape(w) + r"\b", text) for w in words)


def rule_parse(text):
    """On-device parser that turns a sentence into a drink and options (R17).
    It finds the drink (by name or by hints such as "chocolate" or "black")
    and then the size, milk, strength, temperature, syrup and sugar.

    Args:
        text (str): The customer's free-text request.

    Returns:
        dict | None: drink_id, options, interpretation (list of explanations)
        and confidence (float), or None if no drink could be recognised.
    """
    t = " " + text.lower().strip() + " "
    notes = []
    drink_id, explicit = None, False

    for name, did in sorted(DRINK_NAMES.items(), key=lambda kv: -len(kv[0])):
        if _has(t, name):
            drink_id, explicit = did, True
            notes.append(f"drink “{name}” mentioned explicitly")
            break

    dairy_free = _has(t, "no dairy", "dairy free", "dairy-free", "non-dairy", "vegan", "lactose", "lactose free", "lactose-free", "plant milk")
    no_milk = _has(t, "no milk", "without milk", "black")
    sweet = _has(t, "sweet", "sweeter", "sugary")
    wants_coffee = _has(t, "coffee", "caffeine", "espresso", "shot", "awake")

    if drink_id is None:
        if _has(t, "chocolate", "chocolatey", "chocolaty", "choc") and wants_coffee:
            drink_id = 6; notes.append("coffee + chocolate → Mocha")
        elif _has(t, "chocolate", "chocolatey", "chocolaty", "choc", "cocoa"):
            drink_id = 7; notes.append("chocolate without coffee → Hot Chocolate")
        elif _has(t, "tea", "spiced", "spicy"):
            drink_id = 8; notes.append("tea/spice → Chai Latte")
        elif _has(t, "foam", "foamy", "frothy"):
            drink_id = 3; notes.append("foamy → Cappuccino")
        elif no_milk and not dairy_free:
            drink_id = 2; notes.append("black / no milk → Americano")
        elif _has(t, "quick", "short", "tiny shot", "a shot"):
            drink_id = 1; notes.append("quick shot → Espresso")
        elif _has(t, "milky", "creamy", "milk", "smooth") or sweet or dairy_free:
            drink_id = 4; notes.append("creamy / sweet coffee → Caffe Latte")
        elif wants_coffee:
            drink_id = 2; notes.append("plain coffee → Americano")
        else:
            return None

    opts = {}
    if _has(t, "large", "big", "huge", "grande", "massive"):
        opts["size"] = "L"; notes.append("size Large")
    elif _has(t, "small", "tiny", "little cup", "short"):
        opts["size"] = "S"; notes.append("size Small")

    if _has(t, "oat"):
        opts["milk"] = "oat_milk"; notes.append("oat milk")
    elif _has(t, "almond"):
        opts["milk"] = "almond_milk"; notes.append("almond milk")
    elif dairy_free:
        opts["milk"] = "oat_milk"; notes.append("dairy-free → oat milk")
    elif _has(t, "whole milk", "regular milk", "cow"):
        opts["milk"] = "whole_milk"; notes.append("whole milk")

    if _has(t, "strong", "stronger", "extra shot", "double", "bold", "tired", "wake me up", "awake", "intense"):
        opts["strength"] = 3; notes.append("strong (extra shot)")
    elif _has(t, "mild", "weak", "light", "gentle"):
        opts["strength"] = 1; notes.append("mild strength")

    if _has(t, "extra hot", "very hot", "scalding", "piping hot"):
        opts["temperature"] = "extra_hot"; notes.append("extra hot")
    elif _has(t, "warm", "lukewarm", "not too hot", "not hot"):
        opts["temperature"] = "warm"; notes.append("warm, not too hot")

    for syrup in ("vanilla", "caramel", "hazelnut"):
        if _has(t, syrup):
            opts["syrup"] = f"{syrup}_syrup"; notes.append(f"{syrup} syrup")
            break

    if _has(t, "no sugar", "unsweetened", "sugar free", "sugar-free", "not sweet"):
        opts["sugar"] = 0; notes.append("no sugar")
    elif _has(t, "very sweet", "extra sweet", "super sweet"):
        opts["sugar"] = 3; notes.append("very sweet (sugar ×3)")
        opts.setdefault("syrup", "vanilla_syrup")
    elif _has(t, "a little sweet", "slightly sweet", "bit sweet"):
        opts["sugar"] = 1; notes.append("slightly sweet")
    elif sweet:
        if "syrup" not in opts:
            opts["sugar"] = 2; notes.append("sweet (sugar ×2)")

    confidence = 0.9 if explicit else 0.7
    return {"drink_id": drink_id, "options": opts, "interpretation": notes, "confidence": confidence}


def llm_parse(text):
    """Optionally ask a local LLM (Ollama) to convert the request to JSON.
    Only used when the environment variable ``SMARTBREW_LLM_URL`` is set.

    Args:
        text (str): The customer's free-text request.

    Returns:
        dict | None: Same shape as :func:`rule_parse`, or None if no LLM is
        configured or the call or parsing fails.
    """
    url = os.environ.get("SMARTBREW_LLM_URL")
    if not url:
        return None
    prompt = (
        "You convert coffee-machine requests to JSON. Drinks (id:name): 1 Espresso, 2 Americano, 3 Cappuccino, "
        "4 Caffe Latte, 5 Flat White, 6 Mocha, 7 Hot Chocolate, 8 Chai Latte. Options: size S|M|L, "
        "milk none|whole_milk|oat_milk|almond_milk, strength 1|2|3, temperature warm|hot|extra_hot, "
        "syrup none|vanilla_syrup|caramel_syrup|hazelnut_syrup, sugar 0-3. Reply ONLY with "
        '{"drink_id":int,"options":{...}}. Request: ' + text
    )
    body = json.dumps({"model": os.environ.get("SMARTBREW_LLM_MODEL", "llama3.2"), "prompt": prompt,
                       "stream": False, "format": "json"}).encode()
    try:
        req = urllib.request.Request(url.rstrip("/") + "/api/generate", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(json.loads(resp.read())["response"])
        return {"drink_id": int(data["drink_id"]), "options": data.get("options", {}),
                "interpretation": ["interpreted by on-device LLM"], "confidence": 0.85}
    except Exception:
        return None


def interpret(conn, text, prefs=None):
    """Turn a natural-language request into a validated, priced recipe. Tries
    the local LLM first, then the rule parser, and switches milk if the
    requested one is out of stock.

    Args:
        conn (sqlite3.Connection): Open database connection.
        text (str): The customer's request, e.g. "big sweet coffee, no dairy".
        prefs (dict | None): The user's preferences, used as defaults.

    Returns:
        dict: ``understood`` (bool). If True, also source, confidence,
        interpretation and the full :func:`beverages.quote` result; if False,
        a ``message`` explaining what to try.

    Raises:
        OptionError: If the text is empty.
    """
    if not text or not text.strip():
        raise beverages.OptionError("Please tell me what you would like")
    parsed, source = llm_parse(text), "local-llm"
    if parsed:
        try:
            drink = beverages.get_drink(conn, parsed["drink_id"])
            beverages.normalise_options(drink, parsed["options"])
        except Exception:
            parsed = None
    if not parsed:
        parsed, source = rule_parse(text), "on-device parser"
    if not parsed:
        return {"understood": False, "message": "Sorry, I couldn't work out a drink from that. Try e.g. “large oat latte with vanilla”."}

    drink = beverages.get_drink(conn, parsed["drink_id"])
    opts = beverages.default_options(drink, prefs)
    # A milk-free request on a milk drink, or a plant milk on a non-milk drink, is still valid.
    opts.update(parsed["options"])
    if beverages.is_milk_drink(drink) and opts.get("milk") == "none":
        opts["milk"] = "oat_milk"
    q = beverages.quote(conn, drink["id"], opts)
    if not q["available"] and beverages.is_milk_drink(drink):
        for alt in ("oat_milk", "almond_milk", "whole_milk"):
            if alt == opts["milk"]:
                continue
            q2 = beverages.quote(conn, drink["id"], {**opts, "milk": alt})
            if q2["available"]:
                parsed["interpretation"].append(f"{beverages.MILKS[opts['milk']]} is out – switched to {beverages.MILKS[alt]}")
                q = q2
                break
    return {"understood": True, "source": source, "confidence": parsed["confidence"],
            "interpretation": parsed["interpretation"], **q}
