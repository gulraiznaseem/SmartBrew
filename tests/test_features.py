"""Acceptance tests – one block per selected functionality (F1–F10).

Each test follows the "How-to-demo" column of the product backlog (Task 2D).
Run with:  python -m pytest -v
"""
import pytest

from smartbrew.app import create_app


@pytest.fixture
def client(tmp_path):
    """Pytest fixture: a Flask test client backed by a fresh temporary database.

    Args:
        tmp_path (pathlib.Path): Temporary directory provided by pytest.

    Returns:
        flask.testing.FlaskClient: Client used to call the API.
    """
    app = create_app(str(tmp_path / "test.db"), reset=True)
    app.config["TESTING"] = True
    return app.test_client()


def login(c, email, pw):
    """Helper: log in by password and assert that it succeeds.

    Args:
        c (FlaskClient): Test client.
        email (str): User e-mail.
        pw (str): Password.

    Returns:
        dict: The logged-in user returned by the API.
    """
    r = c.post("/api/auth/login", json={"method": "password", "email": email, "password": pw})
    assert r.status_code == 200, r.json
    return r.json["user"]


def settings(c, **kw):
    """Helper: change demo settings (network, weather, time) and assert success.

    Args:
        c (FlaskClient): Test client.
        **kw: Settings to change.

    Returns:
        None.
    """
    assert c.post("/api/demo/settings", json=kw).status_code == 200


CARD_OK = "4242 4242 4242 4242"
CARD_DECLINE = "4000 0000 0000 0002"


def buy(c, drink_id=4, options=None, payment=None):
    """Helper: check out a drink (default: Caffe Latte paid by an approved card).

    Args:
        c (FlaskClient): Test client.
        drink_id (int): Drink to buy (default 4).
        options (dict | None): Customisation options.
        payment (dict | None): Payment data.

    Returns:
        flask.Response: The API response.
    """
    return c.post("/api/checkout", json={"drink_id": drink_id, "options": options or {},
                                         "payment": payment or {"method": "card", "card_number": CARD_OK}})


# ---------------- F1  Customer account & preferences (R01, PB01, US01)
def test_f1_account_preferences_persist(client):
    """Acceptance test - F1: preferences saved at sign-up survive logout/login, pre-fill drinks and can be updated.

    Args:
        client (FlaskClient): Test client with fresh demo data.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    r = client.post("/api/auth/register", json={"name": "Erik", "email": "erik@test.se", "password": "secret1",
                                                "preferences": {"milk": "almond_milk", "strength": 3, "size": "L"}})
    assert r.status_code == 201
    client.post("/api/auth/logout")
    user = login(client, "erik@test.se", "secret1")
    assert user["preferences"]["milk"] == "almond_milk"
    # preferences drive the default customisation on the menu
    latte = next(d for d in client.get("/api/menu").json["drinks"] if d["name"] == "Caffe Latte")
    assert latte["default_options"]["milk"] == "almond_milk" and latte["default_options"]["size"] == "L"
    # update profile
    r = client.put("/api/profile", json={"preferences": {"temperature": "extra_hot"}})
    assert r.json["user"]["preferences"]["temperature"] == "extra_hot"
    assert r.json["user"]["preferences"]["milk"] == "almond_milk"


def test_f1_validation(client):
    """Acceptance test - F1: invalid sign-up data and duplicate e-mails are rejected.

    Args:
        client (FlaskClient): Test client with fresh demo data.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    assert client.post("/api/auth/register", json={"name": "X", "email": "bad", "password": "1"}).status_code == 400
    assert client.post("/api/auth/register", json={"name": "Dup", "email": "alice@smartbrew.se", "password": "secret1"}).status_code == 409


# ---------------- F2  Authentication: password, NFC card, roles, lockout (R02, PB02, US02)
def test_f2_password_and_card_login(client):
    """Acceptance test - F2: password login, NFC card login with role detection, and rejection of unknown cards.

    Args:
        client (FlaskClient): Test client with fresh demo data.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    assert login(client, "alice@smartbrew.se", "coffee123")["role"] == "customer"
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"method": "card", "card_uid": "TECH-01"})
    assert r.json["user"]["role"] == "technician"
    assert client.post("/api/auth/login", json={"method": "card", "card_uid": "NOPE"}).status_code == 401


def test_f2_lockout_and_role_guard(client):
    """Acceptance test - F2: lockout after 3 failed logins, and customers blocked from technician/admin functions.

    Args:
        client (FlaskClient): Test client with fresh demo data.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    for _ in range(3):
        assert client.post("/api/auth/login", json={"email": "alice@smartbrew.se", "password": "wrong"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "alice@smartbrew.se", "password": "coffee123"})
    assert r.status_code == 423  # locked even with right password
    # customers cannot reach technician / admin functions
    client.post("/api/auth/login", json={"method": "card", "card_uid": "CARD-ALICE"})
    assert client.get("/api/tech/status").status_code == 403
    assert client.get("/api/admin/errors").status_code == 403


# ---------------- F3  Browse & customise beverage with live pricing (R03, R04, PB03, PB04, US03)
def test_f3_menu_and_customisation(client):
    """Acceptance test - F3: the menu lists 8 drinks; options change price and ingredient use; invalid options are rejected.

    Args:
        client (FlaskClient): Test client with fresh demo data.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    menu = client.get("/api/menu").json
    assert len(menu["drinks"]) == 8
    base = client.post("/api/quote", json={"drink_id": 4, "options": {"milk": "whole_milk"}}).json
    custom = client.post("/api/quote", json={"drink_id": 4, "options": {"size": "L", "milk": "oat_milk", "strength": 3,
                                                                         "syrup": "caramel_syrup", "temperature": "extra_hot"}}).json
    assert custom["price"] == base["price"] + 8 + 5 + 5 + 7
    assert custom["consumption"]["oat_milk"] > base["consumption"]["whole_milk"]   # large uses more milk
    assert custom["consumption"]["coffee_beans"] == 27                              # strong = 1.5 × 18 g
    assert client.post("/api/quote", json={"drink_id": 4, "options": {"milk": "none"}}).status_code == 400


# ---------------- F4  Ingredient availability lock-out (R06, PB06)
def test_f4_out_of_milk_locks_drinks(client):
    """Acceptance test - F4: with no milk, milk drinks are sold out and cannot be bought, while black coffee still can.

    Args:
        client (FlaskClient): Test client with fresh demo data.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    for m in ("whole_milk", "oat_milk", "almond_milk"):
        client.post("/api/demo/ingredient", json={"name": m, "level": 0})
    drinks = {d["name"]: d for d in client.get("/api/menu").json["drinks"]}
    assert not drinks["Caffe Latte"]["available"] and not drinks["Hot Chocolate"]["available"]
    assert drinks["Espresso"]["available"] and drinks["Americano"]["available"]
    client.post("/api/auth/guest")
    assert buy(client, 4, {"milk": "oat_milk"}).status_code == 409


def test_f4_falls_back_to_other_milk(client):
    """Acceptance test - F4: when one milk runs out, the drink stays available with another milk.

    Args:
        client (FlaskClient): Test client with fresh demo data.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    client.post("/api/demo/ingredient", json={"name": "whole_milk", "level": 0})
    latte = next(d for d in client.get("/api/menu").json["drinks"] if d["name"] == "Caffe Latte")
    assert latte["available"] and latte["default_options"]["milk"] != "whole_milk"


# ---------------- F5  Personalised, context-aware recommendations (R05, PB05, US04, US05)
def test_f5_recommendations_use_history_time_and_weather(client):
    """Acceptance test - F5: suggestions reflect order history, cold weather, and the evening (low caffeine).

    Args:
        client (FlaskClient): Test client with fresh demo data.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    login(client, "alice@smartbrew.se", "coffee123")
    settings(client, hour_override="15", weather_temp_c="3", weather_condition="Snowy")
    recs = client.get("/api/recommendations").json
    assert recs["context"]["personalised"] and recs["context"]["weather"]["temp_c"] == 3
    assert recs["recommendations"][0]["name"] == "Caffe Latte"          # Alice's most ordered drink
    assert any("ordered" in reason for reason in recs["recommendations"][0]["reasons"])
    # evening + guest → low-caffeine drinks first
    client.post("/api/auth/guest")
    settings(client, hour_override="20")
    top = client.get("/api/recommendations").json["recommendations"][0]["name"]
    assert top in ("Hot Chocolate", "Chai Latte")


def test_f5_weather_service_down_fallback(client):
    """Acceptance test - F5: suggestions still work when the weather service is down.

    Args:
        client (FlaskClient): Test client with fresh demo data.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    settings(client, weather_service_online=False)
    recs = client.get("/api/recommendations").json
    assert recs["context"]["weather_fallback"] and len(recs["recommendations"]) == 3


# ---------------- F6  Save & reorder custom recipes (R07, PB07, US06)
def test_f6_custom_recipes(client):
    """Acceptance test - F6: save, list and limit recipes, overwrite the oldest when full, and require login.

    Args:
        client (FlaskClient): Test client with fresh demo data.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    login(client, "alice@smartbrew.se", "coffee123")
    opts = {"size": "L", "milk": "oat_milk", "syrup": "vanilla_syrup", "strength": 3}
    assert client.post("/api/recipes", json={"name": "Monday Fuel", "drink_id": 4, "options": opts}).status_code == 201
    saved = client.get("/api/recipes").json["recipes"]
    assert saved[0]["name"] == "Monday Fuel" and saved[0]["options"]["syrup"] == "vanilla_syrup"
    for i in range(4):
        client.post("/api/recipes", json={"name": f"Drink {i}", "drink_id": 1, "options": {}})
    r = client.post("/api/recipes", json={"name": "One too many", "drink_id": 2, "options": {}})
    assert r.status_code == 409 and r.json["limit_reached"]
    r = client.post("/api/recipes", json={"name": "One too many", "drink_id": 2, "options": {}, "overwrite_oldest": True})
    assert r.status_code == 201
    names = [x["name"] for x in client.get("/api/recipes").json["recipes"]]
    assert "Monday Fuel" not in names and len(names) == 5
    client.post("/api/auth/logout")
    assert client.get("/api/recipes").status_code == 401


# ---------------- F7  Natural-language ordering (R17, PB16, US12)
@pytest.mark.parametrize("text,drink,checks", [
    ("I need a sweet coffee with no dairy", "Caffe Latte", {"milk": "oat_milk", "sugar": 2}),
    ("large strong flat white with almond milk", "Flat White", {"size": "L", "strength": 3, "milk": "almond_milk"}),
    ("something chocolatey with coffee, extra hot", "Mocha", {"temperature": "extra_hot"}),
    ("small black coffee", "Americano", {"size": "S", "milk": "none"}),
    ("a warm chai with caramel", "Chai Latte", {"temperature": "warm", "syrup": "caramel_syrup"}),
])
def test_f7_natural_language(client, text, drink, checks):
    """Acceptance test - F7: natural-language sentences map to the right drink and options.

    Args:
        client (FlaskClient): Test client with fresh demo data.
        text (str): Customer sentence.
        drink (str): Expected drink name.
        checks (dict): Expected option values.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    r = client.post("/api/nl-order", json={"text": text}).json
    assert r["understood"] and r["drink"]["name"] == drink
    for k, v in checks.items():
        assert r["options"][k] == v, (k, r["options"])


def test_f7_not_understood(client):
    """Acceptance test - F7: unrelated text is reported as not understood.

    Args:
        client (FlaskClient): Test client with fresh demo data.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    assert client.post("/api/nl-order", json={"text": "what is the meaning of life"}).json["understood"] is False


# ---------------- F8  Ordering & payment: card, wallet, badge, decline, offline (R08, R09, R16, PB08, PB09, PB15, US07)
def test_f8_payment_methods(client):
    """Acceptance test - F8: card, wallet and badge payments succeed and use inventory; declined and invalid cards fail.

    Args:
        client (FlaskClient): Test client with fresh demo data.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    login(client, "alice@smartbrew.se", "coffee123")
    beans_before = next(i for i in client.get("/api/demo/state").json["ingredients"] if i["name"] == "coffee_beans")["level"]
    r = buy(client)
    assert r.status_code == 201 and r.json["order"]["payment_status"] == "approved"
    beans_after = next(i for i in client.get("/api/demo/state").json["ingredients"] if i["name"] == "coffee_beans")["level"]
    assert beans_after == beans_before - 18                                  # inventory consumed
    assert buy(client, payment={"method": "wallet", "wallet": "swish"}).status_code == 201
    r = buy(client, 1, payment={"method": "badge", "badge_uid": "CARD-ALICE"})
    assert r.status_code == 201
    assert client.get("/api/profile").json["user"]["badge_balance"] == 150 - 28
    assert buy(client, payment={"method": "card", "card_number": CARD_DECLINE}).status_code == 402
    assert buy(client, payment={"method": "card", "card_number": "1234 5678"}).status_code == 402


def test_f8_offline_queue_and_settlement(client):
    """Acceptance test - F8: offline card payments are queued and settled when the network returns.

    Args:
        client (FlaskClient): Test client with fresh demo data.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    client.post("/api/auth/guest")
    settings(client, network_online=False)
    r = buy(client)
    assert r.json["order"]["payment_status"] == "pending_offline"
    assert client.get("/api/demo/state").json["pending_offline"] == 1
    settled = client.post("/api/demo/settings", json={"network_online": True}).json["settled"]
    assert settled == 1 and client.get("/api/demo/state").json["pending_offline"] == 0


# ---------------- F9  Digital receipts: e-mail or QR (R10, PB10, US08)
def test_f9_receipts(client):
    """Acceptance test - F9: e-mail receipt reaches the outbox, the QR receipt page works, and a bad e-mail is rejected.

    Args:
        client (FlaskClient): Test client with fresh demo data.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    client.post("/api/auth/guest")
    oid = buy(client).json["order"]["id"]
    r = client.post(f"/api/orders/{oid}/receipt", json={"method": "email", "email": "me@test.se"}).json
    assert r["sent_to"] == "me@test.se"
    outbox = client.get("/api/demo/state").json["outbox"]
    assert outbox[0]["recipient"] == "me@test.se" and f"#{oid}" in outbox[0]["subject"]
    r = client.post(f"/api/orders/{oid}/receipt", json={"method": "qr"}).json
    assert r["qr_svg"].lstrip().startswith("<?xml") or "<svg" in r["qr_svg"]
    page = client.get(f"/receipt/{r['token']}")
    assert page.status_code == 200 and b"Caffe Latte" in page.data
    assert client.post(f"/api/orders/{oid}/receipt", json={"method": "email", "email": "nope"}).status_code == 400


# ---------------- F10  Maintenance, waste monitoring & error diagnostics (R11–R14, PB11–PB13, US09–US11)
def test_f10_refill_waste_shutdown_and_diagnostics(client):
    """Acceptance test - F10: full bin suspends the machine, technician empties and refills, admin sees and resolves the error log.

    Args:
        client (FlaskClient): Test client with fresh demo data.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    client.post("/api/demo/ingredient", json={"name": "oat_milk", "level": 0})
    client.post("/api/demo/bin", json={"bin": "grounds", "percent": 100})       # bin full → machine suspended
    client.post("/api/auth/guest")
    assert buy(client).status_code == 503

    client.post("/api/auth/login", json={"method": "card", "card_uid": "TECH-01"})
    st = client.get("/api/tech/status").json
    assert st["machine_status"] == "suspended"
    assert client.post("/api/tech/empty-bin", json={"bin": "grounds"}).json["machine_status"] == "operational"
    r = client.post("/api/tech/refill", json={"ingredient": "oat_milk"}).json
    assert r["result"]["after"] == r["result"]["capacity"]
    r = client.post("/api/tech/refill", json={"ingredient": "sugar", "amount": -5})
    assert r.status_code == 400

    client.post("/api/demo/fault", json={"component": "grinder"})
    client.post("/api/auth/login", json={"method": "card", "card_uid": "ADMIN-01"})
    log = client.get("/api/admin/errors").json["errors"]
    codes = {e["code"] for e in log}
    assert {"WST-FULL", "INV-EMPTY", "MNT-REFILL", "MNT-EMPTY", "GRD-JAM"} <= codes
    crit = client.get("/api/admin/errors?severity=critical").json["errors"]
    assert crit and all(e["severity"] == "critical" for e in crit)
    eid = next(e["id"] for e in log if e["code"] == "GRD-JAM")
    assert client.post(f"/api/admin/errors/{eid}/resolve").json["resolved"]


def test_f10_waste_warning_from_brewing(client):
    """Acceptance test - F10: brewing pushes the grounds bin past 80 % and triggers a warning.

    Args:
        client (FlaskClient): Test client with fresh demo data.

    Returns:
        None. Fails with AssertionError if the behaviour is wrong.
    """
    client.post("/api/demo/bin", json={"bin": "grounds", "percent": 79})
    client.post("/api/auth/guest")
    buy(client)
    bins = {b["name"]: b for b in client.get("/api/demo/state").json["bins"]}
    assert bins["grounds"]["state"] == "warning"
