from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
from urllib.parse import urlparse


LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
CATALOG_FILE = ROOT / "urunler.json"
AMAZON_CATALOG_FILE = ROOT / "urunler_amazon.json"
IMAGE_SUFFIXES = {".avif", ".jpg", ".jpeg", ".png", ".webp"}
REFRESH_RATE_PATTERN = re.compile(r"(?<!\d)(\d{2,3})\s*(?:hz|hertz)\b", re.IGNORECASE)
SCREEN_SIZE_PATTERN = re.compile(
    r"(?<!\d)(\d{2}(?:[.,]\d)?)\s*(?:[\"″”]|inç|inch(?:es)?)",
    re.IGNORECASE,
)


def _product_id(product_url: str) -> int:
    matches = re.findall(r"-p-(\d+)(?=[?/#]|$)", product_url)
    if not matches:
        raise ValueError(f"Trendyol ürün numarası bulunamadı: {product_url}")
    return int(matches[-1])


def _valid_product_image(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("https://"):
        return False
    parsed = urlparse(value)
    return (
        PurePosixPath(parsed.path).suffix.lower() in IMAGE_SUFFIXES
        and "/mnresize/" not in parsed.path
        and "search_" not in parsed.path
    )


def _category_id(name: str) -> str:
    normalized = name.casefold().replace("ı", "i").replace("ş", "s")
    normalized = normalized.replace("ğ", "g").replace("ü", "u")
    normalized = normalized.replace("ö", "o").replace("ç", "c")
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")


def _monitor_specs(title: str) -> tuple[int | None, float | None]:
    refresh_rates = [
        int(match.group(1))
        for match in REFRESH_RATE_PATTERN.finditer(title)
        if 20 <= int(match.group(1)) <= 1000
    ]
    screen_sizes = [
        float(match.group(1).replace(",", "."))
        for match in SCREEN_SIZE_PATTERN.finditer(title)
    ]
    screen_sizes = [size for size in screen_sizes if 10 <= size <= 100]
    return (
        max(refresh_rates) if refresh_rates else None,
        screen_sizes[0] if screen_sizes else None,
    )


def _normalize_product(item: dict) -> dict:
    title = str(item.get("title", "")).strip()
    category_name = str(item.get("category", "")).strip()
    normalized_category = category_name.casefold()
    if "monitör" in normalized_category or "monitor" in normalized_category:
        category_name = "Monitör"
    brand = str(item.get("brand", "")).strip()
    product_url = str(item.get("source_url") or "").strip()
    offers = item.get("stores")
    if not product_url and isinstance(offers, list) and offers:
        product_url = str(offers[0].get("product_url") or "").strip()
    if not product_url:
        raise ValueError(f"Ürün yönlendirme URL'si eksik: {title}")
    parsed_url = urlparse(product_url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "www.trendyol.com":
        raise ValueError(f"Trendyol ürün URL'si geçersiz: {product_url}")

    product_id = int(item.get("id") or _product_id(product_url))
    if product_id <= 0 or not title or not category_name or not brand:
        raise ValueError(f"Ürün temel alanları eksik: {product_url}")

    image_url = item.get("main_image") or item.get("image_url")
    if not _valid_product_image(image_url):
        raise ValueError(f"Ürünün HD görseli geçersiz: {product_url}")

    if not isinstance(offers, list) or not offers:
        raise ValueError(f"Ürün için Trendyol teklifi yok: {product_url}")
    normalized_offers = []
    seen_urls = set()
    for offer in offers:
        offer_url = str(offer.get("product_url") or product_url).strip()
        if offer_url in seen_urls:
            continue
        seen_urls.add(offer_url)
        amount = float(offer.get("price") or 0)
        currency = str(offer.get("currency") or "TRY").upper()
        if not math.isfinite(amount) or amount <= 0 or currency != "TRY":
            raise ValueError(f"Gerçek TL fiyatı geçersiz: {offer_url}")
        if (
            urlparse(offer_url).scheme != "https"
            or urlparse(offer_url).hostname != "www.trendyol.com"
        ):
            raise ValueError(f"Satıcı URL'si Trendyol ürünü değil: {offer_url}")
        old_price = float(offer.get("old_price") or amount)
        normalized_offers.append(
            {
                "store_name": "Trendyol",
                "price": amount,
                "old_price": old_price if old_price > amount else amount,
                "product_url": offer_url,
            }
        )

    if not normalized_offers:
        raise ValueError(f"Ürün için kullanılabilir teklif yok: {product_url}")

    tags = item.get("tags", [])
    if not isinstance(tags, list):
        raise ValueError(f"Ürün teknik etiketleri geçersiz: {product_url}")
    images = item.get("image_gallery", [])
    if not isinstance(images, list):
        images = []
    gallery = list(dict.fromkeys(
        image for image in images if _valid_product_image(image)
    ))
    if image_url not in gallery:
        gallery.insert(0, image_url)
    refresh_rate_hz, screen_size_inches = _monitor_specs(title)

    return {
        "id": product_id,
        "title": title,
        "category_id": _category_id(category_name),
        "category_name": category_name,
        "brand": brand,
        "image_url": image_url,
        "image_gallery": gallery,
        "description": str(item.get("description") or "").strip(),
        "tags": [str(tag).strip() for tag in tags if str(tag).strip()],
        "refresh_rate_hz": refresh_rate_hz,
        "screen_size_inches": screen_size_inches,
        "offers": normalized_offers,
    }


def _normalize_amazon_product(item: dict, product_id: int) -> dict:
    title = str(item.get("title", "")).strip()
    category_name = str(item.get("category", "")).strip()
    brand = str(item.get("brand", "")).strip()
    image_url = item.get("main_image")
    offers = item.get("stores")
    if not title or category_name != "Telefon" or not brand:
        raise ValueError(f"Amazon ürünü temel alanları eksik: {title}")
    if not _valid_product_image(image_url):
        raise ValueError(f"Amazon ürün görseli geçersiz: {title}")
    if not isinstance(offers, list) or not offers:
        raise ValueError(f"Amazon ürünü için teklif yok: {title}")

    item_price = float(item.get("price") or 0)
    if not math.isfinite(item_price) or item_price <= 0:
        raise ValueError(f"Amazon ürün fiyatı geçersiz: {title}")

    normalized_offers = []
    for offer in offers:
        offer_url = str(offer.get("product_url") or "").strip()
        parsed_url = urlparse(offer_url)
        amount = float(offer.get("price") or 0)
        if (
            offer.get("store_name") != "Amazon"
            or parsed_url.scheme != "https"
            or parsed_url.hostname != "www.amazon.com.tr"
            or not math.isfinite(amount)
            or amount <= 0
            or amount != item_price
        ):
            raise ValueError(f"Amazon ürünü teklifi geçersiz: {title}")
        normalized_offers.append(
            {
                "store_name": "Amazon",
                "price": amount,
                "old_price": amount,
                "product_url": offer_url,
            }
        )

    gallery = [image_url]
    return {
        "id": product_id,
        "title": title,
        "category_id": _category_id(category_name),
        "category_name": category_name,
        "brand": brand,
        "image_url": image_url,
        "image_gallery": gallery,
        "description": "",
        "tags": [],
        "refresh_rate_hz": None,
        "screen_size_inches": None,
        "offers": normalized_offers,
    }


def seed_catalog(
    db: sqlite3.Connection,
    category_icons: dict[str, str],
    products: list[dict] | None = None,
) -> int:
    load_default_catalog = products is None
    if products is None:
        try:
            with CATALOG_FILE.open(encoding="utf-8-sig") as source:
                products = json.load(source)
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Trendyol kataloğu okunamadı: {CATALOG_FILE}") from error
    if not isinstance(products, list):
        raise RuntimeError("urunler.json bir ürün listesi olmalıdır")

    normalized_by_id: dict[int, dict] = {}
    unavailable_count = 0
    for item in products:
        if item.get("scrape_status") == "unavailable":
            unavailable_count += 1
            continue
        try:
            product = _normalize_product(item)
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError(f"Geçersiz Trendyol katalog kaydı: {error}") from error

        existing = normalized_by_id.get(product["id"])
        if existing is None:
            normalized_by_id[product["id"]] = product
            continue
        if existing["title"] != product["title"]:
            LOGGER.warning(
                "Trendyol product id %s has differing titles; keeping the first",
                product["id"],
            )
        known_urls = {offer["product_url"] for offer in existing["offers"]}
        existing["offers"].extend(
            offer
            for offer in product["offers"]
            if offer["product_url"] not in known_urls
        )

    amazon_products = []
    if load_default_catalog:
        try:
            with AMAZON_CATALOG_FILE.open(encoding="utf-8-sig") as source:
                amazon_products = json.load(source)
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"Amazon kataloğu okunamadı: {AMAZON_CATALOG_FILE}"
            ) from error
        if not isinstance(amazon_products, list):
            raise RuntimeError("urunler_amazon.json bir ürün listesi olmalıdır")

    amazon_skipped = 0
    first_amazon_id = max(normalized_by_id, default=0) + 1
    for index, item in enumerate(amazon_products):
        if not isinstance(item, dict):
            raise RuntimeError("Amazon kataloğunda geçersiz ürün kaydı var")
        if not all(key in item for key in ("brand", "main_image", "price", "stores")):
            amazon_skipped += 1
            continue
        try:
            product = _normalize_amazon_product(item, first_amazon_id + index)
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError(f"Geçersiz Amazon katalog kaydı: {error}") from error
        normalized_by_id[product["id"]] = product

    if not normalized_by_id:
        raise RuntimeError("İçe aktarılabilecek geçerli katalog ürünü bulunamadı")

    today = date.today().isoformat()
    db.execute("DELETE FROM price_history")
    db.execute("DELETE FROM store_prices")
    db.execute("DELETE FROM products")
    db.execute("DELETE FROM categories")
    db.execute("DELETE FROM catalog_metadata")

    categories = {
        (product["category_id"], product["category_name"])
        for product in normalized_by_id.values()
    }
    db.executemany(
        "INSERT INTO categories (id, name, icon) VALUES (?, ?, ?)",
        [
            (category_id, name, category_icons.get(category_id, "🖥️"))
            for category_id, name in sorted(categories)
        ],
    )

    offer_rows = []
    history_rows = []
    product_rows = []
    for product in normalized_by_id.values():
        offers = sorted(product["offers"], key=lambda offer: offer["price"])
        cheapest = offers[0]
        old_price = max(cheapest["old_price"], cheapest["price"])
        product_rows.append(
            (
                product["id"],
                product["title"],
                product["category_id"],
                product["brand"],
                cheapest["price"],
                old_price,
                cheapest["store_name"],
                product["image_url"],
                product["description"],
                "TRY",
                cheapest["product_url"],
                0,
                json.dumps(product["tags"], ensure_ascii=False),
                json.dumps(product["image_gallery"], ensure_ascii=False),
                product["refresh_rate_hz"],
                product["screen_size_inches"],
            )
        )
        offer_rows.extend(
            (
                product["id"],
                offer["store_name"],
                offer["price"],
                offer["product_url"],
                0,
                f"{offer['store_name']} ürün sayfasından alınan son fiyat.",
            )
            for offer in offers
        )
        history_rows.append((product["id"], today, cheapest["price"]))

    db.executemany(
        """INSERT INTO products
           (id, title, category, brand, current_lowest_price, old_price,
            lowest_store_name, image_url, description, price_currency, source_url,
            rating, tags_json, image_gallery_json, refresh_rate_hz,
            screen_size_inches)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        product_rows,
    )
    db.executemany(
        """INSERT INTO store_prices
           (product_id, store_name, price, product_url, is_estimate, source_note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        offer_rows,
    )
    db.executemany(
        """INSERT INTO price_history (product_id, recorded_on, price)
           VALUES (?, ?, ?)""",
        history_rows,
    )

    metadata = (
        ("source", "Trendyol and Amazon product pages"),
        (
            "note",
            "Fiyatlar Trendyol ve Amazon ürün sayfalarından alınan son TL fiyatlarıdır. "
            "Fiyat geçmişi tarama tarihinden itibaren birikir.",
        ),
        ("synced_at", datetime.now(timezone.utc).isoformat()),
        ("product_count", str(len(normalized_by_id))),
        ("offer_count", str(len(offer_rows))),
        ("unavailable_count", str(unavailable_count)),
        ("amazon_unavailable_count", str(amazon_skipped)),
    )
    db.executemany(
        "INSERT INTO catalog_metadata (key, value) VALUES (?, ?)", metadata
    )
    LOGGER.info(
        "Imported %s products from Trendyol and Amazon, %s direct offers; "
        "skipped %s Trendyol and %s unavailable Amazon products",
        len(normalized_by_id),
        len(offer_rows),
        unavailable_count,
        amazon_skipped,
    )
    return len(normalized_by_id)
