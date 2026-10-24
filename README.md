# ☕ SmartBrew: smart coffee machine prototype

> Group 26, Lab 2: Task B (executable prototype) and Task C (repository and documentation)

---

## 1. System overview

**SmartBrew** is a smart self-service coffee machine. Customers use a touchscreen kiosk to log in with a password or by tapping a card, customise drinks, get personalised suggestions, order in plain language, pay with a card, phone wallet or work badge, and receive a digital receipt. Technicians refill supplies and empty waste bins. Admins review the machine's error history.

This repository holds a working prototype of the system:

* a **Python/Flask backend** with a JSON REST API and an **SQLite** database
* a **browser-based touchscreen UI** (HTML/CSS/vanilla JavaScript, no build step)
* a **demo control panel** that simulates the hardware and outside services (network, weather API, bin and ingredient sensors, NFC reader, payment gateway, e-mail), so every feature can be shown without a physical machine
* an **automated acceptance-test suite** (21 tests, pytest)

### The 10 implemented functionalities

They are taken from the requirements (R), backlog items (PB) and user stories (US) of Task A.

| # | Functionality | Traces to |
|---|---|---|
| **F1** | Customer account and saved preferences, which pre-fill every drink | R01 · PB01 · US01 |
| **F2** | Secure login by password **or NFC card tap**, with customer, technician and admin roles; the account locks after 3 failed attempts | R02 · PB02 · US02 |
| **F3** | Browse the menu and customise a drink (size, milk, strength, temperature, syrup, sugar) with a live price | R03, R04 · PB03, PB04 · US03 |
| **F4** | Drinks lock automatically when ingredients run out, with automatic fallback to another milk | R06 · PB06 |
| **F5** | Personalised recommendations from order history, time of day, weather and preferences, with reasons shown | R05 · PB05 · US04, US05 |
| **F6** | Name, save and reorder custom drinks in one tap (limit of 5, with an overwrite prompt) | R07 · PB07 · US06 |
| **F7** | Order in natural language ("I need a big sweet coffee with no dairy"), processed on the device; an optional local LLM is supported | R17 · PB16 · US12 |
| **F8** | Ordering and payment by card, digital wallet or prepaid work badge; card declines; offline mode with deferred settlement | R08, R09, R16 · PB08, PB09, PB15 · US07 |
| **F9** | Digital receipt by e-mail or QR code | R10 · PB10 · US08 |
| **F10** | Maintenance and diagnostics: ingredient refills, waste-bin monitoring with automatic shutdown, admin error history | R11–R14 · PB11–PB13 · US09–US11 |

R15 (camera-based mood detection) is not implemented because it needs camera hardware and an emotion-recognition model. Its role as an input to recommendations is covered by the time and weather context in F5.

### Architecture

```
Browser (touchscreen UI + demo panel)
        │  fetch() JSON
        ▼
Flask REST API  (smartbrew/app.py: routes, sessions, role checks)
        │
        ├── beverages.py    F3/F4  menu, options, pricing, ingredient use, availability
        ├── recommender.py  F5     scoring model + simulated weather service
        ├── nl_order.py     F7     on-device natural-language parser (+ optional Ollama LLM)
        ├── payments.py     F8/F9  payment authorisation, offline queue, receipts, QR codes
        └── maintenance.py  F10    refills, waste bins, shutdown rule, error log
        │
        ▼
SQLite database (smartbrew.db, created and seeded automatically by db.py)
```

### Repository structure

```
smartbrew/
├── README.md               ← this document
├── requirements.txt        ← Python dependencies
├── run.py                  ← start the server (python run.py [--reset] [--port N] [--host H])
├── run_mac_linux.sh        ← one-command setup + start for macOS/Linux
├── run_windows.bat         ← double-click setup + start for Windows
├── smartbrew/              ← backend package
│   ├── app.py              ← Flask application factory and all REST endpoints
│   ├── db.py               ← schema, seed data, settings, event logging
│   ├── beverages.py        ← F3, F4
│   ├── recommender.py      ← F5
│   ├── nl_order.py         ← F7
│   ├── payments.py         ← F8, F9
│   └── maintenance.py      ← F10
├── static/                 ← front-end (index.html, style.css, app.js)
├── tests/test_features.py  ← acceptance tests, one group per functionality
├── tools/check_docs.py     ← checks that every function is documented
```

---

## 2. Environment and prerequisites

| Item | Requirement | Tested with |
|---|---|---|
| Operating system | macOS, Linux or Windows | Linux (Ubuntu); the code is OS-independent |
| Runtime | **Python 3.9 or newer** (no compiler needed: Python is interpreted) | Python **3.9.25** and **3.13.17** |
| Browser | Any modern browser (Chrome, Safari, Firefox, Edge) | Chromium |
| Git | Any recent version, only needed to clone the repository | git 2.43 |

### Python libraries (installed from `requirements.txt`)

| Library | Version | Used for |
|---|---|---|
| Flask | ≥ 3.0 (tested 3.1.3) | Web server, REST API, sessions |
| Werkzeug | installed with Flask (tested 3.1.9) | Password hashing (PBKDF2) |
| qrcode | ≥ 7.4 (tested 8.2) | QR-code receipts (F9), pure Python, no Pillow needed |
| pytest | ≥ 8.0 (tested 8.4.2 / 9.1.1) | Automated acceptance tests |

The standard library also provides `sqlite3`, `json`, `hashlib`, `secrets` and `urllib`. **No database server is needed**: SQLite is built into Python.

### External dependencies

* **None are required.** The payment gateway, weather API, NFC reader, sensors and SMTP server are simulated inside the app and controlled from the demo panel.
* **Optional:** [Ollama](https://ollama.com) as a local LLM for F7 (see §3.6). Without it, F7 uses the built-in on-device parser.

---

## 3. Execution guide

### 3.1 Get the code

```bash
git clone https://github.com/gulraiznaseem/SmartBrew.git
cd SmartBrew
```

(Or download the ZIP from GitHub and unzip it.)

### 3.2 Build / install

Python needs no compiling. "Building" means creating a virtual environment and installing the libraries.

**macOS / Linux**
```bash
python3 --version                 # must be 3.9 or newer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows (Command Prompt)**
```bat
python --version
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**Shortcut:** `bash run_mac_linux.sh` (macOS/Linux) or double-click `run_windows.bat` (Windows). These scripts do all of the above and start the app.

### 3.3 Run

```bash
python run.py --reset
```

Then open **http://127.0.0.1:5001** in your browser. Keep the terminal open while you use the app; press **Ctrl + C** to stop it.

| Argument | Default | Meaning |
|---|---|---|
| `--reset` | off | Delete and re-create the database with fresh demo data |
| `--port N` | `5001` | Port to listen on (5001 avoids macOS AirPlay, which uses 5000) |
| `--host H` | `127.0.0.1` | Use `0.0.0.0` to open the kiosk from another device on the same network |

| Environment variable | Meaning |
|---|---|
| `SMARTBREW_DB` | Path of the SQLite file (default `smartbrew.db`) |
| `SMARTBREW_SECRET` | Session signing key (set your own outside the demo) |
| `SMARTBREW_LLM_URL`, `SMARTBREW_LLM_MODEL` | Optional local LLM for F7 (see §3.6) |

### 3.4 Test (automated)

```bash
python -m pytest -v                 # all 21 acceptance tests
python -m pytest -v -k f5           # only one functionality, e.g. F5
python tools/check_docs.py          # verify that every function is documented
```

| Functionality | Command | What the tests prove |
|---|---|---|
| F1 | `pytest -k f1` | Preferences survive logout/login, pre-fill drinks and can be edited; invalid or duplicate sign-ups are rejected |
| F2 | `pytest -k f2` | Password and card login, unknown card rejected, lockout after 3 failures, customers blocked from technician/admin functions |
| F3 | `pytest -k f3` | Options change price and ingredient amounts; invalid combinations are rejected |
| F4 | `pytest -k f4` | No milk → milk drinks sold out and refused at checkout; one milk missing → another milk is used |
| F5 | `pytest -k f5` | Suggestions reflect history, weather and the evening; still work when the weather service is down |
| F6 | `pytest -k f6` | Save, list, limit of 5, overwrite the oldest, login required |
| F7 | `pytest -k f7` | Five example sentences map to the right drink and options; nonsense is reported as not understood |
| F8 | `pytest -k f8` | Card, wallet and badge payments; inventory deducted; declined and invalid cards; offline queue and settlement |
| F9 | `pytest -k f9` | E-mail receipt reaches the outbox; the QR receipt page opens; invalid e-mail rejected |
| F10 | `pytest -k f10` | Full bin suspends the machine; technician empties and refills; error log filtering and resolving; 80 % warning |

### 3.5 Test manually through the GUI

**Screen layout:** the machine touchscreen is on the left and the **Demo control panel** on the right.

**Demo accounts**

| Role | E-mail / password | NFC card UID |
|---|---|---|
| Customer (has order history, badge balance 150 kr) | `alice@smartbrew.se` / `coffee123` | `CARD-ALICE` |
| Technician | `tech@smartbrew.se` / `tech123` | `TECH-01` |
| Admin | `admin@smartbrew.se` / `admin123` | `ADMIN-01` |

**Test cards:** `4242 4242 4242 4242` is approved and `4000 0000 0000 0002` is declined (insufficient funds).

Start with `python run.py --reset` so the data matches the steps below.

#### F1: Customer account and preferences
1. On the welcome screen, open the **Create account** tab.
2. Enter a name, an e-mail and a password (6+ characters). Choose preferences such as **Milk = Almond** and **Size = Large**. Press **Create account**.
3. ✅ You are logged in. Open **Caffe Latte**: almond milk and Large are already selected.
4. Click **Profile** at the top, change a preference and press **Save changes**. Log out and log back in: ✅ the change is kept.

#### F2: Login by password or card, with roles
1. **Tap card** tab → click **unknown card**: ✅ "Card not recognised".
2. Click **CARD-ALICE**: ✅ logged in as a customer (top bar shows *Menu* and *Profile*).
3. Log out. On the **Log in** tab, enter `alice@smartbrew.se` with a wrong password 3 times: ✅ the account is locked for 60 seconds, even with the correct password.
4. Tap **TECH-01**: ✅ the maintenance screen opens. Tap **ADMIN-01**: ✅ the diagnostics screen opens. Each role only sees its own functions.
5. **Continue as guest** lets you order without an account.

#### F3: Browse and customise
1. Log in as Alice and click any drink in the **Menu**.
2. Change the size, milk, strength, temperature, syrup and sugar: ✅ the **Total** price updates immediately (e.g. Large +8, oat milk +5, Strong +7).

#### F4: Ingredient availability
1. In the demo panel, under **Set ingredient empty**, choose *Whole milk* → **Empty it**. Repeat for *Oat milk* and *Almond milk*.
2. ✅ Every milk drink shows **Sold out** and lists the missing ingredient; Espresso and Americano can still be ordered.
3. If only one milk is emptied, ✅ the drinks stay available and switch to another milk automatically.

#### F5: Personalised recommendations
1. Log in as Alice and look at **Suggested for you**: ✅ the reasons mention her history ("you ordered it 4×") and the weather.
2. In the demo panel, set **Time of day** to *20:00*: ✅ Hot Chocolate and Chai Latte move up (low caffeine in the evening).
3. Change the **Outside temp** to 25: ✅ the reasons change, e.g. Espresso and Americano show "light choice for 25°C" instead of the cold-weather reasons.
4. Untick **Weather service online**: ✅ "weather unavailable – using clock & history", and suggestions still appear.

#### F6: Save custom drinks
1. Customise a drink and press **Save as my drink**. Enter a name, e.g. *Monday Fuel*.
2. ✅ It appears under **My saved drinks**. Press **Order** to go straight to payment.
3. After saving 5 drinks, saving a 6th asks whether to overwrite the oldest.

#### F7: Natural-language ordering
1. In **Just tell me what you want**, type `I need a big sweet coffee with no dairy` and press **Go**.
2. ✅ The result is a **Caffe Latte**: large, oat milk, sugar ×2. The screen also explains how the sentence was understood.
3. Press **Looks right – checkout**, or **Adjust** to fine-tune it.
4. More to try: `large strong flat white with almond milk`, `small black coffee`, `a warm chai with caramel`.

#### F8: Ordering and payment
1. Choose a drink → **Checkout**.
2. **Card** with `4242 4242 4242 4242` → **Pay**: ✅ approved, and the brewing steps play.
3. Try the card `4000 0000 0000 0002`: ✅ "Payment declined by bank: insufficient funds".
4. **Digital wallet** (Apple Pay / Google Pay / Swish): ✅ approved. **Work badge** `CARD-ALICE`: ✅ approved, and the badge balance goes down (see **Profile**).
5. **Offline:** untick **Network online** in the demo panel and pay by card: ✅ "stored offline, will settle when online". Tick it again: ✅ a toast says the payment was settled.

#### F9: Digital receipts
1. After a payment, when the drink is ready:
   * **E-mail it**: ✅ the receipt appears in the demo panel's **E-mail outbox**.
   * **Show QR code**: ✅ a QR code appears; scan it or click **open link** to see the online receipt.

#### F10: Maintenance, waste monitoring and diagnostics
1. In the demo panel, drag the **Coffee grounds bin** slider to 100 %: ✅ the status changes to **Out of service**, a red banner appears and checkout is blocked.
2. Log out, then tap **TECH-01**. Press **Empty & confirm** on the full bin: ✅ the machine is operational again.
3. Refill one ingredient with **Fill**, add a set amount with **Add**, or press **Refill all & reset trackers**: ✅ the levels go back up and sold-out drinks are available again.
4. In the demo panel, click **Pump** to inject a fault.
5. Log out, then tap **ADMIN-01**: ✅ the **Diagnostics** screen shows statistics and the full **Error history**. Filter it by **Severity** or **Component**, tick **open only**, and click **Resolve** on an issue.

Use **Reset demo database** at the bottom of the demo panel to start over at any time.

### 3.6 Optional: a real local LLM for F7

```bash
# install Ollama from https://ollama.com, then:
ollama pull llama3.2
SMARTBREW_LLM_URL=http://localhost:11434 python run.py          # macOS/Linux
set SMARTBREW_LLM_URL=http://localhost:11434 && python run.py   # Windows
```

The on-screen interpretation then reads *"local-llm"*. If the model is unavailable or its answer is invalid, the app falls back to the built-in parser automatically.

### 3.7 Troubleshooting

| Problem | Solution |
|---|---|
| `python3: command not found` (macOS) | Run `xcode-select --install`, or install Python from python.org |
| `'python' is not recognized` (Windows) | Reinstall Python with **"Add Python to PATH"** ticked |
| `Address already in use` | Another app uses the port: `python run.py --port 5002` |
| The page shows nothing or old data | Check the terminal is still running, then refresh; use `--reset` for fresh data |
| `ModuleNotFoundError: flask` | Activate the virtual environment first (`source .venv/bin/activate`) |

---

## 4. Code documentation conventions

* **Python:** every module, class and function has a Google-style docstring with a **description of its purpose**, an **`Args:`** section (expected inputs) and a **`Returns:`** section (output). Route functions also list their JSON body and HTTP status codes.
* **JavaScript:** every named function in `static/app.js` has a **JSDoc** block with a description, `@param` tags and `@returns`.
* `python tools/check_docs.py` checks this automatically (current result: 116/116 Python and 36/36 JavaScript functions documented).

---

## 5. Security notes

Passwords are hashed with PBKDF2 (Werkzeug) and sessions are signed. Every technician and admin endpoint checks the user's role, and forbidden attempts are logged. Accounts lock after repeated failed logins. Raw card numbers are never stored, only the last four digits and a random token. All user text is HTML-escaped in the UI.

## 6. LLM usage statement

The code of this prototype was generated with an LLM (Claude) from the Task A artefacts: the use-case model `G26Lab2.asta`, the user stories, the requirements and the product backlog. The full conversation is included with the submission, as the assignment requires.
