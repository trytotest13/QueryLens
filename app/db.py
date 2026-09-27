"""Sample e-commerce SQLite DB: schema + deterministic seed data.

The app NEVER writes to this DB after seeding, and queries run through a
read-only connection (see safety.py + main.py).
"""
from __future__ import annotations

import random
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "shop.db"

DDL = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    city TEXT NOT NULL,
    signup_date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_date TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL
);
"""

CITIES = ["Delhi", "Mumbai", "Bengaluru", "Pune", "Hyderabad", "Chennai"]
CATEGORIES = {
    "Electronics": ["USB-C Hub", "Mechanical Keyboard", "27in Monitor", "Wireless Mouse", "Webcam 1080p"],
    "Books": ["Clean Code", "Designing Data-Intensive Applications", "The Pragmatic Programmer"],
    "Home": ["Desk Lamp", "Coffee Grinder", "Yoga Mat", "Water Bottle 1L"],
    "Fitness": ["Dumbbell Set", "Resistance Bands", "Foam Roller"],
}
STATUSES = ["delivered", "shipped", "processing", "cancelled"]


def ensure_db() -> Path:
    """Create + seed the DB once. Deterministic (fixed seed) so evals are stable."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists() and DB_PATH.stat().st_size > 10_000:
        return DB_PATH

    rng = random.Random(42)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(DDL)

    customers = [
        (i + 1, f"customer{i + 1}", f"user{i + 1}@example.com", rng.choice(CITIES),
         f"2024-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}")
        for i in range(40)
    ]
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)

    products, pid = [], 1
    for category, names in CATEGORIES.items():
        for name in names:
            products.append((pid, name, category, round(rng.uniform(8, 350), 2)))
            pid += 1
    conn.executemany("INSERT INTO products VALUES (?,?,?,?)", products)

    orders = []
    for oid in range(1, 121):
        month = rng.randint(1, 12)
        orders.append((oid, rng.randint(1, 40), f"2025-{month:02d}-{rng.randint(1, 28):02d}",
                       rng.choices(STATUSES, weights=[6, 2, 1, 1])[0]))
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)

    items = []
    for iid in range(1, 251):
        oid = rng.randint(1, 120)
        prod = rng.choice(products)
        items.append((iid, oid, prod[0], rng.randint(1, 3), prod[3]))
    conn.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", items)

    conn.commit()
    conn.close()
    return DB_PATH


def schema_ddl() -> str:
    """The DDL string handed to the LLM in the prompt."""
    return DDL.strip()


def connect_readonly() -> sqlite3.Connection:
    """Read-only connection: URI mode=ro + query_only pragma (belt and suspenders)."""
    if not DB_PATH.exists():
        ensure_db()
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn
