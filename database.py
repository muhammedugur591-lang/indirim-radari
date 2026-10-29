import json
import math
import os
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


def seed_catalog(seed_file, categories):
    with _DATABASE_LOCK:
        try:
            with _connect() as connection:
                for position, (name, subcategories) in enumerate(categories.items()):
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
                names = {name for name in categories}
                existing = connection.execute(
                    "SELECT name FROM catalog_categories"
                ).fetchall()
                connection.executemany(
                    "DELETE FROM catalog_categories WHERE name = ?",
                    [(row["name"],) for row in existing if row["name"] not in names],
                )
                seeded = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'local_catalog_seeded'"
                ).fetchone()
                has_products = connection.execute(
                    "SELECT 1 FROM catalog_items WHERE collection = 'offers' LIMIT 1"
                ).fetchone()
                if seeded is not None:
                    return
                if has_products is not None:
                    connection.execute(
                        """
                        INSERT INTO metadata (key, value)
                        VALUES ('local_catalog_seeded', 'existing-data')
                        """
                    )
                    return
            if not seed_file.exists():
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
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise RuntimeError(f"{collection} veritabanına kaydedilemedi: {error}") from error
