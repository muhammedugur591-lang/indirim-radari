from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from urllib.error import URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


LOGGER = logging.getLogger(__name__)
API_URL = "https://dummyjson.com/products"
PAGE_SIZE = 100
REQUEST_TIMEOUT_SECONDS = 20
STORE_SEARCH_URLS = (
    ("Amazon Türkiye", "https://www.amazon.com.tr/s?k={query}"),
    ("Hepsiburada", "https://www.hepsiburada.com/ara?q={query}"),
    ("Trendyol", "https://www.trendyol.com/sr?q={query}"),
)


def fetch_products() -> list[dict]:
    products: list[dict] = []
    total = None
    skip = 0

    while total is None or skip < total:
        url = f"{API_URL}?limit={PAGE_SIZE}&skip={skip}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "IndirimRadari/1.0 (catalog seed)",
            },
        )
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.load(response)
        except (OSError, URLError, json.JSONDecodeError) as error:
            raise RuntimeError(f"DummyJSON katalog isteği başarısız: {url}") from error

        if not isinstance(payload, dict):
            raise RuntimeError(f"DummyJSON geçersiz JSON nesnesi döndürdü: {url}")
        page = payload.get("products")
        if not isinstance(page, list) or not page:
            raise RuntimeError(f"DummyJSON beklenmeyen ürün yanıtı döndürdü: {url}")
        try:
            total = int(payload["total"])
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("DummyJSON yanıtında geçerli total alanı bulunamadı") from error

        products.extend(page)
        skip += len(page)
        if skip > 5000:
            raise RuntimeError("DummyJSON katalog sayfalama sınırı aşıldı")

    if len(products) < 100:
        raise RuntimeError(f"DummyJSON en az 100 ürün döndürmeliydi; gelen: {len(products)}")
    return products


def _image_url(product: dict) -> str:
    images = product.get("images")
    candidates = [*(images if isinstance(images, list) else []), product.get("thumbnail", "")]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.startswith("https://"):
            return candidate
    raise RuntimeError(f"DummyJSON ürününde HTTPS görseli yok: {product.get('id')}")


def _description(product: dict) -> str:
    details = [
        product.get("description", "").strip(),
        f"Marka: {product.get('brand') or 'Belirtilmemiş'}",
        f"Kategori: {product.get('category', 'Belirtilmemiş')}",
        f"SKU: {product.get('sku', 'Belirtilmemiş')}",
        f"Ürün puanı: {product.get('rating', 'Belirtilmemiş')}",
        f"Stok durumu: {product.get('availabilityStatus', 'Belirtilmemiş')} "
        f"({product.get('stock', '—')} adet)",
        f"Kargo: {product.get('shippingInformation', 'Belirtilmemiş')}",
        f"Garanti: {product.get('warrantyInformation', 'Belirtilmemiş')}",
        f"İade: {product.get('returnPolicy', 'Belirtilmemiş')}",
    ]
    dimensions = product.get("dimensions")
    if isinstance(dimensions, dict):
        size = " × ".join(
            f"{dimensions[key]} cm"
            for key in ("width", "height", "depth")
            if dimensions.get(key) is not None
        )
        if size:
            details.append(f"Boyutlar: {size}")
    if product.get("weight") is not None:
        details.append(f"Ağırlık: {product['weight']} g")
    return " · ".join(detail for detail in details if detail)


def _category_label(category: str) -> str:
    return category.replace("-", " ").replace("_", " ").title()


def seed_catalog(
    db: sqlite3.Connection,
    category_icons: dict[str, str],
    products: list[dict] | None = None,
) -> int:
    store_names = tuple(store_name for store_name, _ in STORE_SEARCH_URLS)
    store_placeholders = ",".join("?" for _ in store_names)
    db.execute(
        f"DELETE FROM store_prices WHERE store_name NOT IN ({store_placeholders})",
        store_names,
    )
    if products is None:
        metadata = {
            row["key"]: row["value"]
            for row in db.execute("SELECT key, value FROM catalog_metadata").fetchall()
        }
        cached_count = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        synced_at = metadata.get("synced_at")
        if metadata.get("source") == "DummyJSON public sample API" and synced_at:
            try:
                last_sync = datetime.fromisoformat(synced_at)
                if datetime.now(timezone.utc) - last_sync < timedelta(hours=24):
                    return cached_count
            except ValueError:
                LOGGER.warning("Invalid catalog sync timestamp; refreshing DummyJSON data")
        try:
            catalog = fetch_products()
        except RuntimeError:
            if metadata.get("source") == "DummyJSON public sample API" and cached_count:
                LOGGER.exception("DummyJSON refresh failed; retaining the cached catalog")
                return cached_count
            raise
    else:
        catalog = products
    if len(catalog) < 100:
        raise RuntimeError(f"Seed için en az 100 ürün gerekli; gelen: {len(catalog)}")

    normalized: list[dict] = []
    for item in catalog:
        try:
            product_id = int(item["id"])
            title = str(item["title"]).strip()
            category = str(item["category"]).strip().lower()
            source_price = float(item["price"])
            discount = max(0.0, min(float(item.get("discountPercentage", 0)), 95.0))
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("DummyJSON ürün kaydı zorunlu alanları içermiyor") from error
        if not title or not category or source_price < 0:
            raise RuntimeError(f"DummyJSON ürün kaydı geçersiz: {item.get('id')}")
        normalized.append(
            {
                "id": product_id,
                "title": title,
                "category": category,
                "brand": str(item.get("brand") or "Belirtilmemiş").strip(),
                "source_price": source_price,
                "discount": discount,
                "image_url": _image_url(item),
                "description": _description(item),
                "rating": float(item.get("rating") or 0),
                "source_url": f"{API_URL}/{product_id}",
            }
        )

    product_ids = [item["id"] for item in normalized]
    if len(set(product_ids)) != len(product_ids):
        raise RuntimeError("DummyJSON ürün kimlikleri yineleniyor")

    product_placeholders = ",".join("?" for _ in product_ids)
    today = date.today()
    for item in normalized:
        category = item["category"]
        db.execute(
            """INSERT INTO categories (id, name, icon) VALUES (?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET name = excluded.name, icon = excluded.icon""",
            (
                category,
                _category_label(category),
                category_icons.get(category, "🛍️"),
            ),
        )

        source_price = item["source_price"]
        base_factor = 1 - item["discount"] / 100
        old_price = round(source_price / base_factor, 2) if base_factor else source_price
        offer_rows = []
        for store_index, (store_name, _) in enumerate(STORE_SEARCH_URLS):
            estimate_factor = 0.97 + ((item["id"] * 3 + store_index * 5) % 8) * 0.015
            estimated_price = round(source_price * estimate_factor, 2)
            search_url = STORE_SEARCH_URLS[store_index][1].format(
                query=quote_plus(item["title"])
            )
            offer_rows.append(
                (
                    item["id"],
                    store_name,
                    estimated_price,
                    search_url,
                    1,
                    "Tahmini demo teklifidir; mağaza API fiyatı veya birebir ürün sayfası değildir.",
                )
            )
        lowest_offer = min(offer_rows, key=lambda offer: offer[2])

        db.execute(
            """INSERT INTO products
               (id, title, category, brand, current_lowest_price, old_price,
                lowest_store_name, image_url, description, price_currency, source_url, rating)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'USD', ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 title = excluded.title,
                 category = excluded.category,
                 brand = excluded.brand,
                 current_lowest_price = excluded.current_lowest_price,
                 old_price = excluded.old_price,
                 lowest_store_name = excluded.lowest_store_name,
                 image_url = excluded.image_url,
                 description = excluded.description,
                 price_currency = excluded.price_currency,
                 source_url = excluded.source_url,
                 rating = excluded.rating""",
            (
                item["id"],
                item["title"],
                category,
                item["brand"],
                lowest_offer[2],
                old_price,
                lowest_offer[1],
                item["image_url"],
                item["description"],
                item["source_url"],
                item["rating"],
            ),
        )
        db.executemany(
            """INSERT INTO store_prices
               (product_id, store_name, price, product_url, is_estimate, source_note)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(product_id, store_name) DO UPDATE SET
                 price = excluded.price,
                 product_url = excluded.product_url,
                 is_estimate = excluded.is_estimate,
                 source_note = excluded.source_note""",
            offer_rows,
        )

        history_rows = []
        for days_ago in range(365, -1, -1):
            recorded_on = today - timedelta(days=days_ago)
            cycle = abs(math.sin((365 - days_ago + item["id"] * 13) / 31))
            markup = 0 if days_ago == 0 else 0.025 + days_ago * 0.00022 + cycle * 0.035
            history_rows.append(
                (
                    item["id"],
                    recorded_on.isoformat(),
                    round(lowest_offer[2] * (1 + markup), 2),
                )
            )
        db.executemany(
            """INSERT INTO price_history (product_id, recorded_on, price)
               VALUES (?, ?, ?)
               ON CONFLICT(product_id, recorded_on) DO UPDATE SET price = excluded.price""",
            history_rows,
        )

    db.execute(
        f"DELETE FROM products WHERE id NOT IN ({product_placeholders})", product_ids
    )
    category_ids = sorted({item["category"] for item in normalized})
    category_placeholders = ",".join("?" for _ in category_ids)
    db.execute(
        f"DELETE FROM categories WHERE id NOT IN ({category_placeholders})", category_ids
    )
    metadata = (
        ("source", "DummyJSON public sample API"),
        (
            "note",
            "DummyJSON fiyatları USD demo verisidir. Mağaza teklifleri ve 1 yıllık "
            "geçmiş simüle edilmiştir; canlı mağaza fiyatı değildir.",
        ),
        ("synced_at", datetime.now(timezone.utc).isoformat()),
        ("product_count", str(len(normalized))),
    )
    db.executemany(
        """INSERT INTO catalog_metadata (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        metadata,
    )
    LOGGER.info("Seeded %s DummyJSON products into SQLite", len(normalized))
    return len(normalized)
