"""Build the demo warehouse (`var/warehouse.db`, spec §9) deterministically with `random.Random(42)`.

Usage: uv run python data/seed_warehouse.py [PATH]
PATH defaults to `warehouse_db` from the backend config (`VDAGENT_WAREHOUSE_DB` / `VDAGENT_CONFIG`
honoured). The database is rebuilt from scratch on every run.

Planted patterns (volume = number of sales lines; prices are constant, so revenue follows volume):
1. Q4 seasonality: +35% volume in November and December.
2. One promo week per year: Home & Kitchen, ISO week 29, discount 0.3, 3x volume.
3. One store (Detroit Riverside) declines steadily: -40% volume from 2024-01-01 to 2025-12-31.
4. Sports & Outdoors grows +60% YoY; every other category grows ~+8% YoY.
"""

from __future__ import annotations

import math
import os
import random
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

SEED = 42
START = date(2024, 1, 1)
END = date(2025, 12, 31)
TARGET_ROWS = 150_000

SCHEMA = """
CREATE TABLE dim_date (
  date_key INTEGER PRIMARY KEY,
  date TEXT NOT NULL, year INTEGER, quarter INTEGER, month INTEGER,
  month_name TEXT, iso_week INTEGER, day_of_week INTEGER, is_weekend INTEGER);
CREATE TABLE dim_product (
  product_key INTEGER PRIMARY KEY, sku TEXT UNIQUE, name TEXT,
  category TEXT, subcategory TEXT, brand TEXT, unit_cost REAL, list_price REAL);
CREATE TABLE dim_store (
  store_key INTEGER PRIMARY KEY, name TEXT, city TEXT, region TEXT,
  format TEXT CHECK (format IN ('mall','street','online')), opened_date TEXT);
CREATE TABLE fact_sales (
  sale_id INTEGER PRIMARY KEY,
  date_key INTEGER REFERENCES dim_date(date_key),
  product_key INTEGER REFERENCES dim_product(product_key),
  store_key INTEGER REFERENCES dim_store(store_key),
  quantity INTEGER, unit_price REAL, discount REAL, revenue REAL, cost REAL);
"""

INDEXES = """
CREATE INDEX ix_sales_date    ON fact_sales(date_key);
CREATE INDEX ix_sales_product ON fact_sales(product_key);
CREATE INDEX ix_sales_store   ON fact_sales(store_key);
"""

# category -> (sku prefix, share of volume, (min, max) list price, {subcategory: brands})
CATEGORIES: dict[str, tuple[str, float, tuple[float, float], dict[str, list[str]]]] = {
    "Electronics": (
        "ELE", 0.22, (25.0, 900.0),
        {"Audio": ["Sonora", "Beatline"], "Computers": ["Nexa", "Corelink"], "Phones": ["Pixelon", "Nexa"]},
    ),
    "Home & Kitchen": (
        "HOM", 0.24, (12.0, 250.0),
        {"Cookware": ["Ironleaf", "Chefwell"], "Appliances": ["Brewmaster", "Chefwell"], "Decor": ["Nordhaus", "Lumen"]},
    ),
    "Apparel": (
        "APP", 0.22, (15.0, 180.0),
        {"Menswear": ["Harbor & Co", "Stride"], "Womenswear": ["Maison Vale", "Stride"], "Footwear": ["Stride", "Terrano"]},
    ),
    "Sports & Outdoors": (
        "SPO", 0.16, (18.0, 450.0),
        {"Fitness": ["Pulse", "Ironform"], "Camping": ["Terrano", "Northpeak"], "Cycling": ["Velox", "Northpeak"]},
    ),
    "Beauty": (
        "BEA", 0.16, (6.0, 95.0),
        {"Skincare": ["Aurelle", "Pure Botanica"], "Haircare": ["Silkroot", "Aurelle"], "Fragrance": ["Maison Vale", "Noir"]},
    ),
}

PRODUCT_NOUNS: dict[str, list[str]] = {
    "Audio": ["Wireless Earbuds", "Bluetooth Speaker", "Studio Headphones", "Soundbar"],
    "Computers": ["13\" Laptop", "Mechanical Keyboard", "27\" Monitor", "Wireless Mouse"],
    "Phones": ["Smartphone 128GB", "Phone Case", "Fast Charger", "Smartwatch"],
    "Cookware": ["Cast Iron Skillet", "Nonstick Pan Set", "Chef Knife", "Dutch Oven"],
    "Appliances": ["Espresso Machine", "Air Fryer", "Blender", "Electric Kettle"],
    "Decor": ["Table Lamp", "Throw Blanket", "Wall Clock", "Scented Candle"],
    "Menswear": ["Oxford Shirt", "Chino Pants", "Wool Sweater", "Rain Jacket"],
    "Womenswear": ["Linen Dress", "Denim Jacket", "Knit Cardigan", "Silk Blouse"],
    "Footwear": ["Running Shoes", "Leather Boots", "Canvas Sneakers", "Sandals"],
    "Fitness": ["Yoga Mat", "Adjustable Dumbbells", "Resistance Bands", "Kettlebell"],
    "Camping": ["2-Person Tent", "Sleeping Bag", "Camp Stove", "Headlamp"],
    "Cycling": ["Road Helmet", "Bike Lights", "Cycling Jersey", "Water Bottle Cage"],
    "Skincare": ["Daily Moisturizer", "Vitamin C Serum", "Sunscreen SPF50", "Cleansing Gel"],
    "Haircare": ["Repair Shampoo", "Hydrating Conditioner", "Hair Oil", "Styling Cream"],
    "Fragrance": ["Eau de Parfum", "Body Mist", "Cologne", "Travel Spray"],
}

# (name, city, region, format, opened_date, volume weight)
STORES: list[tuple[str, str, str, str, str, float]] = [
    ("Minneapolis Mall of Lakes", "Minneapolis", "North", "mall", "2015-04-18", 1.15),
    ("Chicago Loop", "Chicago", "North", "street", "2012-09-01", 1.05),
    ("Detroit Riverside", "Detroit", "North", "mall", "2016-03-12", 1.10),
    ("New York SoHo", "New York", "East", "street", "2011-05-20", 1.30),
    ("Boston Back Bay", "Boston", "East", "street", "2014-10-04", 0.95),
    ("Philadelphia Center City", "Philadelphia", "East", "mall", "2018-06-15", 0.90),
    ("Atlanta Peachtree", "Atlanta", "South", "mall", "2017-02-25", 1.00),
    ("Miami Brickell", "Miami", "South", "street", "2019-11-09", 0.85),
    ("Houston Galleria", "Houston", "South", "mall", "2013-08-30", 1.10),
    ("Los Angeles Grove", "Los Angeles", "West", "mall", "2012-03-17", 1.25),
    ("Seattle Pike Place", "Seattle", "West", "street", "2020-07-01", 0.85),
    ("Online Store", "San Francisco", "West", "online", "2018-01-15", 1.60),
]

GROWTH_OTHER = 1.08  # YoY multiplier for every category but the fast one
GROWTH_FAST = 1.60
FAST_CATEGORY = "Sports & Outdoors"
Q4_FACTOR = 1.35  # November + December
PROMO_CATEGORY = "Home & Kitchen"
PROMO_WEEKS = {(2024, 29), (2025, 29)}  # (ISO year, ISO week)
PROMO_FACTOR = 3.0
PROMO_DISCOUNT = 0.3
DECLINING_STORE = "Detroit Riverside"
DECLINE_TOTAL = 0.40  # linear decline from 1.0 to 0.6 across the two years
WEEKEND_FACTOR = 1.25  # physical stores only

QUANTITIES = [1, 2, 3, 4, 5, 6]
QUANTITY_WEIGHTS = [48, 24, 13, 8, 5, 2]
DISCOUNTS = [0.0, 0.05, 0.10, 0.15]
DISCOUNT_WEIGHTS = [78, 11, 8, 3]


def build_dates() -> list[tuple]:
    rows = []
    d = START
    while d <= END:
        iso = d.isocalendar()
        rows.append((
            int(d.strftime("%Y%m%d")), d.isoformat(), d.year, (d.month - 1) // 3 + 1, d.month,
            d.strftime("%B"), iso.week, iso.weekday, int(iso.weekday >= 6),
        ))
        d += timedelta(days=1)
    return rows


def build_products(rng: random.Random) -> list[tuple]:
    rows = []
    key = 0
    for category, (prefix, _share, (lo, hi), subcats) in CATEGORIES.items():
        for subcategory, brands in subcats.items():
            for i, noun in enumerate(PRODUCT_NOUNS[subcategory]):
                key += 1
                brand = brands[i % len(brands)]
                list_price = round(max(round(math.exp(rng.uniform(math.log(lo), math.log(hi)))), 2) - 0.01, 2)
                unit_cost = round(list_price * rng.uniform(0.42, 0.68), 2)
                sku = f"{prefix}-{key:04d}"
                rows.append((key, sku, f"{brand} {noun}", category, subcategory, brand, unit_cost, list_price))
    return rows


def poisson(rng: random.Random, lam: float) -> int:
    """Knuth's algorithm; lam stays small (< 30) here."""
    limit = math.exp(-lam)
    k, p = 0, rng.random()
    while p > limit:
        k += 1
        p *= rng.random()
    return k


def build_sales(rng: random.Random, dates: list[tuple], products: list[tuple]) -> list[tuple]:
    by_category: dict[str, list[tuple]] = {c: [] for c in CATEGORIES}
    for p in products:
        by_category[p[3]].append(p)
    # Popularity weights inside each category (fixed per product).
    popularity = {c: [rng.uniform(0.4, 1.6) for _ in ps] for c, ps in by_category.items()}
    days_total = (END - START).days
    total_weight = sum(s[5] for s in STORES)
    declining_weight = next(s[5] for s in STORES if s[0] == DECLINING_STORE)

    # Expected sales lines per (date, store, category); scaled afterwards to hit TARGET_ROWS.
    intensities: list[tuple[int, int, str, float, bool]] = []
    for dk, iso_date, _year, _q, month, _mn, iso_week, dow, _we in dates:
        day = date.fromisoformat(iso_date)
        t = (day - START).days / 365.0  # years since start
        season = Q4_FACTOR if month in (11, 12) else 1.0
        promo_week = (day.isocalendar().year, iso_week) in PROMO_WEEKS
        # The declining store nets out chain-wide growth so its own volume falls by DECLINE_TOTAL;
        # the other stores absorb the difference so every category keeps its planted YoY growth.
        mix_growth = sum(s * (GROWTH_FAST if c == FAST_CATEGORY else GROWTH_OTHER) ** t
                         for c, (_p, s, _r, _sc) in CATEGORIES.items())
        declining = (1.0 - DECLINE_TOTAL * (day - START).days / days_total) / mix_growth
        others = (total_weight - declining_weight * declining) / (total_weight - declining_weight)
        for store_key, (name, _city, _region, fmt, _opened, weight) in enumerate(STORES, start=1):
            store_factor = weight * (declining if name == DECLINING_STORE else others)
            if fmt != "online" and dow >= 6:
                store_factor *= WEEKEND_FACTOR
            for category, (_prefix, share, _range, _subcats) in CATEGORIES.items():
                growth = (GROWTH_FAST if category == FAST_CATEGORY else GROWTH_OTHER) ** t
                promo = promo_week and category == PROMO_CATEGORY
                lam = store_factor * share * season * growth * (PROMO_FACTOR if promo else 1.0)
                intensities.append((dk, store_key, category, lam, promo))

    scale = TARGET_ROWS / sum(i[3] for i in intensities)
    sales: list[tuple] = []
    for dk, store_key, category, lam, promo in intensities:
        n = poisson(rng, lam * scale)
        if not n:
            continue
        cat_products = by_category[category]
        picks = rng.choices(cat_products, weights=popularity[category], k=n)
        for product in picks:
            product_key, _sku, _name, _cat, _sub, _brand, unit_cost, list_price = product
            quantity = rng.choices(QUANTITIES, weights=QUANTITY_WEIGHTS)[0]
            discount = PROMO_DISCOUNT if promo else rng.choices(DISCOUNTS, weights=DISCOUNT_WEIGHTS)[0]
            revenue = round(quantity * list_price * (1 - discount), 2)
            cost = round(quantity * unit_cost, 2)
            sales.append((len(sales) + 1, dk, product_key, store_key, quantity, list_price, discount, revenue, cost))
    return sales


def resolve_path(argv: list[str]) -> Path:
    if len(argv) > 1:
        return Path(argv[1])
    from vdagent_backend.config import load_config

    return Path(load_config().warehouse_db)


def main(argv: list[str]) -> int:
    started = time.perf_counter()
    path = resolve_path(argv)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)

    rng = random.Random(SEED)
    dates = build_dates()
    products = build_products(rng)
    stores = [(k, n, c, r, f, o) for k, (n, c, r, f, o, _w) in enumerate(STORES, start=1)]
    sales = build_sales(rng, dates, products)

    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(SCHEMA)
        with conn:  # single transaction
            conn.executemany("INSERT INTO dim_date VALUES (?,?,?,?,?,?,?,?,?)", dates)
            conn.executemany("INSERT INTO dim_product VALUES (?,?,?,?,?,?,?,?)", products)
            conn.executemany("INSERT INTO dim_store VALUES (?,?,?,?,?,?)", stores)
            conn.executemany("INSERT INTO fact_sales VALUES (?,?,?,?,?,?,?,?,?)", sales)
        conn.executescript(INDEXES)
        conn.execute("ANALYZE")
    finally:
        conn.close()
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    os.replace(tmp, path)

    revenue = sum(s[7] for s in sales)
    print(f"warehouse: {path}")
    print(f"  dim_date={len(dates)} dim_product={len(products)} dim_store={len(stores)} fact_sales={len(sales)}")
    print(f"  revenue={revenue:,.2f}  built in {time.perf_counter() - started:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
