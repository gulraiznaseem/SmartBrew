"""Payment processing (R09), offline fallback (R16) and receipts (R08, R10)."""
import hashlib
import io
import secrets

import qrcode
import qrcode.image.svg

from .db import get_setting, log_event, now_iso

DECLINE_CARDS = {"4000000000000002"}      # simulated "insufficient funds"
WALLETS = {"apple_pay": "Apple Pay", "google_pay": "Google Pay", "swish": "Swish"}


class PaymentError(Exception):
    """Raised when a payment or receipt request is refused; ``code`` is a machine-readable reason."""

    def __init__(self, message, code="PAY-DECLINED"):
        """Create a payment error.

        Args:
            message (str): Message shown to the customer.
            code (str): Machine-readable error code (default ``"PAY-DECLINED"``).

        Returns:
            None.
        """
        super().__init__(message)
        self.code = code


def luhn_ok(number):
    """Validate a card number with the Luhn checksum.

    Args:
        number (str): Card number made of digits only (no spaces).

    Returns:
        bool: True if the number is 12+ digits and passes the checksum.
    """
    digits = [int(c) for c in number if c.isdigit()]
    if len(digits) < 12 or len(digits) != len(number):
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def _token(*parts):
    """Create a random one-way token so raw payment data is never stored.

    Args:
        *parts (str): Values to mix into the hash (e.g. the card number).

    Returns:
        str: Token such as ``"tok_1a2b3c4d5e6f7a8b"``.
    """
    return "tok_" + hashlib.sha256(("|".join(parts) + secrets.token_hex(4)).encode()).hexdigest()[:16]


def authorise(conn, method, amount, details):
    """Authorise a payment by card, digital wallet or prepaid work badge (R09).
    Badges are settled locally, so they work offline. Cards and wallets are
    queued as ``pending_offline`` when the network is down (R16).

    Args:
        conn (sqlite3.Connection): Open database connection.
        method (str): ``"card"``, ``"wallet"`` or ``"badge"``.
        amount (float): Amount to charge in kronor.
        details (dict): Method data: ``card_number``, ``wallet`` or ``badge_uid``.

    Returns:
        tuple[str, str, str]: (status, transaction_id or token, display label),
        where status is ``"approved"`` or ``"pending_offline"``.

    Raises:
        PaymentError: If the card is invalid or declined, the badge is unknown
            or its balance is too low, or the method is unsupported.
    """
    online = get_setting(conn, "network_online") == "1"

    if method == "badge":
        uid = (details.get("badge_uid") or "").strip()
        user = conn.execute("SELECT * FROM users WHERE card_uid=?", (uid,)).fetchone()
        if not user:
            raise PaymentError("Badge not recognised – please re-tap or use another method", "PAY-BADGE-UNKNOWN")
        if user["badge_balance"] < amount:
            raise PaymentError(f"Insufficient badge balance ({user['badge_balance']:.0f} kr)", "PAY-BADGE-FUNDS")
        conn.execute("UPDATE users SET badge_balance = badge_balance - ? WHERE id=?", (amount, user["id"]))
        return "approved", "BDG-" + secrets.token_hex(4).upper(), f"Work badge ({user['name']})"

    if method == "card":
        number = (details.get("card_number") or "").replace(" ", "")
        if not luhn_ok(number):
            raise PaymentError("Card number is invalid", "PAY-CARD-INVALID")
        label = f"Card •••• {number[-4:]}"
        ref = _token(number)
    elif method == "wallet":
        provider = details.get("wallet", "apple_pay")
        if provider not in WALLETS:
            raise PaymentError("Unsupported wallet", "PAY-WALLET")
        label = WALLETS[provider]
        ref = _token(provider)
    else:
        raise PaymentError("Unknown payment method", "PAY-METHOD")

    if not online:
        log_event(conn, "warning", "payment", "PAY-OFFLINE",
                  f"Network down – {label} payment of {amount:.0f} kr stored for deferred settlement")
        return "pending_offline", ref, label + " (offline – will settle later)"

    if method == "card" and number in DECLINE_CARDS:
        log_event(conn, "warning", "payment", "PAY-DECLINED", f"Gateway declined {label}: insufficient funds")
        raise PaymentError("Payment declined by bank: insufficient funds")
    return "approved", "TXN-" + secrets.token_hex(5).upper(), label


def settle_offline_queue(conn):
    """Settle every payment that was stored while the machine was offline.
    Called automatically when the network comes back.

    Args:
        conn (sqlite3.Connection): Open database connection.

    Returns:
        int: Number of payments settled.
    """
    pending = conn.execute("SELECT * FROM offline_queue WHERE settled=0").fetchall()
    for p in pending:
        txn = "TXN-" + secrets.token_hex(5).upper()
        conn.execute("UPDATE offline_queue SET settled=1 WHERE id=?", (p["id"],))
        conn.execute("UPDATE orders SET payment_status='approved', transaction_id=? WHERE id=?", (txn, p["order_id"]))
    if pending:
        log_event(conn, "info", "payment", "PAY-SETTLED", f"Network restored – settled {len(pending)} offline payment(s)")
    return len(pending)


# ---------------- receipts ----------------
def receipt_text(order):
    """Format an order as a plain-text receipt, including VAT.

    Args:
        order (dict): Order with options decoded, plus ``payment_label``.

    Returns:
        str: Multi-line receipt text.
    """
    opts = order["options"]
    lines = [
        "SmartBrew – digital receipt",
        f"Order #{order['id']}   {order['created_at'].replace('T', ' ')}",
        f"{order['drink_name']}  ({opts.get('size', 'M')}, {opts.get('milk', 'none').replace('_', ' ')}, "
        f"strength {opts.get('strength', 2)}, {opts.get('temperature', 'hot').replace('_', ' ')}"
        + (f", {opts['syrup'].replace('_', ' ')}" if opts.get("syrup", "none") != "none" else "")
        + (f", sugar ×{opts['sugar']}" if opts.get("sugar") else "") + ")",
        f"Total: {order['price']:.2f} kr (incl. 12% VAT {order['price'] * 0.12 / 1.12:.2f} kr)",
        f"Paid with: {order['payment_label']}",
        f"Status: {order['payment_status']}   Ref: {order['transaction_id']}",
    ]
    return "\n".join(lines)


def make_qr_svg(data):
    """Generate a QR code as an SVG image.

    Args:
        data (str): Text to encode (here, the receipt URL).

    Returns:
        str: SVG markup of the QR code.
    """
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def issue_receipt(conn, order, method, email, base_url):
    """Issue a digital receipt by e-mail (to the simulated outbox) or as a QR
    code that links to the online receipt page (R10).

    Args:
        conn (sqlite3.Connection): Open database connection.
        order (dict): The paid order.
        method (str): ``"email"`` or ``"qr"``.
        email (str | None): Recipient address (required for e-mail).
        base_url (str): Server URL used to build the receipt link.

    Returns:
        dict: method, url, token, text, plus ``sent_to`` (e-mail) or
        ``qr_svg`` (QR).

    Raises:
        PaymentError: If the e-mail address is invalid or the method is unknown.
    """
    token = secrets.token_urlsafe(10)
    url = f"{base_url.rstrip('/')}/receipt/{token}"
    if method == "email":
        if not email or "@" not in email:
            raise PaymentError("A valid e-mail address is required", "RCPT-EMAIL")
        conn.execute("INSERT INTO outbox (recipient,subject,body,ts) VALUES (?,?,?,?)",
                     (email, f"Your SmartBrew receipt #{order['id']}", receipt_text(order) + f"\n\nView online: {url}", now_iso()))
        result = {"method": "email", "sent_to": email}
    elif method == "qr":
        result = {"method": "qr", "qr_svg": make_qr_svg(url)}
    else:
        raise PaymentError("Receipt method must be email or qr", "RCPT-METHOD")
    conn.execute("INSERT INTO receipts (token,order_id,method,destination,created_at) VALUES (?,?,?,?,?)",
                 (token, order["id"], method, email if method == "email" else None, now_iso()))
    return {**result, "url": url, "token": token, "text": receipt_text(order)}
