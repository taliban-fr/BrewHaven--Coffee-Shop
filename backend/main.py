import hashlib
import math
import os
import re
import secrets
import smtplib
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import bcrypt
import jwt
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel, Field

load_dotenv()  # reads backend/.env. This must run before `import mpesa`.

import mpesa  # noqa: E402

ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.getenv("ADMIN_EMAILS", "kmikekioko1@gmail.com").split(",")
    if e.strip()
}
VALID_STATUSES = {"pending", "preparing", "ready", "completed", "cancelled"}

SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-in-production")
ALGORITHM = "HS256"
TOKEN_HOURS = 24
RESET_MINUTES = 30
DB_FILE = "brewhaven.db"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RESET_PAGE_URL = os.getenv("RESET_PAGE_URL", "http://127.0.0.1:5501/frontend/reset.html")
GOOGLE_CLIENT_ID = os.getenv(
    "GOOGLE_CLIENT_ID", "776335343723-6ctb6270u31ii0rp8vr8j3qv2g0fqdcj.apps.googleusercontent.com"
)

# Email: if SMTP_HOST is empty, emails are printed in this terminal instead of sent
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)

STATUS_MESSAGES = {
    "preparing": ("We're preparing your order", "Good news! We've started preparing your order."),
    "ready": ("Your order is ready for pickup", "Your order is ready! Come and pick it up whenever you like."),
    "cancelled": (
        "Your order was cancelled",
        "Your order has been cancelled. If you weren't expecting this, please contact us.",
    ),
}

SEED_PRODUCTS = [
    ("Ethiopian Yirgacheffe", "Floral notes with bright citrus acidity and a tea-like body. Light roast.", 1899, "1.jpeg", "BESTSELLER", 4.5),
    ("Cappuccino", "Bold espresso balanced with equal parts steamed milk and a thick layer of velvety microfoam.", 2100, "2.jpg", None, 5),
    ("Espresso", "A concentrated, full-bodied shot brewed under high pressure with a signature golden crema.", 1800, "3.jpg", None, 4),
    ("Americano", "Rich espresso diluted with hot water for a smooth, traditional brewed-coffee profile.", 1900, "4.jpg", None, 4.5),
    ("The Café Latte", "A smooth, milk-forward drink featuring espresso paired with a generous volume of steamed milk and a thin layer of foam.", 2150, "5.jpg", None, 5),
    ("Macchiato", "An intense shot of espresso marked with a small dollop of velvety milk foam.", 2150, "6.jpg", "NEW", 4.5),
]

app = FastAPI(title="BrewHaven API")

app.add_middleware(
    CORSMiddleware,
allow_origins=[
    "http://127.0.0.1:5500", "http://localhost:5500",
    "http://127.0.0.1:5501", "http://localhost:5501",
    "https://brewhaven-shop.netlify.app",
],
    allow_methods=["*"],
    allow_headers=["*"],
)

bearer = HTTPBearer()
optional_bearer = HTTPBearer(auto_error=False)


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(get_db()) as conn, conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS password_resets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                price_cents INTEGER NOT NULL,
                image TEXT NOT NULL,
                tag TEXT,
                rating REAL NOT NULL DEFAULT 5,
                active INTEGER NOT NULL DEFAULT 1
            )"""
        )
        if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO products (name, description, price_cents, image, tag, rating) VALUES (?, ?, ?, ?, ?, ?)",
                SEED_PRODUCTS,
            )

        # Upgrade an older orders table so orders can belong to guests
        old_cols = [r["name"] for r in conn.execute("PRAGMA table_info(orders)")]
        if old_cols and "track_token" not in old_cols:
            conn.execute("ALTER TABLE orders RENAME TO orders_old")

        conn.execute(
            """CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                customer_name TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                total_cents INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                note TEXT NOT NULL DEFAULT '',
                track_token TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )

        has_old = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'orders_old'"
        ).fetchone()
        if has_old:
            conn.execute(
                """INSERT INTO orders
                    (id, user_id, customer_name, phone, email, total_cents, status, note, track_token, created_at)
                   SELECT o.id, o.user_id, COALESCE(u.name, ''), '', COALESCE(u.email, ''),
                          o.total_cents, o.status, o.note, lower(hex(randomblob(16))), o.created_at
                   FROM orders_old o LEFT JOIN users u ON u.id = o.user_id"""
            )
            conn.execute("DROP TABLE orders_old")

                # Payment columns, added later, so older databases need them added
        order_cols = {r["name"] for r in conn.execute("PRAGMA table_info(orders)")}
        for column, definition in (
            ("payment_method", "TEXT NOT NULL DEFAULT 'cash'"),
            ("payment_status", "TEXT NOT NULL DEFAULT 'unpaid'"),
            ("mpesa_checkout_id", "TEXT"),
            ("mpesa_receipt", "TEXT"),
            ("payment_started_at", "TEXT"),
        ):
            if column not in order_cols:
                conn.execute(f"ALTER TABLE orders ADD COLUMN {column} {definition}")

        conn.execute(
            """CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                price_cents INTEGER NOT NULL,
                quantity INTEGER NOT NULL
            )"""
        )


init_db()


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    email: str = Field(max_length=120)
    password: str = Field(min_length=8, max_length=72)


class LoginIn(BaseModel):
    email: str
    password: str


class ForgotIn(BaseModel):
    email: str = Field(max_length=120)


class ResetIn(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=72)


class GoogleIn(BaseModel):
    credential: str


class ContactIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    email: str = Field(max_length=120)
    message: str = Field(min_length=5, max_length=2000)


class OrderItemIn(BaseModel):
    product_id: int
    quantity: int = Field(ge=1, le=20)


class OrderIn(BaseModel):
    items: list[OrderItemIn] = Field(min_length=1, max_length=30)
    note: str = Field(default="", max_length=300)
    name: str = Field(default="", max_length=60)
    phone: str = Field(default="", max_length=25)
    email: str = Field(default="", max_length=120)
    payment_method: str = Field(default="cash", pattern="^(cash|mpesa)$")


class ProductIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(min_length=5, max_length=400)
    price_cents: int = Field(ge=1, le=1_000_000)
    image: str = Field(min_length=1, max_length=100)
    tag: str | None = Field(default=None, max_length=20)
    rating: float = Field(default=5, ge=0, le=5)
    active: bool = True


class StatusIn(BaseModel):
    status: str

class PayIn(BaseModel):
    token: str
    phone: str = Field(default="", max_length=25)


def money(cents: int) -> str:
    text = f"{cents / 100:,.2f}".removesuffix(".00")
    return f"KSh {text}"


def normalise_phone(raw: str) -> str | None:
    """Accepts 0712 345 678, +254712345678 or 254712345678. Returns 254712345678."""
    digits = re.sub(r"[\s\-()]", "", raw)
    match = re.fullmatch(r"(?:\+?254|0)([17]\d{8})", digits)
    return f"254{match.group(1)}" if match else None


def send_email(to: str, subject: str, body: str):
    if not to:
        return
    if not SMTP_HOST:
        print(f"\n[EMAIL to {to}] {subject}\n{body}\n")
        return
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    except Exception as exc:
        print(f"[EMAIL FAILED to {to}] {exc}")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def make_token(user_id: int, name: str) -> str:
    payload = {
        "sub": str(user_id),
        "name": name,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def session_response(user_id: int, name: str) -> dict:
    return {"access_token": make_token(user_id, name), "token_type": "bearer", "name": name}


def decode_token(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    return decode_token(creds.credentials)


def optional_user(creds: HTTPAuthorizationCredentials | None = Depends(optional_bearer)):
    # No token at all means a guest. A token that is present but bad is rejected.
    return decode_token(creds.credentials) if creds else None


def is_admin_user(user_id: int) -> bool:
    with closing(get_db()) as conn:
        row = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    return row is not None and row["email"].lower() in ADMIN_EMAILS


def require_admin(user=Depends(current_user)):
    if not is_admin_user(int(user["sub"])):
        raise HTTPException(status_code=403, detail="Admins only")
    return user


def clean_product(data: ProductIn):
    if not re.match(r"^[\w.\- ]+$", data.image):
        raise HTTPException(status_code=400, detail="Image must be a plain file name like 1.jpeg")
    tag = (data.tag or "").strip().upper() or None
    return (
        data.name.strip(), data.description.strip(), data.price_cents,
        data.image.strip(), tag, data.rating, int(data.active),
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={"detail": "Please check your details and try again"},
    )


@app.post("/register", status_code=201)
def register(data: RegisterIn):
    email = data.email.strip().lower()
    name = data.name.strip()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    if len(data.password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="Password is too long")

    try:
        with closing(get_db()) as conn, conn:
            cur = conn.execute(
                "INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (name, email, hash_password(data.password), datetime.now(timezone.utc).isoformat()),
            )
            user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Email already registered")

    return session_response(user_id, name)


@app.post("/login")
def login(data: LoginIn):
    email = data.email.strip().lower()
    with closing(get_db()) as conn:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    try:
        ok = user is not None and bcrypt.checkpw(
            data.password.encode("utf-8"), user["password_hash"].encode("utf-8")
        )
    except ValueError:  # also covers Google-only accounts, which have no password
        ok = False
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    return session_response(user["id"], user["name"])


@app.post("/forgot-password")
def forgot_password(data: ForgotIn, background_tasks: BackgroundTasks):
    email = data.email.strip().lower()
    with closing(get_db()) as conn, conn:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if user:
            token = secrets.token_urlsafe(32)
            expires = datetime.now(timezone.utc) + timedelta(minutes=RESET_MINUTES)
            conn.execute(
                "INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
                (user["id"], hash_token(token), expires.isoformat()),
            )
            link = f"{RESET_PAGE_URL}?token={token}"
            background_tasks.add_task(
                send_email,
                email,
                "Reset your BrewHaven password",
                f"Hi,\n\nUse this link to set a new password (valid for {RESET_MINUTES} minutes):\n"
                f"{link}\n\nIf you didn't ask for this, you can ignore this email.\n",
            )
    # Same answer whether or not the email exists, so nobody can probe for accounts
    return {"message": "If that email is registered, a reset link has been sent."}


@app.post("/reset-password")
def reset_password(data: ResetIn):
    if len(data.password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="Password is too long")

    with closing(get_db()) as conn, conn:
        row = conn.execute(
            "SELECT * FROM password_resets WHERE token_hash = ?", (hash_token(data.token),)
        ).fetchone()
        if (
            row is None
            or row["used"]
            or datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc)
        ):
            raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")

        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(data.password), row["user_id"]),
        )
        conn.execute("UPDATE password_resets SET used = 1 WHERE user_id = ?", (row["user_id"],))

    return {"message": "Password updated. You can now log in."}


@app.post("/auth/google")
def google_login(data: GoogleIn):
    try:
        info = google_id_token.verify_oauth2_token(
            data.credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=10,
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Google sign-in failed. Please try again.")

    if not info.get("email_verified"):
        raise HTTPException(status_code=401, detail="Your Google email is not verified")

    email = info["email"].lower()
    name = info.get("name") or email.split("@")[0]

    with closing(get_db()) as conn, conn:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user is None:
            cur = conn.execute(
                "INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (name, email, "!", datetime.now(timezone.utc).isoformat()),
            )
            return session_response(cur.lastrowid, name)
        return session_response(user["id"], user["name"])


@app.post("/contact", status_code=201)
def contact(data: ContactIn):
    email = data.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    with closing(get_db()) as conn, conn:
        conn.execute(
            "INSERT INTO messages (name, email, message, created_at) VALUES (?, ?, ?, ?)",
            (data.name.strip(), email, data.message.strip(), datetime.now(timezone.utc).isoformat()),
        )
    return {"message": "Thanks! We'll get back to you soon."}


@app.get("/products")
def list_products():
    with closing(get_db()) as conn:
        rows = conn.execute(
            "SELECT id, name, description, price_cents, image, tag, rating "
            "FROM products WHERE active = 1 ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]

STALE_PAYMENT_MINUTES = 10

def expire_stale_payments():
    """Mark M-Pesa payments that never completed as failed."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=STALE_PAYMENT_MINUTES)
    ).isoformat()
    with closing(get_db()) as conn, conn:
        conn.execute(
            "UPDATE orders SET payment_status = 'failed' "
            "WHERE payment_status = 'pending' "
            "AND COALESCE(payment_started_at, created_at) < ?",
            (cutoff,),
        )

def refresh_payment(order_id: int, checkout_id: str) -> str:
    """Ask Safaricom about a pending payment and save the answer."""
    try:
        result = mpesa.stk_query(checkout_id)
    except mpesa.MpesaError:
        return "pending"
    with closing(get_db()) as conn, conn:
        if result in ("paid", "failed"):
            conn.execute(
                "UPDATE orders SET payment_status = ? WHERE id = ? AND payment_status = 'pending'",
                (result, order_id),
            )
        row = conn.execute(
            "SELECT payment_status FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
    return row["payment_status"]


@app.get("/config")
def public_config():
    return {"mpesa_enabled": mpesa.ENABLED}


@app.post("/orders", status_code=201)
def create_order(data: OrderIn, background_tasks: BackgroundTasks, user=Depends(optional_user)):
    wanted: dict[int, int] = {}
    for item in data.items:
        wanted[item.product_id] = wanted.get(item.product_id, 0) + item.quantity

    phone = ""
    if data.phone.strip():
        phone = normalise_phone(data.phone)
        if phone is None:
            raise HTTPException(
                status_code=400, detail="Enter a valid Kenyan phone number, e.g. 0712 345 678"
            )

    pay_with_mpesa = data.payment_method == "mpesa"
    if pay_with_mpesa:
        if not mpesa.ENABLED:
            raise HTTPException(
                status_code=400,
                detail="M-Pesa payments aren't available right now. Please pay at pickup.",
            )
        if not phone:
            raise HTTPException(
                status_code=400, detail="Enter the phone number to pay with M-Pesa"
            )

    with closing(get_db()) as conn, conn:
        if user:
            row = conn.execute(
                "SELECT name, email FROM users WHERE id = ?", (int(user["sub"]),)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=401, detail="Please log in again")
            user_id, name, email = int(user["sub"]), row["name"], row["email"]
        else:
            name = data.name.strip()
            email = data.email.strip().lower()
            if len(name) < 2:
                raise HTTPException(status_code=400, detail="Please enter your name")
            if not phone:
                raise HTTPException(status_code=400, detail="Please enter your phone number")
            if email and not EMAIL_RE.match(email):
                raise HTTPException(status_code=400, detail="Enter a valid email address")
            user_id = None

        marks = ",".join("?" * len(wanted))
        rows = conn.execute(
            f"SELECT id, name, price_cents FROM products WHERE active = 1 AND id IN ({marks})",
            list(wanted),
        ).fetchall()
        if len(rows) != len(wanted):
            raise HTTPException(
                status_code=400, detail="Some items in your cart are no longer available"
            )

        total = sum(r["price_cents"] * wanted[r["id"]] for r in rows)
        track_token = secrets.token_hex(16)
        cur = conn.execute(
            "INSERT INTO orders (user_id, customer_name, phone, email, total_cents, status, note, "
            "track_token, created_at, payment_method, payment_status) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
            (
                user_id, name, phone, email, total, data.note.strip(), track_token,
                datetime.now(timezone.utc).isoformat(),
                data.payment_method, "pending" if pay_with_mpesa else "unpaid",
            ),
        )
        order_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO order_items (order_id, product_id, name, price_cents, quantity) VALUES (?, ?, ?, ?, ?)",
            [(order_id, r["id"], r["name"], r["price_cents"], wanted[r["id"]]) for r in rows],
        )

    # The order is saved. Now start the M-Pesa payment, if the customer chose it.
    payment_status = "pending" if pay_with_mpesa else "unpaid"
    payment_error = None
    if pay_with_mpesa:
        try:
            checkout_id = mpesa.stk_push(order_id, phone, max(1, math.ceil(total / 100)))
            with closing(get_db()) as conn, conn:
                conn.execute(
                    "UPDATE orders SET mpesa_checkout_id = ?, payment_started_at = ? WHERE id = ?",
                     (checkout_id, datetime.now(timezone.utc).isoformat(), order_id),
                )
        except mpesa.MpesaError as exc:
            payment_status = "failed"
            payment_error = str(exc)
            with closing(get_db()) as conn, conn:
                conn.execute("UPDATE orders SET payment_status = 'failed' WHERE id = ?", (order_id,))

    lines = "\n".join(f"  {wanted[r['id']]} x {r['name']}" for r in rows)
    first = name.split(" ")[0]
    pay_line = (
        "Payment: M-Pesa. We'll confirm once it goes through."
        if pay_with_mpesa
        else "Pay when you pick up."
    )
    if email:
        background_tasks.add_task(
            send_email,
            email,
            f"We got your order #{order_id}",
            f"Hi {first},\n\nThanks for ordering from BrewHaven!\n\nOrder #{order_id}\n{lines}\n"
            f"Total: {money(total)}\n\n{pay_line} We'll email you when it's ready.\n",
        )
    for admin_email in ADMIN_EMAILS:
        background_tasks.add_task(
            send_email,
            admin_email,
            f"New order #{order_id} ({money(total)})",
            f"Customer: {name}\nPhone: {phone or '-'}\nEmail: {email or '-'}\n\n{lines}\n"
            f"Total: {money(total)}\nPayment: {data.payment_method}\n"
            f"Note: {data.note.strip() or '-'}\n",
        )

    return {
        "id": order_id,
        "total_cents": total,
        "status": "pending",
        "track_token": track_token,
        "payment_method": data.payment_method,
        "payment_status": payment_status,
        "payment_error": payment_error,
    }

@app.get("/orders/mine")
def my_orders(user=Depends(current_user)):
    expire_stale_payments()
    with closing(get_db()) as conn:
        orders = conn.execute(
            "SELECT id, total_cents, status, note, created_at, payment_method, payment_status, "
            "mpesa_receipt FROM orders WHERE user_id = ? ORDER BY id DESC",
            (int(user["sub"]),),
        ).fetchall()
        result = []
        for o in orders:
            items = conn.execute(
                "SELECT name, price_cents, quantity FROM order_items WHERE order_id = ?",
                (o["id"],),
            ).fetchall()
            result.append({**dict(o), "items": [dict(i) for i in items]})
    return result


@app.get("/orders/track/{order_id}")
def track_order(order_id: int, token: str):
    with closing(get_db()) as conn:
        o = conn.execute(
            "SELECT id, total_cents, status, note, created_at, track_token, payment_method, "
            "payment_status, mpesa_checkout_id, mpesa_receipt FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        if o is None or not secrets.compare_digest(
            o["track_token"].encode("utf-8"), token.encode("utf-8")
        ):
            raise HTTPException(status_code=404, detail="Order not found")
        items = conn.execute(
            "SELECT name, price_cents, quantity FROM order_items WHERE order_id = ?",
            (order_id,),
        ).fetchall()

    order = dict(o)
    order.pop("track_token")
    checkout_id = order.pop("mpesa_checkout_id")
    if order["payment_status"] == "pending" and checkout_id:
        order["payment_status"] = refresh_payment(order_id, checkout_id)
        with closing(get_db()) as conn:
            row = conn.execute(
                "SELECT mpesa_receipt FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
        order["mpesa_receipt"] = row["mpesa_receipt"]
    order["items"] = [dict(i) for i in items]
    return order


@app.post("/orders/{order_id}/pay")
def retry_payment(order_id: int, data: PayIn):
    if not mpesa.ENABLED:
        raise HTTPException(status_code=400, detail="M-Pesa payments aren't available right now")

    with closing(get_db()) as conn:
        o = conn.execute(
            "SELECT id, total_cents, phone, status, payment_status, track_token FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
    if o is None or not secrets.compare_digest(
        o["track_token"].encode("utf-8"), data.token.encode("utf-8")
    ):
        raise HTTPException(status_code=404, detail="Order not found")
    if o["payment_status"] == "paid":
        raise HTTPException(status_code=400, detail="This order is already paid")
    if o["status"] == "cancelled":
        raise HTTPException(status_code=400, detail="This order was cancelled")

    phone = o["phone"]
    if data.phone.strip():
        phone = normalise_phone(data.phone)
        if phone is None:
            raise HTTPException(
                status_code=400, detail="Enter a valid Kenyan phone number, e.g. 0712 345 678"
            )
    if not phone:
        raise HTTPException(status_code=400, detail="Enter the phone number to pay with")

    try:
        checkout_id = mpesa.stk_push(order_id, phone, max(1, math.ceil(o["total_cents"] / 100)))
    except mpesa.MpesaError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    with closing(get_db()) as conn, conn:
        conn.execute(
            "UPDATE orders SET payment_method = 'mpesa', payment_status = 'pending', "
            "mpesa_checkout_id = ?, phone = ?, payment_started_at = ? WHERE id = ?",
            (checkout_id, phone, datetime.now(timezone.utc).isoformat(), order_id),
        )
    return {"payment_status": "pending"}


@app.post("/payments/mpesa/callback/{secret}")
async def mpesa_callback(secret: str, request: Request):
    # Safaricom calls this once the customer has paid or cancelled.
    if not secrets.compare_digest(secret.encode("utf-8"), mpesa.CALLBACK_SECRET.encode("utf-8")):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        body = await request.json()
        callback = body["Body"]["stkCallback"]
        checkout_id = callback["CheckoutRequestID"]
        result_code = int(callback["ResultCode"])
    except (ValueError, KeyError, TypeError):
        return {"ResultCode": 0, "ResultDesc": "Ignored"}

    receipt = None
    for item in (callback.get("CallbackMetadata") or {}).get("Item", []):
        if item.get("Name") == "MpesaReceiptNumber":
            receipt = str(item.get("Value"))

    with closing(get_db()) as conn, conn:
        if result_code == 0:
            conn.execute(
                "UPDATE orders SET payment_status = 'paid', mpesa_receipt = ? "
                "WHERE mpesa_checkout_id = ? AND payment_status != 'paid'",
                (receipt, checkout_id),
            )
        else:
            conn.execute(
                "UPDATE orders SET payment_status = 'failed' "
                "WHERE mpesa_checkout_id = ? AND payment_status = 'pending'",
                (checkout_id,),
            )
    return {"ResultCode": 0, "ResultDesc": "Accepted"}

@app.get("/admin/stats")
def admin_stats(admin=Depends(require_admin)):
    with closing(get_db()) as conn:
        orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        revenue = conn.execute(
            "SELECT COALESCE(SUM(total_cents), 0) FROM orders WHERE status != 'cancelled'"
        ).fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending'").fetchone()[0]
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    return {
        "orders": orders, "revenue_cents": revenue, "pending": pending,
        "users": users, "messages": messages,
    }

@app.get("/admin/orders")
def admin_orders(admin=Depends(require_admin)):
    expire_stale_payments()
    with closing(get_db()) as conn:
        orders = conn.execute(
            "SELECT id, total_cents, status, note, created_at, customer_name AS customer, "
            "phone, email, payment_method, payment_status, mpesa_receipt, "
            "CASE WHEN user_id IS NULL THEN 1 ELSE 0 END AS guest "
            "FROM orders ORDER BY id DESC"
        ).fetchall()
        result = []
        for o in orders:
            items = conn.execute(
                "SELECT name, quantity FROM order_items WHERE order_id = ?", (o["id"],)
            ).fetchall()
            result.append({**dict(o), "guest": bool(o["guest"]), "items": [dict(i) for i in items]})
    return result


@app.patch("/admin/orders/{order_id}/status")
def admin_set_status(
    order_id: int,
    data: StatusIn,
    background_tasks: BackgroundTasks,
    admin=Depends(require_admin),
):
    if data.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    with closing(get_db()) as conn, conn:
        order = conn.execute(
            "SELECT customer_name, email, total_cents, status FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (data.status, order_id))

    notified = False
    if data.status in STATUS_MESSAGES and data.status != order["status"] and order["email"]:
        subject, text = STATUS_MESSAGES[data.status]
        first = (order["customer_name"] or "there").split(" ")[0]
        background_tasks.add_task(
            send_email,
            order["email"],
            f"{subject} (order #{order_id})",
            f"Hi {first},\n\n{text}\n\nOrder #{order_id} - {money(order['total_cents'])}\n\nBrewHaven\n",
        )
        notified = True

    return {"id": order_id, "status": data.status, "notified": notified}


@app.get("/admin/products")
def admin_products(admin=Depends(require_admin)):
    with closing(get_db()) as conn:
        rows = conn.execute(
            "SELECT id, name, description, price_cents, image, tag, rating, active "
            "FROM products ORDER BY id"
        ).fetchall()
    return [{**dict(r), "active": bool(r["active"])} for r in rows]


@app.post("/admin/products", status_code=201)
def admin_add_product(data: ProductIn, admin=Depends(require_admin)):
    values = clean_product(data)
    with closing(get_db()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO products (name, description, price_cents, image, tag, rating, active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            values,
        )
    return {"id": cur.lastrowid}


@app.put("/admin/products/{product_id}")
def admin_update_product(product_id: int, data: ProductIn, admin=Depends(require_admin)):
    values = clean_product(data)
    with closing(get_db()) as conn, conn:
        cur = conn.execute(
            "UPDATE products SET name = ?, description = ?, price_cents = ?, image = ?, "
            "tag = ?, rating = ?, active = ? WHERE id = ?",
            (*values, product_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Product not found")
    return {"id": product_id}


@app.get("/admin/messages")
def admin_messages(admin=Depends(require_admin)):
    with closing(get_db()) as conn:
        rows = conn.execute(
            "SELECT id, name, email, message, created_at FROM messages ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/admin/users")
def admin_users(admin=Depends(require_admin)):
    with closing(get_db()) as conn:
        rows = conn.execute(
            "SELECT id, name, email, created_at FROM users ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/me")
def me(user=Depends(current_user)):
    return {
        "id": int(user["sub"]),
        "name": user["name"],
        "is_admin": is_admin_user(int(user["sub"])),
    }