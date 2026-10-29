import json
import math
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATABASE_FILE = Path(
    os.environ.get("DATABASE_PATH", str(ROOT / "indirim-radari.sqlite3"))
)
_DATABASE_LOCK = threading.RLock()
_INITIALIZED = False


@contextmanager
def _connect():
    connection = sqlite3.connect(DATABASE_FILE, timeout=30)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            yield connection
    finally:
        connection.close()


def _format_date(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
        except ValueError:
            return None
    return parsed.strftime("%Y-%m-%d %H:%M")


def _insert_history(connection, collection, entry_id, history):
    for point in history:
        if not isinstance(point, dict):
            continue
        try:
            price = float(point.get("price"))
        except (TypeError, ValueError, OverflowError):
            continue
        observed_at = _format_date(point.get("date") or point.get("timestamp"))
        if observed_at is None:
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO price_history
                (collection, entry_id, observed_at, price)
            VALUES (?, ?, ?, ?)
            """,
            (collection, entry_id, observed_at, price),
        )


def _legacy_history(entry):
    history = entry.get("price_history")
    if isinstance(history, list) and history:
        return history
    try:
        price = float(entry.get("current_price") or entry.get("price"))
    except (TypeError, ValueError, OverflowError):
        return []
    if not math.isfinite(price) or price <= 0:
        return []
    date = (
        entry.get("last_checked")
        or entry.get("last_seen")
        or entry.get("created_at")
        or datetime.now().astimezone().isoformat(timespec="minutes")
    )
    return [{"date": date, "price": price}]


def initialize(legacy_files):
    global _INITIALIZED
    with _DATABASE_LOCK:
        if _INITIALIZED:
            return
        try:
            DATABASE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with _connect() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS catalog_items (
                        collection TEXT NOT NULL,
                        entry_id TEXT NOT NULL,
                        position INTEGER NOT NULL,
                        payload TEXT NOT NULL,
                        PRIMARY KEY (collection, entry_id)
                    );
                    CREATE TABLE IF NOT EXISTS price_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        collection TEXT NOT NULL,
                        entry_id TEXT NOT NULL,
                        observed_at TEXT NOT NULL,
                        price REAL NOT NULL,
                        UNIQUE (collection, entry_id, observed_at, price),
                        FOREIGN KEY (collection, entry_id)
                            REFERENCES catalog_items(collection, entry_id)
                            ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS price_history_lookup
                        ON price_history(collection, entry_id, observed_at);
                    CREATE TABLE IF NOT EXISTS catalog_categories (
                        position INTEGER NOT NULL,
                        name TEXT PRIMARY KEY,
                        subcategories TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS stores (
                        name TEXT PRIMARY KEY
                    );
                    CREATE TABLE IF NOT EXISTS categories (
                        category_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        parent_id TEXT REFERENCES categories(category_id),
                        position INTEGER NOT NULL,
                        UNIQUE(parent_id, name)
                    );
                    CREATE TABLE IF NOT EXISTS products (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        brand TEXT NOT NULL,
                        query_text TEXT NOT NULL,
                        category_id TEXT REFERENCES categories(category_id),
                        subcategory_id TEXT REFERENCES categories(category_id),
                        store_name TEXT REFERENCES stores(name),
                        price REAL NOT NULL,
                        active INTEGER NOT NULL,
                        is_discounted INTEGER NOT NULL,
                        last_seen TEXT NOT NULL,
                        url TEXT NOT NULL,
                        image_url TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS products_price_lookup
                        ON products(active, is_discounted, price);
                    CREATE INDEX IF NOT EXISTS products_store_lookup
                        ON products(store_name, active, price);
                    CREATE INDEX IF NOT EXISTS products_category_lookup
                        ON products(category_id, subcategory_id, active, price);
                    CREATE INDEX IF NOT EXISTS products_brand_lookup
                        ON products(brand, active, price);
                    CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
                        title, brand, store, category, subcategory, query_text,
                        technical,
                        tokenize='unicode61 remove_diacritics 2',
                        prefix='2 3 4'
                    );
                    """
                )
                migrated = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'legacy_json_migrated'"
                ).fetchone()
                if migrated is None:
                    for collection, path in legacy_files.items():
                        if not path.exists():
                            continue
                        try:
                            entries = json.loads(path.read_text(encoding="utf-8-sig"))
                        except (OSError, json.JSONDecodeError) as error:
                            raise RuntimeError(
                                f"{path.name} veritabanına aktarılamadı: {error}"
                            ) from error
                        if not isinstance(entries, list):
                            raise RuntimeError(
                                f"{path.name} bir JSON listesi olmalıdır."
                            )
                        for position, entry in enumerate(entries):
                            if not isinstance(entry, dict):
                                continue
                            entry_id = entry.get("id")
                            if not isinstance(entry_id, str):
                                continue
                            payload = {
                                key: value
                                for key, value in entry.items()
                                if key not in ("price_history", "previous_price")
                            }
                            connection.execute(
                                """
                                INSERT OR IGNORE INTO catalog_items
                                    (collection, entry_id, position, payload)
                                VALUES (?, ?, ?, ?)
                                """,
                                (
                                    collection,
                                    entry_id,
                                    position,
                                    json.dumps(payload, ensure_ascii=False),
                                ),
                            )
                            _insert_history(
                                connection,
                                collection,
                                entry_id,
                                _legacy_history(entry),
                            )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO metadata (key, value)
                        VALUES ('legacy_json_migrated', 'true')
                        """
                    )
            _INITIALIZED = True
        except sqlite3.Error as error:
            raise RuntimeError(f"SQLite veritabanı hazırlanamadı: {error}") from error


def _category_ids(connection, category_name, subcategory_name=None):
    if not isinstance(category_name, str) or not category_name:
        return None, None
    category_id = f"category:{category_name}"
    connection.execute(
        """
        INSERT OR IGNORE INTO categories (category_id, name, parent_id, position)
        VALUES (?, ?, NULL, 0)
        """,
        (category_id, category_name),
    )
    subcategory_id = None
    if isinstance(subcategory_name, str) and subcategory_name:
        subcategory_id = f"subcategory:{category_name}:{subcategory_name}"
        connection.execute(
            """
            INSERT OR IGNORE INTO categories (category_id, name, parent_id, position)
            VALUES (?, ?, ?, 0)
            """,
            (subcategory_id, subcategory_name, category_id),
        )
    return category_id, subcategory_id


def _technical_text(value):
    if isinstance(value, dict):
        return " ".join(
            f"{key} {_technical_text(item)}"
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return " ".join(_technical_text(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _upsert_product(connection, entry):
    entry_id = entry["id"]
    store = entry.get("store")
    if not isinstance(store, str) or not store:
        store = ""
    connection.execute("INSERT OR IGNORE INTO stores (name) VALUES (?)", (store,))
    category_id, subcategory_id = _category_ids(
        connection, entry.get("category"), entry.get("subcategory")
    )
    try:
        price = float(entry.get("price"))
    except (TypeError, ValueError, OverflowError):
        price = 0.0
    payload = {
        key: value
        for key, value in entry.items()
        if key not in ("price_history", "previous_price")
    }
    connection.execute(
        """
        INSERT INTO products (
            id, title, brand, query_text, category_id, subcategory_id, store_name,
            price, active, is_discounted, last_seen, url, image_url, payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            brand = excluded.brand,
            query_text = excluded.query_text,
            category_id = excluded.category_id,
            subcategory_id = excluded.subcategory_id,
            store_name = excluded.store_name,
            price = excluded.price,
            active = excluded.active,
            is_discounted = excluded.is_discounted,
            last_seen = excluded.last_seen,
            url = excluded.url,
            image_url = excluded.image_url,
            payload = excluded.payload
        """,
        (
            entry_id,
            str(entry.get("title", "")),
            str(entry.get("brand", "")),
            str(entry.get("query", "")),
            category_id,
            subcategory_id,
            store,
            price,
            int(bool(entry.get("active"))),
            int(bool(entry.get("is_discounted") or entry.get("shock_alert"))),
            str(entry.get("last_seen", "")),
            str(entry.get("url", "")),
            str(entry.get("image_url", "")),
            json.dumps(payload, ensure_ascii=False),
        ),
    )
    row_id = connection.execute(
        "SELECT rowid FROM products WHERE id = ?", (entry_id,)
    ).fetchone()["rowid"]
    connection.execute(
        """
        INSERT OR REPLACE INTO products_fts (
            rowid, title, brand, store, category, subcategory, query_text, technical
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            str(entry.get("title", "")),
            str(entry.get("brand", "")),
            store,
            str(entry.get("category", "")),
            str(entry.get("subcategory", "")),
            str(entry.get("query", "")),
            _technical_text(
                {
                    "feature": entry.get("feature", ""),
                    "selected_filters": entry.get("selected_filters", {}),
                    "monitor_filters": entry.get("monitor_filters", {}),
                }
            ),
        ),
    )


def seed_catalog(seed_file, categories, stores=()):
    stores = tuple(stores)
    schema_signature = json.dumps(
        {"categories": categories, "stores": stores},
        ensure_ascii=False,
        sort_keys=True,
    )
    with _DATABASE_LOCK:
        try:
            with _connect() as connection:
                seeded_schema = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'catalog_schema_seeded'"
                ).fetchone()
                if seeded_schema is not None and seeded_schema["value"] == schema_signature:
                    return
                for position, (name, subcategories) in enumerate(categories.items()):
                    category_id = f"category:{name}"
                    connection.execute(
                        """
                        INSERT INTO categories
                            (category_id, name, parent_id, position)
                        VALUES (?, ?, NULL, ?)
                        ON CONFLICT(category_id) DO UPDATE SET
                            name = excluded.name,
                            position = excluded.position
                        """,
                        (category_id, name, position),
                    )
                    for child_position, subcategory in enumerate(subcategories):
                        subcategory_id = f"subcategory:{name}:{subcategory}"
                        connection.execute(
                            """
                            INSERT INTO categories
                                (category_id, name, parent_id, position)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(category_id) DO UPDATE SET
                                name = excluded.name,
                                parent_id = excluded.parent_id,
                                position = excluded.position
                            """,
                            (
                                subcategory_id,
                                subcategory,
                                category_id,
                                child_position,
                            ),
                        )
                    connection.execute(
                        """
                        INSERT INTO catalog_categories
                            (position, name, subcategories)
                        VALUES (?, ?, ?)
                        ON CONFLICT(name) DO UPDATE SET
                            position = excluded.position,
                            subcategories = excluded.subcategories
                        """,
                        (
                            position,
                            name,
                            json.dumps(subcategories, ensure_ascii=False),
                        ),
                    )
                category_names = set(categories)
                existing_categories = connection.execute(
                    "SELECT name FROM catalog_categories"
                ).fetchall()
                connection.executemany(
                    "DELETE FROM catalog_categories WHERE name = ?",
                    [
                        (row["name"],)
                        for row in existing_categories
                        if row["name"] not in category_names
                    ],
                )
                connection.executemany(
                    "INSERT OR IGNORE INTO stores (name) VALUES (?)",
                    [(name,) for name in stores],
                )
                normalized = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'normalized_products_migrated'"
                ).fetchone()
                if normalized is None:
                    old_offers = connection.execute(
                        """
                        SELECT payload FROM catalog_items
                        WHERE collection = 'offers' ORDER BY position, rowid
                        """
                    ).fetchall()
                    for record in old_offers:
                        entry = json.loads(record["payload"])
                        if isinstance(entry.get("id"), str):
                            _upsert_product(connection, entry)
                    connection.execute(
                        """
                        INSERT INTO metadata (key, value)
                        VALUES ('normalized_products_migrated', 'true')
                        """
                    )
                seeded = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'local_catalog_seeded'"
                ).fetchone()
                has_products = connection.execute(
                    "SELECT 1 FROM catalog_items WHERE collection = 'offers' LIMIT 1"
                ).fetchone()
                if seeded is not None or has_products is not None:
                    if seeded is None:
                        connection.execute(
                            """
                            INSERT INTO metadata (key, value)
                            VALUES ('local_catalog_seeded', 'existing-data')
                            """
                        )
                    connection.execute(
                        """
                        INSERT INTO metadata (key, value)
                        VALUES ('catalog_schema_seeded', ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value
                        """,
                        (schema_signature,),
                    )
                    return
            if not seed_file.exists():
                with _connect() as connection:
                    connection.execute(
                        """
                        INSERT INTO metadata (key, value)
                        VALUES ('catalog_schema_seeded', ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value
                        """,
                        (schema_signature,),
                    )
                return
            try:
                catalog = json.loads(seed_file.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(
                    f"{seed_file.name} ürün kataloğu okunamadı: {error}"
                ) from error
            products = catalog.get("products") if isinstance(catalog, dict) else None
            if not isinstance(products, list):
                raise RuntimeError(
                    f"{seed_file.name} products listesi içermelidir."
                )
            save_records("offers", products)
            with _connect() as connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO metadata (key, value)
                    VALUES ('local_catalog_seeded', 'seed-file')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO metadata (key, value)
                    VALUES ('catalog_schema_seeded', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (schema_signature,),
                )
        except sqlite3.Error as error:
            raise RuntimeError(f"Ürün kataloğu hazırlanamadı: {error}") from error


def load_categories():
    with _DATABASE_LOCK:
        try:
            with _connect() as connection:
                rows = connection.execute(
                    """
                    SELECT name, subcategories
                    FROM catalog_categories
                    ORDER BY position
                    """
                ).fetchall()
                return {
                    row["name"]: json.loads(row["subcategories"])
                    for row in rows
                }
        except (sqlite3.Error, json.JSONDecodeError) as error:
            raise RuntimeError(f"Kategoriler veritabanından okunamadı: {error}") from error


def load_stores():
    with _DATABASE_LOCK:
        try:
            with _connect() as connection:
                rows = connection.execute(
                    "SELECT name FROM stores ORDER BY name"
                ).fetchall()
                return [row["name"] for row in rows]
        except sqlite3.Error as error:
            raise RuntimeError(f"Mağazalar veritabanından okunamadı: {error}") from error


def _fts_query(value, columns=()):
    tokens = re.findall(r"[\w]+", value, flags=re.UNICODE)
    if not tokens:
        return None
    if columns:
        return " OR ".join(
            "(" + " AND ".join(f'{column} : "{token}"*' for token in tokens) + ")"
            for column in columns
        )
    return " AND ".join(f'"{token}"*' for token in tokens)


def search_products(
    query="",
    min_price=None,
    max_price=None,
    stores=None,
    category="",
    subcategory="",
    brand="",
    technical="",
):
    conditions = ["(p.active = 1 OR p.is_discounted = 1)"]
    parameters = []
    match_query = _fts_query(f"{query} {technical}".strip())
    if match_query:
        conditions.append(
            "p.rowid IN (SELECT rowid FROM products_fts WHERE products_fts MATCH ?)"
        )
        parameters.append(match_query)
    brand_query = _fts_query(brand, ("title", "brand"))
    if brand_query:
        conditions.append(
            "p.rowid IN (SELECT rowid FROM products_fts WHERE products_fts MATCH ?)"
        )
        parameters.append(brand_query)
    if min_price is not None:
        conditions.append("p.price >= ?")
        parameters.append(min_price)
    if max_price is not None:
        conditions.append("p.price <= ?")
        parameters.append(max_price)
    if stores:
        conditions.append(
            f"p.store_name IN ({', '.join('?' for _ in stores)})"
        )
        parameters.extend(stores)
    if category:
        conditions.append(
            "p.category_id = (SELECT category_id FROM categories "
            "WHERE parent_id IS NULL AND name = ?)"
        )
        parameters.append(category)
    if subcategory:
        conditions.append(
            "p.subcategory_id = (SELECT child.category_id FROM categories child "
            "JOIN categories parent ON child.parent_id = parent.category_id "
            "WHERE parent.name = ? AND child.name = ?)"
        )
        parameters.extend([category, subcategory])
    if brand and not brand_query:
        conditions.append("(p.brand LIKE ? OR p.title LIKE ?)")
        parameters.extend([f"%{brand}%", f"%{brand}%"])
    sql = f"""
        SELECT p.payload,
            (
                SELECT price FROM price_history
                WHERE collection = 'offers' AND entry_id = p.id
                ORDER BY observed_at DESC, id DESC
                LIMIT 1 OFFSET 1
            ) AS previous_price
        FROM products p
        WHERE {' AND '.join(conditions)}
        ORDER BY p.is_discounted DESC, p.price ASC, p.last_seen DESC
    """
    with _DATABASE_LOCK:
        try:
            with _connect() as connection:
                rows = connection.execute(sql, parameters).fetchall()
                results = []
                for row in rows:
                    entry = json.loads(row["payload"])
                    if row["previous_price"] is not None:
                        entry["previous_price"] = row["previous_price"]
                    results.append(entry)
                return results
        except (sqlite3.Error, json.JSONDecodeError) as error:
            raise RuntimeError(f"SQLite ürün araması başarısız: {error}") from error


def load_vitrines(limit=8):
    with _DATABASE_LOCK:
        try:
            with _connect() as connection:
                shock_rows = connection.execute(
                    """
                    SELECT payload FROM products
                    WHERE active = 1 AND is_discounted = 1
                    ORDER BY price ASC, last_seen DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                popular_rows = connection.execute(
                    """
                    WITH ranked AS (
                        SELECT payload, title, price,
                            COUNT(*) OVER (PARTITION BY lower(title)) AS matches,
                            ROW_NUMBER() OVER (
                                PARTITION BY lower(title)
                                ORDER BY price ASC, last_seen DESC
                            ) AS title_rank
                        FROM products
                        WHERE active = 1
                    )
                    SELECT payload FROM ranked
                    WHERE title_rank = 1
                    ORDER BY matches DESC, price ASC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return {
                    "shock_deals": [
                        json.loads(row["payload"]) for row in shock_rows
                    ],
                    "popular_products": [
                        json.loads(row["payload"]) for row in popular_rows
                    ],
                }
        except (sqlite3.Error, json.JSONDecodeError) as error:
            raise RuntimeError(f"SQLite ürün vitrini okunamadı: {error}") from error


def load_records(collection):
    with _DATABASE_LOCK:
        try:
            with _connect() as connection:
                records = connection.execute(
                    """
                    SELECT entry_id, payload
                    FROM catalog_items
                    WHERE collection = ?
                    ORDER BY position, rowid
                    """,
                    (collection,),
                ).fetchall()
                recent_prices = connection.execute(
                    """
                    WITH ranked_prices AS (
                        SELECT entry_id, price,
                            ROW_NUMBER() OVER (
                                PARTITION BY entry_id
                                ORDER BY observed_at DESC, id DESC
                            ) AS rank
                        FROM price_history
                        WHERE collection = ?
                    )
                    SELECT entry_id, price, rank
                    FROM ranked_prices
                    WHERE rank <= 2
                    ORDER BY entry_id, rank
                    """,
                    (collection,),
                ).fetchall()
                previous_prices = {}
                for point in recent_prices:
                    if point["rank"] == 2:
                        previous_prices[point["entry_id"]] = point["price"]
                result = []
                for record in records:
                    entry = json.loads(record["payload"])
                    entry.pop("price_history", None)
                    entry.pop("previous_price", None)
                    if record["entry_id"] in previous_prices:
                        entry["previous_price"] = previous_prices[record["entry_id"]]
                    result.append(entry)
                return result
        except (sqlite3.Error, json.JSONDecodeError) as error:
            raise RuntimeError(f"{collection} veritabanından okunamadı: {error}") from error


def load_price_history(collection, entry_id, since=None):
    with _DATABASE_LOCK:
        try:
            with _connect() as connection:
                if since is None:
                    rows = connection.execute(
                        """
                        SELECT observed_at, price
                        FROM price_history
                        WHERE collection = ? AND entry_id = ?
                        ORDER BY observed_at, id
                        """,
                        (collection, entry_id),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT observed_at, price
                        FROM price_history
                        WHERE collection = ? AND entry_id = ? AND observed_at >= ?
                        ORDER BY observed_at, id
                        """,
                        (collection, entry_id, since),
                    ).fetchall()
                return [
                    {"date": row["observed_at"], "price": row["price"]}
                    for row in rows
                ]
        except sqlite3.Error as error:
            raise RuntimeError(
                f"{collection} fiyat geçmişi veritabanından okunamadı: {error}"
            ) from error


def save_records(collection, entries):
    with _DATABASE_LOCK:
        try:
            with _connect() as connection:
                valid_ids = []
                for position, entry in enumerate(entries):
                    if not isinstance(entry, dict) or not isinstance(
                        entry.get("id"), str
                    ):
                        continue
                    entry_id = entry["id"]
                    valid_ids.append(entry_id)
                    payload = {
                        key: value
                        for key, value in entry.items()
                        if key not in ("price_history", "previous_price")
                    }
                    connection.execute(
                        """
                        INSERT INTO catalog_items
                            (collection, entry_id, position, payload)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(collection, entry_id) DO UPDATE SET
                            position = excluded.position,
                            payload = excluded.payload
                        """,
                        (
                            collection,
                            entry_id,
                            position,
                            json.dumps(payload, ensure_ascii=False),
                        ),
                    )
                    if collection == "offers":
                        _upsert_product(connection, entry)
                    _insert_history(
                        connection,
                        collection,
                        entry_id,
                        entry.get("price_history", []),
                    )
                existing_ids = connection.execute(
                    "SELECT entry_id FROM catalog_items WHERE collection = ?",
                    (collection,),
                ).fetchall()
                removed_ids = [
                    record["entry_id"]
                    for record in existing_ids
                    if record["entry_id"] not in valid_ids
                ]
                connection.executemany(
                    "DELETE FROM catalog_items WHERE collection = ? AND entry_id = ?",
                    [(collection, entry_id) for entry_id in removed_ids],
                )
                if collection == "offers":
                    for entry_id in removed_ids:
                        row = connection.execute(
                            "SELECT rowid FROM products WHERE id = ?", (entry_id,)
                        ).fetchone()
                        if row is not None:
                            connection.execute(
                                "DELETE FROM products_fts WHERE rowid = ?",
                                (row["rowid"],),
                            )
                            connection.execute(
                                "DELETE FROM products WHERE id = ?", (entry_id,)
                            )
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise RuntimeError(f"{collection} veritabanına kaydedilemedi: {error}") from error
