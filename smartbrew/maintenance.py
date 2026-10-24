"""Technician & admin operations: refills (R11), waste monitoring + safety shutdown (R12, R13),
low-stock alerts and the diagnostic error log (R14)."""
from .db import get_setting, log_event, set_setting

WARN_PCT = 80
GROUNDS_PER_GRAM_BEANS = 1.0      # spent puck weight ≈ dose weight (wet)
DRIP_ML_PER_DRINK = 15


def bins(conn):
    """Read every waste bin with its fill percentage and state (R12).

    Args:
        conn (sqlite3.Connection): Open database connection.

    Returns:
        list[dict]: Bin rows plus ``percent`` and ``state``
        (``ok``, ``warning`` at 80 %+, ``critical`` at 100 %).
    """
    out = []
    for r in conn.execute("SELECT * FROM waste_bins ORDER BY name"):
        pct = round(100 * r["level"] / r["capacity"], 1)
        state = "critical" if pct >= 100 else "warning" if pct >= WARN_PCT else "ok"
        out.append({**dict(r), "percent": pct, "state": state})
    return out


def ingredient_status(conn):
    """Read every ingredient with its fill percentage and stock state.

    Args:
        conn (sqlite3.Connection): Open database connection.

    Returns:
        list[dict]: Ingredient rows plus ``percent`` and ``state``
        (``ok``, ``low`` or ``empty``).
    """
    out = []
    for r in conn.execute("SELECT * FROM ingredients ORDER BY label"):
        pct = round(100 * r["level"] / r["capacity"], 1)
        state = "empty" if r["level"] <= 0 else "low" if r["level"] <= r["low_threshold"] else "ok"
        out.append({**dict(r), "percent": pct, "state": state})
    return out


def machine_status(conn):
    """Return whether the machine is currently able to brew.

    Args:
        conn (sqlite3.Connection): Open database connection.

    Returns:
        str: ``"operational"`` or ``"suspended"``.
    """
    return get_setting(conn, "machine_status")


def evaluate_bins(conn):
    """Suspend the machine when a bin is full and resume it when none is (R13).
    Logs a critical event on suspension and an info event on resume.

    Args:
        conn (sqlite3.Connection): Open database connection.

    Returns:
        list[dict]: Current bin states (see :func:`bins`).
    """
    critical = [b for b in bins(conn) if b["state"] == "critical"]
    status = machine_status(conn)
    if critical and status != "suspended":
        set_setting(conn, "machine_status", "suspended")
        for b in critical:
            log_event(conn, "critical", "waste", "WST-FULL",
                      f"{b['label']} is full ({b['percent']}%) – beverage preparation suspended")
    elif not critical and status == "suspended":
        set_setting(conn, "machine_status", "operational")
        log_event(conn, "info", "waste", "WST-RESUMED", "All waste bins below capacity – dispensing resumed")
    return bins(conn)


def record_brew_waste(conn, need):
    """Add the waste produced by a brewed drink (coffee grounds and drip water)
    to the bins, warn when a bin crosses 80 % and re-check for shutdown.

    Args:
        conn (sqlite3.Connection): Open database connection.
        need (dict[str, float]): Ingredient amounts used by the drink.

    Returns:
        None.
    """
    before = {b["name"]: b["state"] for b in bins(conn)}
    grounds = need.get("coffee_beans", 0) * GROUNDS_PER_GRAM_BEANS + need.get("chai", 0)
    conn.execute("UPDATE waste_bins SET level = MIN(level + ?, capacity * 1.0) WHERE name='grounds'", (grounds,))
    conn.execute("UPDATE waste_bins SET level = MIN(level + ?, capacity * 1.0) WHERE name='drip_tray'", (DRIP_ML_PER_DRINK,))
    for b in bins(conn):
        if b["state"] == "warning" and before.get(b["name"]) == "ok":
            log_event(conn, "warning", "waste", "WST-WARN", f"{b['label']} at {b['percent']}% – please empty soon")
    evaluate_bins(conn)


def check_low_stock(conn, consumed):
    """Log a low-stock warning (or an error if empty) for each ingredient that
    just fell to or below its threshold.

    Args:
        conn (sqlite3.Connection): Open database connection.
        consumed (dict[str, float]): Ingredient amounts just used.

    Returns:
        None.
    """
    for r in conn.execute("SELECT * FROM ingredients"):
        if r["name"] in consumed and r["level"] <= r["low_threshold"]:
            before = r["level"] + consumed[r["name"]]
            if before > r["low_threshold"]:
                sev = "error" if r["level"] <= 0 else "warning"
                log_event(conn, sev, "inventory", "INV-LOW",
                          f"{r['label']} low: {r['level']:.0f} {r['unit']} left (threshold {r['low_threshold']:.0f})")


def refill(conn, ingredient, amount=None, technician="technician", log=True):
    """Record a technician refill of one ingredient and resolve its open stock
    alerts (R11).

    Args:
        conn (sqlite3.Connection): Open database connection.
        ingredient (str): Ingredient name, e.g. ``"oat_milk"``.
        amount (float | None): Amount to add; None fills it to capacity.
        technician (str): Name written to the audit log.
        log (bool): Whether to write an audit-log entry (default True).

    Returns:
        dict: ingredient, before, after and capacity levels.

    Raises:
        ValueError: If the ingredient is unknown or the amount is not positive.
    """
    row = conn.execute("SELECT * FROM ingredients WHERE name=?", (ingredient,)).fetchone()
    if not row:
        raise ValueError(f"Unknown ingredient {ingredient}")
    if amount is None:
        new_level = row["capacity"]
    else:
        amount = float(amount)
        if amount <= 0:
            raise ValueError("Refill amount must be positive")
        new_level = min(row["capacity"], row["level"] + amount)
    conn.execute("UPDATE ingredients SET level=? WHERE name=?", (new_level, ingredient))
    conn.execute("UPDATE error_log SET resolved=1 WHERE component='inventory' AND resolved=0 AND message LIKE ?", (f"%{row['label']}%",))
    if log:
        log_event(conn, "info", "maintenance", "MNT-REFILL",
              f"{technician} refilled {row['label']}: {row['level']:.0f} → {new_level:.0f} {row['unit']}")
    return {"ingredient": ingredient, "before": row["level"], "after": new_level, "capacity": row["capacity"]}


def refill_all(conn, technician="technician"):
    """Fill every ingredient to capacity ("refill and reset trackers") and
    write one summary entry to the audit log.

    Args:
        conn (sqlite3.Connection): Open database connection.
        technician (str): Name written to the audit log.

    Returns:
        list[dict]: One :func:`refill` result per ingredient.
    """
    res = [refill(conn, r["name"], None, technician, log=False) for r in conn.execute("SELECT name FROM ingredients").fetchall()]
    changed = [r["ingredient"] for r in res if r["after"] != r["before"]]
    log_event(conn, "info", "maintenance", "MNT-REFILL",
              f"{technician} refilled all supplies and reset trackers ({len(changed)} topped up: {', '.join(changed) or 'none'})")
    return res


def empty_bin(conn, name, technician="technician"):
    """Record that a technician emptied a waste bin; resumes the machine if no
    bin is full any more.

    Args:
        conn (sqlite3.Connection): Open database connection.
        name (str): Bin name, ``"grounds"`` or ``"drip_tray"``.
        technician (str): Name written to the audit log.

    Returns:
        list[dict]: Updated bin states.

    Raises:
        ValueError: If the bin name is unknown.
    """
    row = conn.execute("SELECT * FROM waste_bins WHERE name=?", (name,)).fetchone()
    if not row:
        raise ValueError(f"Unknown bin {name}")
    conn.execute("UPDATE waste_bins SET level=0 WHERE name=?", (name,))
    log_event(conn, "info", "maintenance", "MNT-EMPTY", f"{technician} emptied {row['label']} (was {row['level']:.0f} {row['unit']})")
    evaluate_bins(conn)
    return bins(conn)


def error_log(conn, severity=None, component=None, include_resolved=True, limit=200):
    """Query the error history for the admin dashboard (R14), newest first.

    Args:
        conn (sqlite3.Connection): Open database connection.
        severity (str | None): Only this severity, or None for all.
        component (str | None): Only this component, or None for all.
        include_resolved (bool): If False, return only open issues.
        limit (int): Maximum number of rows (default 200).

    Returns:
        list[dict]: Log entries (id, ts, severity, component, code, message, resolved).
    """
    q, args = "SELECT * FROM error_log WHERE 1=1", []
    if severity:
        q += " AND severity=?"; args.append(severity)
    if component:
        q += " AND component=?"; args.append(component)
    if not include_resolved:
        q += " AND resolved=0"
    q += " ORDER BY id DESC LIMIT ?"; args.append(limit)
    return [dict(r) for r in conn.execute(q, args)]


def error_summary(conn):
    """Count the open (unresolved) issues by severity and by component.

    Args:
        conn (sqlite3.Connection): Open database connection.

    Returns:
        dict: ``open_by_severity`` and ``open_by_component`` (name -> count).
    """
    rows = conn.execute("SELECT severity, component, COUNT(*) n FROM error_log WHERE resolved=0 GROUP BY severity, component").fetchall()
    by_sev, by_comp = {}, {}
    for r in rows:
        by_sev[r["severity"]] = by_sev.get(r["severity"], 0) + r["n"]
        by_comp[r["component"]] = by_comp.get(r["component"], 0) + r["n"]
    return {"open_by_severity": by_sev, "open_by_component": by_comp}


FAULTS = {
    "heater": ("error", "HTR-TEMP", "Water heater did not reach target temperature (62°C < 70°C)"),
    "grinder": ("error", "GRD-JAM", "Grinder motor stalled – possible bean jam"),
    "pump": ("critical", "PMP-PRES", "Pump pressure dropped below 6 bar"),
    "nfc": ("warning", "NFC-READ", "NFC reader returned CRC error"),
}


def simulate_fault(conn, component):
    """Inject a simulated hardware fault for the demo (heater, grinder, pump or NFC).

    Args:
        conn (sqlite3.Connection): Open database connection.
        component (str): ``"heater"``, ``"grinder"``, ``"pump"`` or ``"nfc"``.

    Returns:
        dict: The logged severity, code and message.

    Raises:
        ValueError: If the component is unknown.
    """
    if component not in FAULTS:
        raise ValueError("Unknown component")
    sev, code, msg = FAULTS[component]
    log_event(conn, sev, component, code, msg)
    return {"severity": sev, "code": code, "message": msg}
