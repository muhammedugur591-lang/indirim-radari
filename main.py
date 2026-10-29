from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Iterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from seed import seed_catalog


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "akakce_catalog.db"
INDEX_FILE = ROOT / "index.html"
LOGGER = logging.getLogger(__name__)

CATEGORY_ICONS = {
    "monitor": "🖥️",
    "oyuncu-monitoru": "🎮",
    "4k-monitor": "🖥️",
    "oyuncu-masaustu-bilgisayari": "🖥️",
}


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DATABASE, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _ensure_column(
    db: sqlite3.Connection, table: str, column: str, definition: str
) -> None:
    columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def initialize_schema(db: sqlite3.Connection) -> None:
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'store_prices'"
    ).fetchone():
        for index in db.execute("PRAGMA index_list(store_prices)").fetchall():
            if not index["unique"]:
                continue
            columns = [
                row["name"]
                for row in db.execute(f"PRAGMA index_info({index['name']})")
            ]
            if columns == ["product_id", "store_name"]:
                db.execute("DROP TABLE store_prices")
                break

    db.executescript(
        """
            CREATE TABLE IF NOT EXISTS categories (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                icon TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT NOT NULL REFERENCES categories(id),
                brand TEXT NOT NULL,
                current_lowest_price REAL NOT NULL,
                old_price REAL NOT NULL,
                lowest_store_name TEXT NOT NULL,
                image_url TEXT NOT NULL,
                description TEXT NOT NULL,
                price_currency TEXT NOT NULL DEFAULT 'TRY',
                source_url TEXT NOT NULL DEFAULT '',
                rating REAL NOT NULL DEFAULT 0,
                tags_json TEXT NOT NULL DEFAULT '[]',
                image_gallery_json TEXT NOT NULL DEFAULT '[]',
                refresh_rate_hz INTEGER,
                screen_size_inches REAL
            );
            CREATE TABLE IF NOT EXISTS store_prices (
                id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                store_name TEXT NOT NULL,
                price REAL NOT NULL,
                product_url TEXT NOT NULL,
                is_estimate INTEGER NOT NULL DEFAULT 0,
                source_note TEXT NOT NULL DEFAULT 'Trendyol product page',
                UNIQUE(product_id, store_name, product_url)
            );
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                recorded_on TEXT NOT NULL,
                price REAL NOT NULL,
                UNIQUE(product_id, recorded_on)
            );
            CREATE TABLE IF NOT EXISTS catalog_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
            CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand);
            CREATE INDEX IF NOT EXISTS idx_products_price ON products(current_lowest_price);
            CREATE INDEX IF NOT EXISTS idx_store_prices_store ON store_prices(store_name);
            CREATE INDEX IF NOT EXISTS idx_price_history_product_date
                ON price_history(product_id, recorded_on);
        """
    )
    _ensure_column(db, "products", "price_currency", "TEXT NOT NULL DEFAULT 'TRY'")
    _ensure_column(db, "products", "source_url", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(db, "products", "rating", "REAL NOT NULL DEFAULT 0")
    _ensure_column(db, "products", "tags_json", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(
        db, "products", "image_gallery_json", "TEXT NOT NULL DEFAULT '[]'"
    )
    _ensure_column(db, "products", "refresh_rate_hz", "INTEGER")
    _ensure_column(db, "products", "screen_size_inches", "REAL")
    _ensure_column(db, "store_prices", "is_estimate", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(
        db,
        "store_prices",
        "source_note",
        "TEXT NOT NULL DEFAULT 'Trendyol product page'",
    )


def initialize_database() -> None:
    with connect() as db:
        initialize_schema(db)
        seeded_count = seed_catalog(db, CATEGORY_ICONS)
        LOGGER.info("SQLite catalog initialized with %s Trendyol products", seeded_count)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="İndirim Radarı", version="1.1.0", lifespan=lifespan)


def product_card(row: sqlite3.Row, include_gallery: bool = False) -> dict:
    product = dict(row)
    product["tags"] = json.loads(product.pop("tags_json", "[]"))
    gallery = product.pop("image_gallery_json", "[]")
    if include_gallery:
        product["image_gallery"] = json.loads(gallery)
    return product


def _category_rows(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        """SELECT c.id, c.name, c.icon, COUNT(p.id) AS product_count
           FROM categories c LEFT JOIN products p ON p.category = c.id
           GROUP BY c.id ORDER BY c.name COLLATE NOCASE"""
    ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/categories")
def get_categories() -> list[dict]:
    with connect() as db:
        return _category_rows(db)


@app.get("/api/brands")
def get_brands(category: str = "") -> list[str]:
    with connect() as db:
        if category:
            rows = db.execute(
                """SELECT DISTINCT brand FROM products WHERE category = ?
                   ORDER BY brand COLLATE NOCASE""",
                (category,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT DISTINCT brand FROM products ORDER BY brand COLLATE NOCASE"
            ).fetchall()
    return [row["brand"] for row in rows]


@app.get("/api/filters")
def get_filter_facets(category: str = "monitor") -> dict:
    with connect() as db:
        refresh_rates = [
            row["refresh_rate_hz"]
            for row in db.execute(
                """SELECT DISTINCT refresh_rate_hz FROM products
                   WHERE category = ? AND refresh_rate_hz IS NOT NULL
                     AND refresh_rate_hz < 360
                   ORDER BY refresh_rate_hz""",
                (category,),
            ).fetchall()
        ]
        has_360_plus = db.execute(
            """SELECT 1 FROM products
               WHERE category = ? AND refresh_rate_hz >= 360 LIMIT 1""",
            (category,),
        ).fetchone()
        if has_360_plus:
            refresh_rates.append(360)
        screen_sizes: list[float | str] = [
            row["screen_size_inches"]
            for row in db.execute(
                """SELECT DISTINCT screen_size_inches FROM products
                   WHERE category = ? AND screen_size_inches IS NOT NULL
                     AND screen_size_inches < 34
                   ORDER BY screen_size_inches""",
                (category,),
            ).fetchall()
        ]
        has_34_plus = db.execute(
            """SELECT 1 FROM products
               WHERE category = ? AND screen_size_inches >= 34 LIMIT 1""",
            (category,),
        ).fetchone()
        if has_34_plus:
            screen_sizes.append("34+")
    return {"refresh_rates": refresh_rates, "screen_sizes": screen_sizes}


@app.get("/api/home")
def get_home() -> dict:
    select_card = """SELECT p.*, c.name AS category_name,
                            (SELECT COUNT(*) FROM store_prices s WHERE s.product_id = p.id) AS offer_count,
                            (SELECT s.product_url FROM store_prices s
                             WHERE s.product_id = p.id ORDER BY s.price ASC LIMIT 1) AS lowest_store_url
                     FROM products p JOIN categories c ON c.id = p.category"""
    with connect() as db:
        popular = db.execute(
            f"{select_card} ORDER BY p.rating DESC, p.id ASC LIMIT 24"
        ).fetchall()
        deals = db.execute(
            f""" {select_card}
                 ORDER BY (p.old_price - p.current_lowest_price) / p.old_price DESC,
                          p.rating DESC LIMIT 24"""
        ).fetchall()
        metadata = {
            row["key"]: row["value"]
            for row in db.execute("SELECT key, value FROM catalog_metadata").fetchall()
        }
        return {
            "popular": [product_card(row) for row in popular],
            "deals": [product_card(row) for row in deals],
            "categories": _category_rows(db),
            "catalog_source": metadata.get("source", "Trendyol"),
            "catalog_note": metadata.get("note", ""),
        }


@app.get("/api/products")
def get_products(
    q: str = "",
    category: str = "",
    brand: str = "",
    store: str = "",
    min_price: Annotated[float | None, Query(ge=0)] = None,
    max_price: Annotated[float | None, Query(ge=0)] = None,
    refresh_rate: Annotated[int | None, Query(ge=1, le=1000)] = None,
    min_refresh_rate: Annotated[int | None, Query(ge=1, le=1000)] = None,
    screen_size: Annotated[float | None, Query(ge=10, le=100)] = None,
    min_screen_size: Annotated[float | None, Query(ge=10, le=100)] = None,
    sort: str = "relevance",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict:
    clauses: list[str] = []
    values: list[object] = []
    if q.strip():
        clauses.append("(p.title LIKE ? OR p.brand LIKE ? OR p.description LIKE ?)")
        search = f"%{q.strip()[:100]}%"
        values.extend((search, search, search))
    if category:
        clauses.append("p.category = ?")
        values.append(category)
    if brand:
        clauses.append("p.brand = ?")
        values.append(brand)
    if min_price is not None:
        clauses.append("p.current_lowest_price >= ?")
        values.append(min_price)
    if max_price is not None:
        clauses.append("p.current_lowest_price <= ?")
        values.append(max_price)
    if refresh_rate is not None:
        clauses.append("p.refresh_rate_hz = ?")
        values.append(refresh_rate)
    if min_refresh_rate is not None:
        clauses.append("p.refresh_rate_hz >= ?")
        values.append(min_refresh_rate)
    if screen_size is not None:
        clauses.append("p.screen_size_inches = ?")
        values.append(screen_size)
    if min_screen_size is not None:
        clauses.append("p.screen_size_inches >= ?")
        values.append(min_screen_size)
    if store:
        clauses.append(
            "EXISTS (SELECT 1 FROM store_prices s WHERE s.product_id = p.id AND s.store_name = ?)"
        )
        values.append(store)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order_by = {
        "price_asc": "p.current_lowest_price ASC, p.id ASC",
        "price_desc": "p.current_lowest_price DESC, p.id ASC",
        "discount": "(p.old_price - p.current_lowest_price) / p.old_price DESC, p.id ASC",
        "relevance": "p.rating DESC, p.id ASC",
    }.get(sort, "p.rating DESC, p.id ASC")
    with connect() as db:
        rows = db.execute(
            f"""SELECT p.*, c.name AS category_name,
                       (SELECT COUNT(*) FROM store_prices s WHERE s.product_id = p.id) AS offer_count,
                       (SELECT s.product_url FROM store_prices s
                        WHERE s.product_id = p.id ORDER BY s.price ASC LIMIT 1) AS lowest_store_url
                FROM products p JOIN categories c ON c.id = p.category
                {where} ORDER BY {order_by} LIMIT ?""",
            (*values, limit),
        ).fetchall()
        total = db.execute(f"SELECT COUNT(*) FROM products p {where}", values).fetchone()[0]
        items = [product_card(row) for row in rows]
    return {"items": items, "total": total}


@app.get("/api/products/{product_id}")
def get_product(product_id: int) -> dict:
    with connect() as db:
        row = db.execute(
            """SELECT p.*, c.name AS category_name,
                      (SELECT COUNT(*) FROM store_prices s WHERE s.product_id = p.id) AS offer_count,
                      (SELECT s.product_url FROM store_prices s
                       WHERE s.product_id = p.id ORDER BY s.price ASC LIMIT 1) AS lowest_store_url
               FROM products p JOIN categories c ON c.id = p.category WHERE p.id = ?""",
            (product_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Ürün bulunamadı")
        product = product_card(row, include_gallery=True)
        offers = db.execute(
            """SELECT store_name, price, product_url, is_estimate, source_note
               FROM store_prices WHERE product_id = ? ORDER BY price ASC, store_name ASC""",
            (product_id,),
        ).fetchall()
        product["offers"] = [dict(offer) for offer in offers]
    return product


@app.get("/api/products/{product_id}/history")
def get_price_history(product_id: int, period: str = "1y") -> dict:
    period_days = {"1w": 7, "1m": 30, "6m": 183, "1y": 365}
    if period not in period_days:
        raise HTTPException(status_code=422, detail="Geçersiz fiyat geçmişi dönemi")
    start = date.today() - timedelta(days=period_days[period])
    with connect() as db:
        product = db.execute(
            "SELECT price_currency FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if product is None:
            raise HTTPException(status_code=404, detail="Ürün bulunamadı")
        rows = db.execute(
            """SELECT recorded_on, price FROM price_history
               WHERE product_id = ? AND recorded_on >= ?
               ORDER BY recorded_on ASC""",
            (product_id, start.isoformat()),
        ).fetchall()
    prices = [row["price"] for row in rows]
    if not prices:
        raise HTTPException(status_code=503, detail="Bu ürün için fiyat geçmişi bulunamadı")
    return {
        "points": [dict(row) for row in rows],
        "highest": max(prices),
        "lowest": min(prices),
        "current": prices[-1],
        "currency": product["price_currency"],
        "is_estimate": False,
    }


@app.get("/")
def home_page() -> FileResponse:
    return FileResponse(INDEX_FILE)


@app.get("/{full_path:path}")
def spa_fallback(full_path: str) -> FileResponse:
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API rotası bulunamadı")
    if full_path in {"style.css", "app.js"}:
        return FileResponse(ROOT / full_path)
    return FileResponse(INDEX_FILE)
