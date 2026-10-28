import os

import json
import math
import re
import threading
import time
import unicodedata
import uuid
import webbrowser
import httpx
from bs4 import BeautifulSoup
from copy import deepcopy
from contextlib import asynccontextmanager
from json import JSONDecodeError
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from pathlib import Path
from urllib.parse import (
    parse_qs,
    quote,
    quote_plus,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response


HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
OPEN_BROWSER = os.environ.get("OPEN_BROWSER", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
CHECK_INTERVAL_SECONDS = 60 * 60
MAX_SEARCH_RESULTS = 50
HTTP_TIMEOUT_SECONDS = 12
STORE_SCAN_TIMEOUT_SECONDS = 36
DEFAULT_SCAN_DEPTH = 3
MAX_SCAN_DEPTH = 10
MAX_CONCURRENT_REQUESTS = 2
HTTP_HEADERS = {
    "User-Agent": "IndirimRadari/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}
ALL_STORES = "all"
ALL_FILTERS = "Tümünü Seç"
PRICE_HISTORY_LIMIT = 250

ROOT = Path(__file__).resolve().parent
PRODUCTS_FILE = ROOT / "products.json"
RADARS_FILE = ROOT / "radars.json"
OFFERS_FILE = ROOT / "offers.json"
PRICE_TOKEN = re.compile(r"(?<!\d)\d[\d.,\s]*\d(?!\d)|(?<!\d)\d(?!\d)")
PRICE_WITH_CURRENCY = re.compile(
    r"(?:₺\s*\d[\d.,\s]*\d|\d[\d.,\s]*\d\s*(?:₺|TL|TRY))",
    re.IGNORECASE,
)
PRODUCT_SELECTORS = (
    ".price-view .discounted",
    ".prc-dsc",
    '[data-testid="price-current-price"]',
    "#corePrice_feature_div .a-offscreen",
    "#apex_desktop .a-offscreen",
    "#price_inside_buybox",
    "#priceblock_dealprice",
    "#priceblock_ourprice",
    '[data-test-id="price"]',
    '[itemprop="price"]',
    ".product-detail-price",
    ".product-price",
    ".price-current",
    ".current-price",
    ".a-price .a-offscreen",
)

PRODUCTS_LOCK = threading.RLock()
CHECK_LOCK = threading.Lock()
RADARS_LOCK = threading.RLock()
OFFERS_LOCK = threading.RLock()
RADAR_SCAN_LOCK = threading.Lock()
SCAN_PROGRESS_LOCK = threading.RLock()
SCAN_PROGRESS = {
    "active": False,
    "radar_id": None,
    "query": "",
    "max_price": 0,
    "completed_stores": [],
    "pending_stores": [],
    "products": [],
}

STORE_CONFIGS = {
    "Gaming.gen.tr": {
        "domain": "gaming.gen.tr",
        "search_url": "https://www.gaming.gen.tr/?s={query}&post_type=product",
        "page_param": "paged",
        "cards": (
            "li.product",
            ".product-inner",
            ".product-small",
            ".product-item",
            ".product",
            '[class*="product-card"]',
        ),
        "prices": (
            ".price ins .amount",
            ".price .amount",
            ".price",
            ".product-price",
            '[itemprop="price"]',
        ),
    },
    "Trendyol": {
        "domain": "trendyol.com",
        "search_url": "https://www.trendyol.com/sr?q={query}",
        "page_param": "pi",
        "cards": (
            '[data-testid="product-card"]',
            ".p-card-chld",
            ".p-card-wrppr",
            ".product-card",
            '[class*="product-card"]',
        ),
        "prices": (
            ".prct-box-w",
            ".prc-box-dscntd",
            ".prc-dsc",
            ".price-view .discounted",
            '[data-testid="price-current-price"]',
        ),
    },
    "Hepsiburada": {
        "domain": "hepsiburada.com",
        "search_url": "https://www.hepsiburada.com/ara?q={query}",
        "page_param": "sayfa",
        "cards": (
            '[data-test-id="product-card"]',
            '[data-testid="product-card"]',
            "li[class*='productListContent']",
            ".productListContent-item",
            "li[class*='productList']",
            ".productListItem",
            "[data-productid]",
        ),
        "prices": (
            '[data-test-id="price"]',
            '[data-testid="price"]',
            '[itemprop="price"]',
            ".price-value",
            ".product-price",
        ),
    },
    "Amazon": {
        "domain": "amazon.com.tr",
        "search_url": "https://www.amazon.com.tr/s?k={query}",
        "page_param": "page",
        "cards": ('[data-component-type="s-search-result"]',),
        "prices": (
            ".a-price .a-offscreen",
            ".a-price-whole",
            ".a-price",
        ),
    },
    "İtopya": {
        "domain": "itopya.com",
        "search_url": "https://www.itopya.com/ara?bul={query}",
        "page_param": "pg",
        "cards": (
            ".product",
            ".product-body",
            ".productItem",
            ".product-item",
            ".product-card",
            "div[class*='product-item']",
        ),
        "prices": (
            ".product-price",
            ".price",
            ".current-price",
            '[itemprop="price"]',
        ),
    },
    "İncehesap": {
        "domain": "incehesap.com",
        "search_url": "https://www.incehesap.com/arama/{query_path}/",
        "page_param": "pg",
        "cards": (
            ".product-item",
            ".product_box",
            ".product-list-item",
            ".product-card",
            "div[data-product-id]",
            "div[data-product]",
            "div[class*='product-item']",
        ),
        "prices": (
            ".product-price",
            ".productPrice",
            ".price-current",
            ".current-price",
            '[itemprop="price"]',
        ),
    },
    "Sinerji": {
        "domain": "sinerji.gen.tr",
        "search_url": "https://www.sinerji.gen.tr/arama?q={query}",
        "page_param": "page",
        "cards": (
            "div[class*='product']",
            "div.card",
            ".product-list-item",
            ".product-item",
            ".productItem",
            ".product-card",
            ".product-box",
            '[class*="product-item"]',
        ),
        "prices": (
            ".price",
            ".product-price",
            ".new-price",
            '[itemprop="price"]',
        ),
    },
    "Tebilon": {
        "domain": "tebilon.com",
        "search_url": "https://www.tebilon.com/arama/{query_path}/",
        "page_param": "page",
        "cards": (
            ".product-item",
            ".productItem",
            ".product-card",
            ".product-box",
            '[class*="product-item"]',
        ),
        "prices": (
            ".price",
            ".product-price",
            ".new-price",
            '[itemprop="price"]',
        ),
    },
    "Vatan Bilgisayar": {
        "domain": "vatanbilgisayar.com",
        "search_url": "https://www.vatanbilgisayar.com/arama/{query_path}/",
        "page_param": "page",
        "cards": (
            ".product-list",
            ".product-list__item",
            ".product-item",
            ".product-card",
            '[class*="product-list"]',
        ),
        "prices": (
            ".product-list__price",
            ".product-list__content .product-list__price",
            ".product-list__price--current",
            ".product-price",
            '[itemprop="price"]',
        ),
    },
    "N11": {
        "domain": "n11.com",
        "search_url": "https://www.n11.com/arama?q={query}",
        "page_param": "pg",
        "cards": (
            ".column",
            ".product-item",
            ".pro",
            ".product-card",
            '[class*="productList"]',
        ),
        "prices": (
            ".price",
            ".newPrice",
            ".priceContainer",
            ".product-price",
            '[itemprop="price"]',
        ),
    },
}

NEGATIVE_KEYWORD_STEMS = (
    "kolu",
    "stand",
    "aski",
    "askilik",
    "koruyucu",
    "film",
    "kablo",
    "adapt",
    "temizle",
    "canta",
    "kilif",
    "braket",
    "tutucu",
    "amortisor",
    "yedek",
    "replacement",
    "sogutucu",
    "cooler",
    "kirilm",
    "ekran koruma",
    "guc kablo",
)

STORE_GROUPS = {
    "Hazır Sistem & Oyuncu Mağazaları": (
        "Gaming.gen.tr",
        "İtopya",
        "İncehesap",
        "Sinerji",
        "Tebilon",
        "Vatan Bilgisayar",
    ),
    "Pazaryerleri": (
        "Amazon",
        "Hepsiburada",
        "Trendyol",
        "N11",
    ),
}

CATEGORY_CATALOG = {
    "Bilgisayar & Donanım": {
        "Masaüstü Bilgisayar": ("Gaming", "İş İstasyonu", "Hazır Sistem"),
        "Monitör": (),
        "Ekran Kartı": ("RTX 4060", "RTX 4070", "RX 7600"),
        "Laptop": ("RTX 4060", "RTX 4070", "16GB RAM", "OLED"),
        "İşlemci": ("Ryzen 7", "Intel Core i7", "Ryzen 5"),
        "Anakart": ("AM5", "LGA1700", "DDR5"),
        "Bellek (RAM)": ("16GB", "32GB", "DDR5"),
        "SSD & Depolama": ("1TB NVMe", "2TB NVMe", "Harici SSD"),
        "Bilgisayar Kasası": ("Mini Tower", "Mid Tower", "Full Tower"),
        "Güç Kaynağı": ("650W", "750W", "80+ Gold"),
        "Soğutma": ("Sıvı Soğutma", "240mm", "360mm"),
    },
    "Çevre Birimleri": {
        "Klavye": ("Mekanik", "Kablosuz", "TKL"),
        "Oyuncu Faresi": ("Kablosuz", "8000 DPI+", "Hafif"),
        "Kulaklık & Mikrofon": ("Kablosuz", "7.1 Surround", "ANC"),
        "Webcam": ("1080p", "4K", "Otomatik Odaklama"),
        "Yazıcı & Tarayıcı": ("Lazer", "Tanklı", "Çok Fonksiyonlu"),
        "Monitör Aksesuarları": ("Monitör Kolu", "VESA", "Işık Barı"),
        "Ağ Ürünleri": ("Wi-Fi 6", "Wi-Fi 7", "Mesh"),
    },
    "Telefon & Tablet": {
        "Akıllı Telefon": ("256GB", "OLED", "5G"),
        "Tablet": ("128GB", "256GB", "Kalem Destekli"),
        "Akıllı Saat": ("GPS", "LTE", "AMOLED"),
        "Telefon Aksesuarları": ("Hızlı Şarj", "Kablosuz Şarj", "Powerbank"),
    },
    "Konsol & Oyun": {
        "Oyun Konsolu": ("PlayStation 5", "Xbox Series X", "Nintendo Switch"),
        "Konsol Oyunları": ("PlayStation", "Xbox", "Nintendo Switch"),
        "VR Gözlük": ("Meta Quest", "PlayStation VR2", "256GB"),
        "Gamepad & Aksesuar": ("Kablosuz Gamepad", "Direksiyon Seti", "Şarj İstasyonu"),
    },
    "Ev Aletleri": {
        "Robot Süpürge": ("Lidar", "Otomatik Boşaltma", "Paspaslı"),
        "Elektrikli Süpürge": ("Dikey", "Kablosuz", "HEPA Filtre"),
        "Kahve Makinesi": ("Tam Otomatik", "Espresso", "Filtre Kahve"),
        "Küçük Ev Aletleri": ("Airfryer", "Blender", "Mutfak Robotu"),
    },
    "TV & Ses": {
        "Televizyon": ("55 inç", "65 inç", "OLED"),
        "Kulaklık": ("Bluetooth", "ANC", "Kablosuz"),
        "Hoparlör": ("Bluetooth", "Soundbar", "Taşınabilir"),
        "Projeksiyon": ("Full HD", "4K", "Taşınabilir"),
    },
}

MONITOR_FILTERS = {
    "refresh_rate": (
        ALL_FILTERS,
        "60Hz",
        "75Hz",
        "100Hz",
        "120Hz",
        "144Hz",
        "165Hz",
        "170Hz",
        "180Hz",
        "200Hz",
        "240Hz",
        "280Hz",
        "360Hz",
        "500Hz+",
    ),
    "resolution": (
        ALL_FILTERS,
        "HD (720p)",
        "FHD (1080p)",
        "QHD / 2K (1440p)",
        "UW-QHD (Ultrawide 1440p)",
        "UHD / 4K (2160p)",
        "8K",
    ),
    "panel_type": (
        ALL_FILTERS,
        "IPS",
        "Fast IPS",
        "VA",
        "OLED",
        "QD-OLED",
        "TN",
        "Mini-LED",
    ),
}

NON_PRODUCT_PRICE_CONTEXT = re.compile(
    r"kargo|kupon|taksit|aylık|ayda|bedava|puan|kazanç|tasarruf|sepette|"
    r"shipping|coupon|installment|per month|monthly|free delivery",
    re.IGNORECASE,
)
RESULT_CARD_TITLE_SELECTORS = (
    "h2",
    "h3",
    ".product-title",
    ".title",
    ".name",
    '[data-testid*="title"]',
)
FALLBACK_RESULT_CARD_SELECTORS = (
    "article",
    "li[class*='product']",
    "div[class*='product']",
    "[data-testid*='product']",
    "[data-test-id*='product']",
    "[data-productid]",
)
FALLBACK_PRICE_SELECTORS = (
    '[class*="price"]',
    '[class*="fiyat"]',
    '[data-testid*="price"]',
    '[data-test-id*="price"]',
    '[itemprop="price"]',
)


def _initial_price_history(entry, price_key, timestamp_key):
    try:
        price = float(entry.get(price_key))
    except (TypeError, ValueError, OverflowError):
        return []
    if not math.isfinite(price) or price <= 0:
        return []
    timestamp = entry.get(timestamp_key) or datetime.now().astimezone().isoformat(timespec="milliseconds")
    return [{"timestamp": timestamp, "price": price}]


def load_products():
    with PRODUCTS_LOCK:
        if not PRODUCTS_FILE.exists():
            save_products([])
        try:
            products = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"{PRODUCTS_FILE.name} okunamadı: {error}") from error
        if not isinstance(products, list):
            raise RuntimeError(f"{PRODUCTS_FILE.name} bir ürün listesi olmalıdır.")
        changed = False
        for product in products:
            if (
                not isinstance(product, dict)
                or not isinstance(product.get("id"), str)
                or not _valid_product_url(product.get("url"))
            ):
                raise RuntimeError(f"{PRODUCTS_FILE.name} geçersiz bir ürün içeriyor.")
            try:
                target = float(product.get("target"))
            except (TypeError, ValueError, OverflowError) as error:
                raise RuntimeError(
                    f"{PRODUCTS_FILE.name} geçersiz bir hedef fiyat içeriyor."
                ) from error
            if not math.isfinite(target) or target <= 0:
                raise RuntimeError(
                    f"{PRODUCTS_FILE.name} geçersiz bir hedef fiyat içeriyor."
                )
            history = product.get("price_history")
            if not isinstance(history, list):
                history = []
            if not history:
                history = _initial_price_history(product, "current_price", "last_checked")
            if product.get("price_history") != history:
                product["price_history"] = history
                changed = True
        if changed:
            save_products(products)
        return products


def save_products(products):
    with PRODUCTS_LOCK:
        temporary_file = PRODUCTS_FILE.with_suffix(".json.tmp")
        try:
            temporary_file.write_text(
                json.dumps(products, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_file.replace(PRODUCTS_FILE)
        except OSError as error:
            raise RuntimeError(f"{PRODUCTS_FILE.name} kaydedilemedi: {error}") from error


def _price_from_token(token):
    match = PRICE_TOKEN.search(token)
    if not match:
        return None
    token = match.group()
    token = re.sub(r"\s+", "", token.replace("\u00a0", " "))
    if "," in token and "." in token:
        decimal_separator = "," if token.rfind(",") > token.rfind(".") else "."
        grouping_separator = "." if decimal_separator == "," else ","
        token = token.replace(grouping_separator, "")
        token = token.replace(decimal_separator, ".")
    elif "," in token or "." in token:
        separator = "," if "," in token else "."
        whole, fraction = token.rsplit(separator, 1)
        if len(fraction) in (1, 2):
            token = whole.replace(",", "").replace(".", "") + "." + fraction
        else:
            token = token.replace(",", "").replace(".", "")

    try:
        price = Decimal(token)
    except InvalidOperation:
        return None
    if not price.is_finite() or price <= 0:
        return None
    return float(price)


def parse_price(text, allow_bare=False):
    normalized = unescape(text).replace("\u00a0", " ").strip()
    if not normalized or NON_PRODUCT_PRICE_CONTEXT.search(normalized):
        return None

    currency_matches = list(PRICE_WITH_CURRENCY.finditer(normalized))
    if currency_matches:
        return _price_from_token(currency_matches[-1].group())
    if allow_bare and re.fullmatch(r"[\d.,\s]+", normalized):
        match = PRICE_TOKEN.fullmatch(normalized)
        if match:
            return _price_from_token(match.group())
    return None


def _prices_from_element_text(text, allow_bare=False):
    prices = []
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate or NON_PRODUCT_PRICE_CONTEXT.search(candidate):
            continue
        if allow_bare:
            price = parse_price(candidate, allow_bare=True)
            if price is not None:
                prices.append(price)
                continue
        for match in PRICE_WITH_CURRENCY.finditer(candidate):
            price = _price_from_token(match.group())
            if price is not None:
                prices.append(price)
    return prices


def extract_price(soup, store=None):
    selectors = list(PRODUCT_SELECTORS)
    if store in STORE_CONFIGS:
        selectors = list(STORE_CONFIGS[store]["prices"]) + selectors
    selectors.append('meta[itemprop="price"]')
    seen = set()
    for selector in selectors:
        if selector in seen:
            continue
        seen.add(selector)
        for element in soup.select(selector)[:6]:
            if selector == 'meta[itemprop="price"]':
                price = parse_price(element.get("content", ""), allow_bare=True)
                if price is not None:
                    return price
            text = element.get_text("\n", strip=True)
            prices = _prices_from_element_text(
                text,
                allow_bare=selector in STORE_CONFIGS.get(store, {}).get("prices", ()),
            )
            if prices:
                return prices[-1]
    return None


def _http_image_url(value, page_url):
    if not value:
        return None
    candidates = value.strip().split(",", 1)[0].split()
    if not candidates:
        return None
    image_candidate = candidates[0]
    if image_candidate.startswith("data:"):
        return None
    image_url = urljoin(page_url, image_candidate)
    parsed_image = urlsplit(image_url)
    if parsed_image.scheme in ("http", "https") and parsed_image.netloc:
        return image_url
    return None


def _image_url_from_element(image, page_url):
    for attribute in ("data-src", "data-lazy-src", "data-original", "src", "data-srcset", "srcset"):
        value = image.get(attribute)
        image_url = _http_image_url(value, page_url)
        if image_url:
            return image_url
    return None


def _image_url_from_card(card, page_url):
    for image in card.select("img")[:5]:
        image_url = _image_url_from_element(image, page_url)
        if image_url:
            return image_url
    return None


def _product_image_url(soup, page_url):
    for selector in (
        'meta[property="og:image"]',
        'meta[name="twitter:image"]',
        '[data-testid*="image"] img',
        "main img",
        "img",
    ):
        for image in soup.select(selector)[:8]:
            if selector.startswith("meta["):
                image_url = _http_image_url(image.get("content"), page_url)
                if image_url:
                    return image_url
                continue
            image_url = _image_url_from_element(image, page_url)
            if image_url:
                return image_url
    return None


def _jsonld_products_from_page(soup, store, search_url):
    products = []
    seen_urls = set()
    scripts = soup.select('script[type="application/ld+json"]')[:10]

    def visit(value):
        if isinstance(value, list):
            for entry in value:
                visit(entry)
            return
        if not isinstance(value, dict):
            return

        graph = value.get("@graph")
        if graph is not None:
            visit(graph)
        item_type = value.get("@type", [])
        if isinstance(item_type, str):
            item_type = [item_type]
        elif not isinstance(item_type, list):
            item_type = []
        normalized_types = {
            entry.rsplit("/", 1)[-1]
            for entry in item_type
            if isinstance(entry, str)
        }
        if "Product" in normalized_types:
            offers = value.get("offers", {})
            if isinstance(offers, list):
                offers = next(
                    (entry for entry in offers if isinstance(entry, dict)), {}
                )
            if not isinstance(offers, dict):
                offers = {}
            price = parse_price(
                str(offers.get("price") or offers.get("lowPrice") or ""),
                allow_bare=True,
            )
            title = value.get("name")
            href = value.get("url") or offers.get("url")
            product_url = urljoin(search_url, href or "")
            parsed_url = urlsplit(product_url)
            image = value.get("image")
            if isinstance(image, list):
                image = next((entry for entry in image if isinstance(entry, str)), None)
            if isinstance(image, dict):
                image = image.get("url") or image.get("contentUrl")
            if (
                isinstance(title, str)
                and title.strip()
                and isinstance(href, str)
                and href.strip()
                and price is not None
                and _host_matches(
                    parsed_url.hostname or "",
                    STORE_CONFIGS[store]["domain"],
                )
                and product_url not in seen_urls
            ):
                seen_urls.add(product_url)
                products.append(
                    {
                        "title": title.strip(),
                        "description": title.strip(),
                        "url": product_url,
                        "price": price,
                        "store": store,
                        "image_url": _http_image_url(image, search_url),
                    }
                )
        for key in ("itemListElement", "item", "mainEntity"):
            if key in value:
                visit(value[key])
        item = value.get("item")
        if isinstance(item, dict):
            visit(item)

    for script in scripts:
        try:
            parsed_json = json.loads(script.string or script.get_text())
        except JSONDecodeError:
            continue
        visit(parsed_json)
    return products


def check_price(url, target_price):
    print(f"\nÜrün kontrol ediliyor: {url}", flush=True)
    hostname = (urlsplit(url).hostname or "").lower()
    store = next(
        (name for name, config in STORE_CONFIGS.items() if config["domain"] in hostname),
        None,
    )
    try:
        with httpx.Client(
            headers=HTTP_HEADERS,
            timeout=HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            current_price = extract_price(soup, store)
            image_url = _product_image_url(soup, str(response.url))
    except Exception as error:
        message = f"Fiyat kontrolü başarısız: {error}"
        print(f"❌ {message}", flush=True)
        return {
            "current_price": None,
            "last_price_label": None,
            "image_url": None,
            "message": message,
        }

    if current_price is None:
        message = "Sayfada okunabilir bir ürün fiyatı bulunamadı."
        print(f"⚠️ {message}", flush=True)
        return {
            "current_price": None,
            "last_price_label": None,
            "image_url": None,
            "message": message,
        }

    message = "Hedef yakalandı." if current_price <= target_price else "Henüz hedef fiyata düşmedi."
    current_display = format_price(current_price)
    target_display = format_price(target_price)
    print(f"Son Kontrol Edilen Güncel Fiyat: {current_display} TL", flush=True)
    print(f"Hedef fiyat: {target_display} TL", flush=True)
    print(message, flush=True)
    return {
        "current_price": current_price,
        "last_price_label": f"Son Kontrol Edilen Güncel Fiyat: {current_display} TL",
        "image_url": image_url,
        "message": message,
    }


def format_price(price):
    formatted = f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return formatted[:-3] if formatted.endswith(",00") else formatted


def _host_matches(hostname, domain):
    normalized_host = hostname.lower().rstrip(".")
    normalized_domain = domain.lower().rstrip(".")
    return normalized_host == normalized_domain or normalized_host.endswith(
        f".{normalized_domain}"
    )


def _append_price_history(entry, price, recorded_at=None):
    history = entry.get("price_history")
    if not isinstance(history, list):
        history = []
    timestamp = recorded_at or datetime.now().astimezone().isoformat(timespec="milliseconds")
    history.append({"timestamp": timestamp, "price": float(price)})
    return history[-PRICE_HISTORY_LIMIT:]


def _detect_shock_drop(previous_offer, current_price, radar, checked_at):
    history = previous_offer.get("price_history")
    previous_price = None
    if isinstance(history, list) and history:
        last_point = history[-1]
        if isinstance(last_point, dict):
            try:
                previous_price = float(last_point.get("price"))
            except (TypeError, ValueError, OverflowError):
                previous_price = None
    if previous_price is None:
        try:
            previous_price = float(previous_offer.get("price"))
        except (TypeError, ValueError, OverflowError):
            return None

    mode = radar.get("shock_mode", "none")
    try:
        threshold = float(radar.get("shock_threshold", 0))
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        mode not in ("amount", "percent")
        or not math.isfinite(threshold)
        or threshold <= 0
        or previous_price <= 0
        or current_price >= previous_price
    ):
        return None

    decrease = previous_price - current_price
    decrease_percent = decrease / previous_price * 100
    reached = decrease >= threshold if mode == "amount" else decrease_percent >= threshold
    if not reached:
        return None
    return {
        "timestamp": checked_at,
        "previous_price": previous_price,
        "current_price": float(current_price),
        "decrease": decrease,
        "decrease_percent": decrease_percent,
        "mode": mode,
        "threshold": threshold,
    }


def check_products():
    if not CHECK_LOCK.acquire(blocking=False):
        print("Fiyat kontrolü zaten sürüyor; yeni istek atlandı.", flush=True)
        return None

    results = []
    try:
        products = load_products()
        for product in products:
            result = check_price(product["url"], float(product["target"]))
            checked_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
            checked_product = {
                **product,
                **result,
                "last_checked": checked_at,
            }
            if not result.get("image_url"):
                checked_product["image_url"] = product.get("image_url")
            if result["current_price"] is not None:
                checked_product["price_history"] = _append_price_history(
                    product,
                    result["current_price"],
                    checked_at,
                )
            results.append(checked_product)

            with PRODUCTS_LOCK:
                latest_products = load_products()
                for index, latest in enumerate(latest_products):
                    if latest["id"] == product["id"]:
                        latest_products[index] = checked_product
                        break
                save_products(latest_products)
    finally:
        CHECK_LOCK.release()
    return results


def _load_json_list(path, lock):
    with lock:
        if not path.exists():
            _save_json_list(path, [], lock)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"{path.name} okunamadı: {error}") from error
        if not isinstance(data, list):
            raise RuntimeError(f"{path.name} bir liste olmalıdır.")
        return data


def _save_json_list(path, entries, lock):
    with lock:
        temporary_file = path.with_suffix(".json.tmp")
        try:
            temporary_file.write_text(
                json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_file.replace(path)
        except OSError as error:
            raise RuntimeError(f"{path.name} kaydedilemedi: {error}") from error


def load_radars():
    radars = _load_json_list(RADARS_FILE, RADARS_LOCK)
    for radar in radars:
        if not isinstance(radar, dict) or not isinstance(radar.get("id"), str):
            raise RuntimeError(f"{RADARS_FILE.name} geçersiz bir radar içeriyor.")
        if not isinstance(radar.get("query"), str):
            raise RuntimeError(f"{RADARS_FILE.name} geçersiz bir arama içeriyor.")
        stores = radar.get("stores")
        if stores is not None:
            if (
                not isinstance(stores, list)
                or not stores
                or any(
                    not isinstance(store, str) or store not in STORE_CONFIGS
                    for store in stores
                )
                or len(set(stores)) != len(stores)
            ):
                raise RuntimeError(f"{RADARS_FILE.name} geçersiz mağaza seçimi içeriyor.")
        elif radar.get("store") not in (*STORE_CONFIGS, ALL_STORES):
            raise RuntimeError(f"{RADARS_FILE.name} geçersiz mağaza içeriyor.")
        try:
            budget = float(radar.get("max_price"))
        except (TypeError, ValueError, OverflowError) as error:
            raise RuntimeError(f"{RADARS_FILE.name} geçersiz bütçe içeriyor.") from error
        if not math.isfinite(budget) or budget <= 0:
            raise RuntimeError(f"{RADARS_FILE.name} geçersiz bütçe içeriyor.")
        try:
            min_price = float(radar.get("min_price", 0))
        except (TypeError, ValueError, OverflowError) as error:
            raise RuntimeError(f"{RADARS_FILE.name} geçersiz minimum fiyat içeriyor.") from error
        if not math.isfinite(min_price) or min_price < 0 or min_price > budget:
            raise RuntimeError(f"{RADARS_FILE.name} geçersiz fiyat aralığı içeriyor.")
        scan_depth = radar.get("scan_depth", DEFAULT_SCAN_DEPTH)
        if (
            not isinstance(scan_depth, int)
            or isinstance(scan_depth, bool)
            or not 1 <= scan_depth <= MAX_SCAN_DEPTH
        ):
            raise RuntimeError(f"{RADARS_FILE.name} geçersiz tarama derinliği içeriyor.")
    return radars


def load_offers():
    offers = _load_json_list(OFFERS_FILE, OFFERS_LOCK)
    changed = False
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        history = offer.get("price_history")
        if not isinstance(history, list):
            history = []
        if not history:
            history = _initial_price_history(offer, "price", "last_seen")
        if offer.get("price_history") != history:
            offer["price_history"] = history
            changed = True
    if changed:
        _save_json_list(OFFERS_FILE, offers, OFFERS_LOCK)
    return offers


def _price_from_card(card, store):
    prices = []
    selectors = (
        tuple(STORE_CONFIGS[store]["prices"])
        + FALLBACK_PRICE_SELECTORS
        + ("span", "div")
    )
    for selector in dict.fromkeys(selectors):
        for element in card.select(selector)[:50]:
            text = element.get_text("\n", strip=True)
            if text:
                prices.extend(
                    _prices_from_element_text(
                        text,
                        allow_bare=selector in STORE_CONFIGS[store]["prices"],
                    )
                )
            if not text:
                price = parse_price(element.get("content", ""), allow_bare=True)
                if price is not None:
                    prices.append(price)
        if prices:
            return prices[-1]
    prices = _prices_from_element_text(card.get_text("\n", strip=True))
    if prices:
        return prices[0]
    return None


def _product_from_card(card, store, search_url):
    title = ""
    link = None
    for selector in RESULT_CARD_TITLE_SELECTORS:
        heading = card.select_one(selector)
        if heading:
            title = (
                heading.get_text(" ", strip=True)
                or heading.get("title")
                or heading.get("aria-label")
                or heading.get("alt")
                or ""
            ).strip()
            link = heading if heading.name == "a" else heading.select_one("a")
            break
    if link is None:
        if card.name == "a":
            link = card
        else:
            link = (
                card.select_one('a[href*="/dp/"]')
                or card.select_one("a[href][title]")
                or card.select_one("a[href][aria-label]")
                or card.select_one("a[href]")
            )
    href = link.get("href") if link else None
    if not title and link:
        title = (
            link.get_text(" ", strip=True)
            or link.get("title")
            or link.get("aria-label")
            or ""
        ).strip()
        if not title:
            image = link.select_one("img[alt]")
            title = image.get("alt", "").strip() if image else ""

    product_url = urljoin(search_url, href or "")
    parsed_url = urlsplit(product_url)
    if store == "Amazon" and "/sspa/click" in parsed_url.path:
        wrapped_url = parse_qs(parsed_url.query).get("url", [])
        if wrapped_url:
            product_url = urljoin(search_url, wrapped_url[0])
            parsed_url = urlsplit(product_url)
    expected_domain = STORE_CONFIGS[store]["domain"]
    if (
        not title
        or not href
        or parsed_url.scheme not in ("http", "https")
        or not _host_matches(parsed_url.hostname or "", expected_domain)
    ):
        return None
    price = _price_from_card(card, store)
    if price is None:
        return None
    description = card.get_text(" ", strip=True) or title
    image_url = _image_url_from_card(card, search_url)
    return {
        "title": title,
        "description": description,
        "url": product_url,
        "price": price,
        "store": store,
        "image_url": image_url,
    }


def _search_query_for_store(radar, store):
    query = str(radar.get("query", "")).strip()
    if store == "İtopya":
        if radar.get("subcategory") == "Monitör":
            query = "monitor"
        query = query.translate(str.maketrans({"ı": "i", "İ": "I"}))
        query = unicodedata.normalize("NFKD", query).encode("ascii", "ignore").decode("ascii")
        query = re.sub(r"[^a-zA-Z0-9\s]", " ", query)
    return re.sub(r"\s+", " ", query).strip()


def _search_page_url(search_url, page_param, page_number):
    if page_number <= 1:
        return search_url
    parts = urlsplit(search_url)
    query = parse_qs(parts.query, keep_blank_values=True)
    query[page_param] = [str(page_number)]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment)
    )


def _search_radar(radar):
    store = radar["store"]
    if not isinstance(store, str) or store not in STORE_CONFIGS:
        return [], f"Geçersiz mağaza seçimi: {store!r}."
    try:
        selected_stores = _selected_stores(radar)
    except ValueError as error:
        return [], str(error)
    if store not in selected_stores:
        return [], f"{store} seçili mağaza listesinde bulunmuyor."
    config = STORE_CONFIGS[store]
    search_query = _search_query_for_store(radar, store)
    search_url = config["search_url"].format(
        query=quote_plus(search_query),
        query_path=quote(search_query, safe=""),
    )
    try:
        scan_depth = min(
            max(int(radar.get("scan_depth", DEFAULT_SCAN_DEPTH)), 1),
            MAX_SCAN_DEPTH,
        )
    except (TypeError, ValueError, OverflowError):
        scan_depth = DEFAULT_SCAN_DEPTH
    try:
        store_deadline = time.monotonic() + STORE_SCAN_TIMEOUT_SECONDS
        products = []
        seen_urls = set()
        errors = []
        challenge_markers = (
            "cloudflare",
            "captcha",
            "security verification",
            "performing security verification",
            "attention required",
            "access denied",
            "erişim engellendi",
        )
        with httpx.Client(
            headers=HTTP_HEADERS,
            timeout=HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            for page_number in range(1, scan_depth + 1):
                remaining_seconds = store_deadline - time.monotonic()
                if remaining_seconds <= 0:
                    errors.append(
                        f"{store} taraması {STORE_SCAN_TIMEOUT_SECONDS} saniyelik "
                        "mağaza süresini aştı."
                    )
                    break
                page_url = _search_page_url(
                    search_url,
                    config["page_param"],
                    page_number,
                )
                try:
                    response = client.get(
                        page_url,
                        timeout=min(HTTP_TIMEOUT_SECONDS, remaining_seconds),
                    )
                    response.raise_for_status()
                except httpx.HTTPStatusError as error:
                    errors.append(
                        f"{store} araması HTTP {error.response.status_code} "
                        f"yanıtı verdi (sayfa {page_number})."
                    )
                    if page_number == 1:
                        break
                    continue
                except httpx.HTTPError as error:
                    errors.append(
                        f"{store} sayfa {page_number} alınamadı: {error}"
                    )
                    break

                soup = BeautifulSoup(response.text, "html.parser")
                cards = []
                for selector in (*config["cards"], *FALLBACK_RESULT_CARD_SELECTORS):
                    cards = soup.select(selector)
                    if cards:
                        break
                page_url = str(response.url)
                if not cards:
                    jsonld_products = _jsonld_products_from_page(
                        soup,
                        store,
                        page_url,
                    )
                    for product in jsonld_products:
                        parsed_url = urlsplit(product["url"])
                        product_identity = urlunsplit(
                            (
                                parsed_url.scheme.lower(),
                                parsed_url.netloc.lower(),
                                parsed_url.path.rstrip("/"),
                                "",
                                "",
                            )
                        )
                        if product_identity not in seen_urls:
                            seen_urls.add(product_identity)
                            products.append(product)
                    if jsonld_products:
                        break
                    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
                    body = soup.body.get_text(" ", strip=True).lower() if soup.body else ""
                    if any(word in title + body[:1200] for word in challenge_markers):
                        errors.append(
                            f"{store} otomatik aramaya güvenlik doğrulaması uyguladı."
                        )
                    elif page_number == 1:
                        errors.append(
                            f"{store} arama sayfasında ürün kartı bulunamadı."
                        )
                    break

                parsed_count = 0
                for card in cards[:10]:
                    if time.monotonic() >= store_deadline:
                        break
                    product = _product_from_card(card, store, page_url)
                    if product:
                        parsed_count += 1
                        parsed_product_url = urlsplit(product["url"])
                        product_identity = urlunsplit(
                            (
                                parsed_product_url.scheme.lower(),
                                parsed_product_url.netloc.lower(),
                                parsed_product_url.path.rstrip("/"),
                                "",
                                "",
                            )
                        )
                        if product_identity not in seen_urls:
                            seen_urls.add(product_identity)
                            products.append(product)
                for product in _jsonld_products_from_page(soup, store, page_url):
                    parsed_product_url = urlsplit(product["url"])
                    product_identity = urlunsplit(
                        (
                            parsed_product_url.scheme.lower(),
                            parsed_product_url.netloc.lower(),
                            parsed_product_url.path.rstrip("/"),
                            "",
                            "",
                        )
                    )
                    if product_identity not in seen_urls:
                        seen_urls.add(product_identity)
                        products.append(product)
                if cards and not parsed_count:
                    errors.append(
                        f"{store} sayfa {page_number}: ürün kartları bulundu ancak "
                        "başlık, mağaza bağlantısı veya fiyat çıkarılamadı."
                    )
        print(f"[LOG] {store}: {len(products)} ürün", flush=True)
        for message in errors:
            print(f"[WARN] {message}", flush=True)
        return products, " ".join(errors) or None
    except Exception as error:
        message = f"{store} araması başarısız: {error}"
        print(f"❌ {message}", flush=True)
        return [], message


def _selected_stores(radar):
    stores = radar.get("stores")
    if isinstance(stores, list):
        if any(not isinstance(store, str) or store not in STORE_CONFIGS for store in stores):
            raise ValueError("Radar geçersiz mağaza içeriyor.")
        return list(dict.fromkeys(stores))
    legacy_store = radar.get("store")
    if legacy_store == ALL_STORES:
        return list(STORE_CONFIGS)
    if legacy_store not in STORE_CONFIGS:
        raise ValueError("Radar geçersiz mağaza içeriyor.")
    return [legacy_store]


def _products_from_selected_stores(products, selected_stores):
    allowed_stores = frozenset(selected_stores)
    return [
        product
        for product in products
        if isinstance(product, dict)
        and isinstance(product.get("store"), str)
        and product.get("store") in allowed_stores
    ]


def _begin_scan_progress(radar, stores):
    global SCAN_PROGRESS
    with SCAN_PROGRESS_LOCK:
        SCAN_PROGRESS = {
            "active": True,
            "radar_id": radar["id"],
            "query": radar.get("query", ""),
            "min_price": float(radar.get("min_price", 0)),
            "max_price": float(radar.get("max_price", 0)),
            "selected_filters": _selected_filter_map(radar),
            "completed_stores": [],
            "pending_stores": list(stores),
            "products": [],
        }


def _record_scan_store_result(radar, store, products, error):
    global SCAN_PROGRESS
    allowed_stores = frozenset(_selected_stores(radar))
    eligible_products = [
        product
        for product in products
        if isinstance(product, dict)
        and product.get("store") == store
        and store in allowed_stores
        and float(radar.get("min_price", 0))
        <= product.get("price", float("inf"))
        <= float(radar["max_price"])
        and _matches_radar_filters(product, radar)
    ]
    with SCAN_PROGRESS_LOCK:
        if SCAN_PROGRESS.get("radar_id") != radar.get("id"):
            return
        completed_stores = SCAN_PROGRESS["completed_stores"]
        if store not in completed_stores:
            completed_stores.append(store)
        SCAN_PROGRESS["pending_stores"] = [
            pending
            for pending in SCAN_PROGRESS["pending_stores"]
            if pending != store
        ]
        existing_urls = {
            product["url"] for product in SCAN_PROGRESS["products"]
        }
        for product in eligible_products:
            if product["url"] in existing_urls:
                continue
            existing_urls.add(product["url"])
            SCAN_PROGRESS["products"].append(
                {
                    **product,
                    "id": f"scan-{radar['id']}-{len(existing_urls)}",
                    "radar_id": radar["id"],
                    "query": radar.get("query", ""),
                    "min_price": float(radar.get("min_price", 0)),
                    "max_price": float(radar["max_price"]),
                    "active": True,
                    "last_seen": datetime.now().astimezone().isoformat(
                        timespec="milliseconds"
                    ),
                    "selected_filters": SCAN_PROGRESS["selected_filters"],
                    "provisional": True,
                }
            )
        SCAN_PROGRESS["last_error"] = error


def _finish_scan_progress(radar_id):
    global SCAN_PROGRESS
    with SCAN_PROGRESS_LOCK:
        if SCAN_PROGRESS.get("radar_id") == radar_id:
            SCAN_PROGRESS["active"] = False
            SCAN_PROGRESS["pending_stores"] = []
            SCAN_PROGRESS["products"] = []


def _search_radar_stores(radar, stores=None, on_store_complete=None):
    results = []
    errors = []
    selected_stores = _selected_stores(radar)
    stores = selected_stores if stores is None else list(stores)
    if not stores or any(
        not isinstance(store, str)
        or store not in STORE_CONFIGS
        or store not in selected_stores
        for store in stores
    ):
        raise ValueError("Tarama mağazaları seçili mağaza listesiyle uyuşmuyor.")
    allowed_stores = frozenset(selected_stores)
    with ThreadPoolExecutor(
        max_workers=min(len(stores), MAX_CONCURRENT_REQUESTS)
    ) as executor:
        futures = {
            executor.submit(
                _search_radar,
                {**radar, "store": store},
            ): store
            for store in stores
        }
        for future in as_completed(futures):
            store = futures[future]
            try:
                products, error = future.result()
            except Exception as error:
                products, error = [], f"{store} araması başarısız: {error}"
            results.extend(
                product
                for product in products
                if isinstance(product, dict)
                and product.get("store") == store
                and product.get("store") in allowed_stores
            )
            if error:
                errors.append(error)
            if on_store_complete is not None:
                try:
                    on_store_complete(store, products, error)
                except Exception as callback_error:
                    message = f"{store} ara sonuçları yayımlanamadı: {callback_error}"
                    errors.append(message)
                    print(f"[ERROR] {message}", flush=True)
    results.sort(key=lambda product: product["price"])
    return results, errors


def filter_saved_offers(query="", min_price=None, max_price=None, stores=None):
    normalized_query = _normalized_filter_text(query.strip()) if query else ""
    query_terms = normalized_query.split()
    store_filter = set(stores or [])
    offers = load_offers()
    matching = []
    for offer in offers:
        if not offer.get("active"):
            continue
        price = offer.get("price")
        if not isinstance(price, (int, float)) or not math.isfinite(price):
            continue
        if min_price is not None and price < min_price:
            continue
        if max_price is not None and price > max_price:
            continue
        if store_filter and offer.get("store") not in store_filter:
            continue
        searchable_text = _normalized_filter_text(
            f"{offer.get('title', '')} {offer.get('query', '')} "
            f"{offer.get('category', '')} {offer.get('subcategory', '')} "
            f"{offer.get('store', '')}"
        )
        if query_terms and not all(term in searchable_text for term in query_terms):
            continue
        matching.append(offer)
    matching.sort(key=lambda offer: offer["price"])
    return matching


def _radar_query_from_selection(payload):
    category = payload.get("category")
    subcategory = payload.get("subcategory")
    feature = payload.get("feature")
    if category not in CATEGORY_CATALOG:
        raise ValueError("Listeden geçerli bir ana kategori seçin.")
    subcategories = CATEGORY_CATALOG[category]
    if subcategory not in subcategories:
        raise ValueError("Seçilen ana kategoriye uygun bir alt kategori seçin.")
    selected_payload = payload.get("selected_filters")
    if subcategory == "Monitör":
        monitor_filters = {}
        for key, allowed_values in MONITOR_FILTERS.items():
            values = (
                selected_payload.get(key, [])
                if isinstance(selected_payload, dict)
                else payload.get(key, [])
            )
            if isinstance(values, str):
                values = [] if values in ("", ALL_FILTERS) else [values]
            if not isinstance(values, list) or any(
                not isinstance(value, str)
                or value not in allowed_values
                or value == ALL_FILTERS
                for value in values
            ):
                raise ValueError(f"Monitör için geçersiz filtre: {key}.")
            monitor_filters[key] = list(dict.fromkeys(values))
        selected_filters = {
            "features": [],
            **monitor_filters,
        }
    else:
        allowed_features = subcategories[subcategory]
        if isinstance(selected_payload, dict):
            features = selected_payload.get("features", [])
        elif isinstance(selected_payload, list):
            features = selected_payload
        elif feature in (None, "", ALL_FILTERS):
            features = []
        else:
            features = [feature]
        if not isinstance(features, list) or any(
            not isinstance(value, str) or value not in allowed_features
            for value in features
        ):
            raise ValueError("Seçilen alt kategoriye uygun filtreleri işaretleyin.")
        selected_filters = {
            "features": list(dict.fromkeys(features)),
            "refresh_rate": [],
            "resolution": [],
            "panel_type": [],
        }
        monitor_filters = {}

    selected_values = [
        value for values in selected_filters.values() for value in values
    ]
    feature_label = " · ".join(selected_values) if selected_values else ALL_FILTERS
    return (
        subcategory,
        category,
        subcategory,
        feature_label,
        monitor_filters,
        selected_filters,
    )


def _normalized_filter_text(value):
    normalized = unicodedata.normalize("NFKD", value.casefold())
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return normalized.replace("ı", "i")


def _negative_keyword_pattern():
    alternatives = []
    for keyword in NEGATIVE_KEYWORD_STEMS:
        normalized = _normalized_filter_text(keyword)
        words = re.escape(normalized).replace(r"\ ", r"\s+")
        if normalized == "stand":
            alternatives.append(r"\bstand(?:i|lari|leri|lar|ler|in|ina|inda)?\b")
            continue
        alternatives.append(rf"\b{words}\w*")
    return re.compile("|".join(alternatives), re.IGNORECASE)


NEGATIVE_KEYWORD_PATTERN = _negative_keyword_pattern()


def _feature_pattern(feature):
    normalized = _normalized_filter_text(feature)
    gpu_match = re.fullmatch(r"(rtx|gtx|rx)\s*(\d{3,4})", normalized)
    if gpu_match:
        family, model = gpu_match.groups()
        return re.compile(
            rf"(?<!\w){family}[\s-]*{model}(?!\w)",
            re.IGNORECASE,
        )
    if normalized == "oled":
        return re.compile(r"(?<!qd-)(?<!qd )\boled\b", re.IGNORECASE)
    escaped = re.escape(normalized).replace(r"\ ", r"\s+")
    escaped = escaped.replace(r"\-", r"[\s-]*")
    if normalized.endswith("+"):
        escaped = escaped[:-2] + r"(?:\+|plus)?"
    if normalized == "16gb ram":
        escaped = r"(?<!\w)16\s*gb(?:\s*ram)?(?!\w)"
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


def _catalog_features_by_dimension():
    features_by_dimension = {}
    for subcategories in CATEGORY_CATALOG.values():
        for options in subcategories.values():
            for value in options:
                features_by_dimension.setdefault(_feature_dimension(value), set()).add(value)
    for options in MONITOR_FILTERS.values():
        for value in options:
            if value != ALL_FILTERS:
                features_by_dimension.setdefault(_feature_dimension(value), set()).add(value)
    return features_by_dimension


def _feature_dimension(value):
    normalized = _normalized_filter_text(value)
    if re.search(r"\b(?:rtx|gtx|rx)\s*\d{3,4}\b", normalized):
        return "gpu"
    if re.search(r"\b\d+\s*gb\s*ram\b", normalized):
        return "ram"
    if re.search(r"\bddr\d\b", normalized):
        return "memory_generation"
    if re.search(r"\b\d+(?:tb|gb)\s*(?:nvme|ssd|hdd)\b", normalized):
        return "storage"
    if re.search(r"\b(?:oled|ips|va|tn|mini[\s-]*led)\b", normalized):
        return "panel"
    if re.search(r"\b\d{2,4}\s*hz\b", normalized):
        return "refresh_rate"
    if re.search(r"\b(?:720p|1080p|1440p|2160p|4k|8k|fhd|qhd|uhd)\b", normalized):
        return "resolution"
    if re.search(r"\b\d{2,3}\s*(?:in[cç]|inch|inç)\b", normalized):
        return "screen_size"
    if re.search(r"\b(?:5g|4g|lte|wifi\s*[67])\b", normalized):
        return "connectivity"
    return "features"


def _selected_filter_map(radar):
    filters = radar.get("selected_filters")
    if isinstance(filters, dict):
        return filters
    legacy_filters = radar.get("monitor_filters")
    if isinstance(legacy_filters, dict):
        return {
            key: ([] if value in (None, "", ALL_FILTERS) else [value])
            if isinstance(value, str)
            else value
            for key, value in legacy_filters.items()
        }
    legacy_feature = radar.get("feature")
    return {"features": [] if legacy_feature in (None, "", ALL_FILTERS) else [legacy_feature]}


def _matches_radar_filters(product, radar):
    title = _normalized_filter_text(str(product.get("title", "")))
    full_text = _normalized_filter_text(
        f"{product.get('title', '')} {product.get('description', '')}"
    )
    if NEGATIVE_KEYWORD_PATTERN.search(full_text):
        return False

    filters = _selected_filter_map(radar)

    features = filters.get("features", [])
    if isinstance(features, str):
        features = [features] if features not in ("", ALL_FILTERS) else []
    features_by_dimension = {}
    for value in features:
        features_by_dimension.setdefault(_feature_dimension(value), []).append(value)
    category_options = CATEGORY_CATALOG.get(radar.get("category"), {}).get(
        radar.get("subcategory"), ()
    )
    catalog_features = _catalog_features_by_dimension()
    for dimension, selected_values in features_by_dimension.items():
        if not any(_feature_pattern(value).search(title) for value in selected_values):
            return False
        if dimension == "gpu":
            selected_models = {
                re.sub(
                    r"[\s-]+",
                    "",
                    _normalized_filter_text(value),
                )
                for value in selected_values
            }
            detected_models = {
                re.sub(
                    r"[\s-]+",
                    "",
                    _normalized_filter_text(value),
                )
                for value in re.findall(
                    r"\b(?:rtx|gtx|rx)\s*\d{3,4}\b",
                    title,
                )
            }
            if detected_models - selected_models:
                return False
        if dimension == "ram":
            selected_capacities = set()
            for value in selected_values:
                match = re.search(r"\b(\d+)\s*gb\b", _normalized_filter_text(value))
                if match:
                    selected_capacities.add(int(match.group(1)))
            detected_capacities = {
                int(match)
                for match in re.findall(r"\b(\d+)\s*gb\s*(?:ram|ddr\d)\b", title)
            }
            if detected_capacities - selected_capacities:
                return False
        dimension_options = set(category_options)
        if dimension in {
            "gpu",
            "ram",
            "memory_generation",
            "storage",
            "panel",
            "refresh_rate",
            "resolution",
            "screen_size",
            "connectivity",
        }:
            dimension_options.update(catalog_features.get(dimension, ()))
        conflicting_values = [
            value for value in dimension_options
            if _feature_dimension(value) == dimension and value not in selected_values
        ]
        if any(_feature_pattern(value).search(title) for value in conflicting_values):
            return False

    refresh_rates = filters.get("refresh_rate", [])
    if isinstance(refresh_rates, str):
        refresh_rates = [] if refresh_rates == ALL_FILTERS else [refresh_rates]
    if refresh_rates:
        available_rates = [
            int(rate)
            for rate in re.findall(r"(?<!\d)(\d{2,4})\s*hz\b", title)
        ]
        def rate_is_selected(rate):
            return any(
                rate >= int(re.search(r"\d+", requested).group())
                if requested == "500Hz+"
                else rate == int(re.search(r"\d+", requested).group())
                for requested in refresh_rates
            )

        if not available_rates or not any(
            rate_is_selected(rate) for rate in available_rates
        ):
            return False
        if any(not rate_is_selected(rate) for rate in available_rates):
            return False

    resolution_patterns = {
        "HD (720p)": r"\b720p\b|\b1280\s*[x×]\s*720\b",
        "FHD (1080p)": r"\b1080p\b|\b1920\s*[x×]\s*1080\b|\bfhd\b|\bfull[\s-]?hd\b",
        "QHD / 2K (1440p)": r"\b1440p\b|\b2560\s*[x×]\s*1440\b|\bqhd\b|\b2k\b",
        "UW-QHD (Ultrawide 1440p)": r"\b3440\s*[x×]\s*1440\b|\bultra\s*wide\b|\bultrawide\b|\buw[\s-]?qhd\b",
        "UHD / 4K (2160p)": r"\b2160p\b|\b3840\s*[x×]\s*2160\b|\b4k\b|\buhd\b",
        "8K": r"\b8k\b|\b4320p\b|\b7680\s*[x×]\s*4320\b",
    }
    resolutions = filters.get("resolution", [])
    if isinstance(resolutions, str):
        resolutions = [] if resolutions == ALL_FILTERS else [resolutions]
    if resolutions:
        if not any(
            resolution in resolution_patterns
            and re.search(resolution_patterns[resolution], title)
            for resolution in resolutions
        ):
            return False
        conflicting_resolutions = [
            value for value in MONITOR_FILTERS["resolution"]
            if value != ALL_FILTERS and value not in resolutions
        ]
        if any(
            re.search(resolution_patterns[value], title)
            and not (
                value == "QHD / 2K (1440p)"
                and re.search(
                    r"\b3440\s*[x×]\s*1440\b|\bultra\s*wide\b|\bultrawide\b|\buw[\s-]?qhd\b",
                    title,
                )
            )
            for value in conflicting_resolutions
            if value in resolution_patterns
        ):
            return False

    panel_patterns = {
        "IPS": r"\bips\b",
        "Fast IPS": r"\bfast[\s-]?ips\b",
        "VA": r"\bva\b",
        "OLED": r"(?<!qd-)\boled\b",
        "QD-OLED": r"\bqd[\s-]?oled\b",
        "TN": r"\btn\b",
        "Mini-LED": r"\bmini[\s-]?led\b",
    }
    panel_types = filters.get("panel_type", [])
    if isinstance(panel_types, str):
        panel_types = [] if panel_types == ALL_FILTERS else [panel_types]
    if panel_types:
        if not any(
            panel in panel_patterns and re.search(panel_patterns[panel], title)
            for panel in panel_types
        ):
            return False
        conflicting_panels = [
            value for value in MONITOR_FILTERS["panel_type"]
            if value != ALL_FILTERS and value not in panel_types
        ]
        if any(
            re.search(panel_patterns[value], title)
            and not (
                value == "Fast IPS" and "IPS" in panel_types
                or value == "IPS" and "Fast IPS" in panel_types
            )
            for value in conflicting_panels
            if value in panel_patterns
        ):
            return False
    return True


def _matches_radar_fallback(product, radar):
    full_text = _normalized_filter_text(
        f"{product.get('title', '')} {product.get('description', '')}"
    )
    if NEGATIVE_KEYWORD_PATTERN.search(full_text):
        return False

    title = _normalized_filter_text(str(product.get("title", "")))
    keyword_sources = [radar.get("query"), radar.get("subcategory")]
    for values in _selected_filter_map(radar).values():
        if isinstance(values, str):
            keyword_sources.append(values)
        elif isinstance(values, (list, tuple, set)):
            keyword_sources.extend(values)

    terms = {
        term
        for value in keyword_sources
        if isinstance(value, str)
        for term in re.findall(r"\w+", _normalized_filter_text(value))
        if len(term) >= 3 and term not in {"tumunu", "sec"}
    }
    return bool(terms) and any(
        re.search(rf"(?<!\w){re.escape(term)}(?!\w)", title)
        for term in terms
    )


def _save_radar_status(radar_id, status, last_scan):
    radars = load_radars()
    for index, radar in enumerate(radars):
        if radar["id"] == radar_id:
            radars[index] = {
                **radar,
                "status": status,
                "last_scan": last_scan,
            }
            break
    _save_json_list(RADARS_FILE, radars, RADARS_LOCK)


def _scan_radar(radar):
    checked_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
    try:
        selected_stores = _selected_stores(radar)
    except ValueError as error:
        _save_radar_status(radar["id"], str(error), checked_at)
        print(f"⚠️ {error}", flush=True)
        return []
    if not selected_stores:
        message = "En az bir geçerli mağaza seçin."
        _save_radar_status(radar["id"], message, checked_at)
        print(f"⚠️ {message}", flush=True)
        return []
    _begin_scan_progress(radar, selected_stores)
    if len(selected_stores) > 1:
        print(
            f"\nÇoklu mağaza radarı: {radar['query']} "
            f"({len(selected_stores)} mağaza eş zamanlı)",
            flush=True,
        )
    else:
        print(
            f"\nAkıllı arama: {selected_stores[0]} / {radar['query']}",
            flush=True,
        )
    products, errors = _search_radar_stores(
        radar,
        selected_stores,
        on_store_complete=lambda store, store_products, store_error: (
            _record_scan_store_result(radar, store, store_products, store_error)
        ),
    )
    error = "Bazı mağazalar taranamadı: " + " ".join(errors) if errors else None
    if error and not products:
        _save_radar_status(radar["id"], error, checked_at)
        print(f"⚠️ {error}", flush=True)
        _finish_scan_progress(radar["id"])
        return []

    scanned_products = _products_from_selected_stores(products, selected_stores)
    budget = float(radar["max_price"])
    minimum_price = float(radar.get("min_price", 0))
    scanned_product_count = len(scanned_products)
    strict_products = [
        product for product in scanned_products
        if _matches_radar_filters(product, radar)
    ]
    used_filter_fallback = bool(scanned_product_count and not strict_products)
    products = strict_products
    if used_filter_fallback:
        products = [
            product for product in scanned_products
            if _matches_radar_fallback(product, radar)
        ]
    matching = [
        product
        for product in products
        if minimum_price <= product["price"] <= budget
    ]
    try:
        offers = load_offers()
        found_products = {product["url"]: product for product in products}
        failed_stores = {
            store for store in selected_stores
            if any(error.startswith(f"{store} ") for error in errors)
        }
        successful_stores = set(selected_stores) - failed_stores
        for index, offer in enumerate(offers):
            if offer.get("radar_id") == radar["id"] and offer.get("store") not in selected_stores:
                offers[index] = {**offer, "active": False, "shock_alert": None}
            elif offer.get("radar_id") == radar["id"] and offer.get("store") in successful_stores:
                if offer.get("url") not in found_products:
                    offers[index] = {**offer, "active": False, "shock_alert": None}

        new_offers = []
        for product in products:
            existing_index = next(
                (
                    index for index, offer in enumerate(offers)
                    if offer.get("radar_id") == radar["id"]
                    and offer.get("url") == product["url"]
                ),
                None,
            )
            previous_offer = offers[existing_index] if existing_index is not None else {}
            shock_alert = _detect_shock_drop(
                previous_offer,
                product["price"],
                radar,
                checked_at,
            )
            if shock_alert is None:
                previous_alert = previous_offer.get("shock_alert")
                if isinstance(previous_alert, dict):
                    alert_price = previous_alert.get("current_price")
                    if (
                        isinstance(alert_price, (int, float))
                        and math.isfinite(alert_price)
                        and product["price"] <= alert_price
                    ):
                        shock_alert = previous_alert
            offer = {
                **previous_offer,
                **product,
                "id": (
                    offers[existing_index]["id"]
                    if existing_index is not None
                    else str(uuid.uuid4())
                ),
                "radar_id": radar["id"],
                "query": radar["query"],
                "stores": radar.get("stores", selected_stores),
                "min_price": minimum_price,
                "max_price": budget,
                "category": radar.get("category"),
                "subcategory": radar.get("subcategory"),
                "feature": radar.get("feature"),
                "monitor_filters": radar.get("monitor_filters", {}),
                "selected_filters": radar.get("selected_filters", {}),
                "filter_fallback": used_filter_fallback,
                "price_history": _append_price_history(
                    previous_offer,
                    product["price"],
                    checked_at,
                ),
                "shock_alert": shock_alert,
                "active": minimum_price <= product["price"] <= budget,
                "last_seen": checked_at,
            }
            if not product.get("image_url"):
                offer["image_url"] = previous_offer.get("image_url")
            if existing_index is None:
                if offer["active"]:
                    new_offers.append(offer)
                offers.insert(0, offer)
            else:
                offers[existing_index] = offer
        offers.sort(key=lambda offer: (not offer.get("active", False), offer.get("price", math.inf)))
        _save_json_list(OFFERS_FILE, offers, OFFERS_LOCK)
        matching.sort(key=lambda product: product["price"])
        over_budget_products = [
            product for product in products if product["price"] > budget
        ]
        status = f"{len(matching)} fırsat bulundu."
        if used_filter_fallback and matching:
            status = (
                f"{len(matching)} fırsat bulundu (esnek özellik eşleşmesi; "
                "kesin filtrede eşleşme bulunmadı)."
            )
        if not matching:
            if used_filter_fallback and products:
                status = (
                    f"Kesin özellik eşleşmesi bulunamadı; {len(products)} ürün "
                    "aksesuar filtresi korunarak esnek eşleşmeyle gösteriliyor."
                )
            elif used_filter_fallback:
                status = (
                    "Kesin özellik eşleşmesi bulunamadı ve ürün başlığında "
                    "arama anahtar kelimeleriyle eşleşen sonuç yok."
                )
            elif scanned_product_count and not products:
                status = (
                    f"{scanned_product_count} ürün tarandı ancak seçilen teknik özellikler "
                    "kesin eşleşmedi; yanlış ürünleri göstermek için filtre gevşetilmedi."
                )
            elif products:
                status = (
                    f"{len(products)} ürün özellikleri karşıladı ancak belirlenen "
                    "fiyat aralığında eşleşme bulunamadı."
                )
            else:
                status = "Mağazalardan uygun fiyatlı ürün verisi alınamadı."
        if over_budget_products:
            nearest_product = min(over_budget_products, key=lambda product: product["price"])
            status += (
                f" Bütçenizin biraz üzerinde {len(over_budget_products)} ürün bulundu; "
                f"en yakın fiyat {format_price(nearest_product['price'])} TL."
            )
        below_minimum_products = [
            product for product in products if product["price"] < minimum_price
        ]
        if below_minimum_products:
            status += (
                f" {len(below_minimum_products)} ürün belirlenen minimum fiyatın altında."
            )
        if error:
            status = f"{status} {error}"
        _save_radar_status(radar["id"], status, checked_at)
        print(f"{radar['store']}: {status}", flush=True)
        _finish_scan_progress(radar["id"])
        return new_offers
    except RuntimeError as error:
        message = f"Fırsatlar kaydedilemedi: {error}"
        _save_radar_status(radar["id"], message, checked_at)
        print(f"❌ {message}", flush=True)
        _finish_scan_progress(radar["id"])
        return []


def scan_radars(radar_ids=None):
    if not RADAR_SCAN_LOCK.acquire(blocking=False):
        return None
    new_offers = []
    try:
        radars = load_radars()
        selected = [
            radar for radar in radars
            if radar_ids is None or radar["id"] in radar_ids
        ]
        for radar in selected:
            new_offers.extend(_scan_radar(radar))
    finally:
        RADAR_SCAN_LOCK.release()
    return new_offers


def _scan_added_radar(radar_id):
    while RADAR_SCAN_LOCK.locked():
        time.sleep(0.5)
    if any(radar["id"] == radar_id for radar in load_radars()):
        scan_radars([radar_id])


def _valid_product_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return False
    return (
        parsed.scheme in ("http", "https")
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
    )


@asynccontextmanager
async def lifespan(_application):
    load_products()
    load_radars()
    load_offers()
    threading.Thread(target=_run_all_checks, daemon=True).start()
    threading.Thread(target=_periodic_checks, daemon=True).start()
    yield


app = FastAPI(title="İndirim Radarı API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(HTTPException)
async def handle_http_exception(_request, error):
    detail = error.detail
    message = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
    return JSONResponse(
        status_code=error.status_code,
        content={"error": message},
        headers=error.headers,
    )


async def _read_api_json(request: Request):
    body = await request.body()
    if not body or len(body) > 65536:
        raise HTTPException(
            status_code=400,
            detail="İstek gövdesi boş veya izin verilen boyutu aşıyor.",
        )
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=400, detail=f"Geçersiz JSON: {error}") from error


@app.get("/health")
def health_check():
    return {"ok": True}


@app.get("/api/categories")
def get_categories():
    return {
        "categories": CATEGORY_CATALOG,
        "monitor_filters": MONITOR_FILTERS,
        "store_groups": STORE_GROUPS,
    }


@app.get("/api/history/{collection_name}/{entry_id}")
def get_price_history(collection_name: str, entry_id: str):
    if collection_name not in ("products", "offers"):
        raise HTTPException(status_code=404, detail="Ürün geçmişi bulunamadı.")
    try:
        collection = load_products() if collection_name == "products" else load_offers()
        entry = next((item for item in collection if item.get("id") == entry_id), None)
        if entry is None:
            raise HTTPException(status_code=404, detail="Ürün geçmişi bulunamadı.")
        history = entry.get("price_history", [])
        if not isinstance(history, list):
            history = []
        return {
            "id": entry_id,
            "title": entry.get("title") or entry.get("url") or "Ürün",
            "price_history": history,
        }
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.get("/api/products")
def get_products():
    try:
        return load_products()
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.get("/api/scan-progress")
def get_scan_progress():
    try:
        with SCAN_PROGRESS_LOCK:
            return {
                **SCAN_PROGRESS,
                "completed_stores": list(SCAN_PROGRESS["completed_stores"]),
                "pending_stores": list(SCAN_PROGRESS["pending_stores"]),
                "products": [dict(product) for product in SCAN_PROGRESS["products"]],
            }
    except Exception as error:
        message = str(error)
        print(f"❌ Tarama durumu alınamadı: {message}", flush=True)
        return JSONResponse(
            status_code=200,
            content={"status": "error", "message": message},
        )


@app.get("/api/radars")
def get_radars():
    try:
        return load_radars()
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.get("/api/offers")
def get_offers():
    try:
        return load_offers()
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.get("/api/search")
def search_offers(request: Request):
    query = request.query_params.get("q", "")
    try:
        min_price = (
            float(request.query_params["min_price"])
            if request.query_params.get("min_price")
            else None
        )
        max_price = (
            float(request.query_params["max_price"])
            if request.query_params.get("max_price")
            else None
        )
        stores = request.query_params.getlist("store")
        if any(store not in STORE_CONFIGS for store in stores):
            raise ValueError("Mağaza filtresi geçersiz.")
        if min_price is not None and (
            not math.isfinite(min_price) or min_price < 0
        ):
            raise ValueError("Minimum fiyat sıfır veya daha büyük olmalıdır.")
        if max_price is not None and (
            not math.isfinite(max_price) or max_price <= 0
        ):
            raise ValueError("Maksimum fiyat sıfırdan büyük olmalıdır.")
        if (
            min_price is not None
            and max_price is not None
            and min_price > max_price
        ):
            raise ValueError("Minimum fiyat maksimum fiyattan büyük olamaz.")
        return filter_saved_offers(query, min_price, max_price, stores)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except Exception as error:
        message = str(error)
        print(f"❌ Ürün araması başarısız: {message}", flush=True)
        return JSONResponse(
            status_code=200,
            content={"status": "error", "message": message},
        )


@app.get("/")
def get_index():
    return FileResponse(
        ROOT / "index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/index.html")
def get_index_file():
    return _serve_static_file("index.html", "text/html; charset=utf-8")


@app.get("/config.js")
def get_frontend_config():
    return _serve_static_file("config.js", "text/javascript; charset=utf-8")


@app.get("/sw.js")
def get_service_worker():
    return _serve_static_file("sw.js", "text/javascript; charset=utf-8")


def _serve_static_file(filename, media_type):
    try:
        body = (ROOT / filename).read_bytes()
    except OSError as error:
        raise HTTPException(
            status_code=500,
            detail=f"{filename} okunamadı: {error}",
        ) from error
    return Response(
        content=body,
        media_type=media_type,
        headers={"Cache-Control": "no-cache"},
    )


@app.post("/api/check")
def post_check():
    try:
        results = check_products()
        if results is None:
            raise HTTPException(status_code=409, detail="Fiyat kontrolü zaten sürüyor.")
        return {"products": results}
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.post("/api/scan", status_code=202)
def post_scan():
    if RADAR_SCAN_LOCK.locked():
        raise HTTPException(status_code=409, detail="Radar taraması zaten sürüyor.")
    threading.Thread(target=_run_all_checks, daemon=True).start()
    return {"message": "Ürün ve radar taraması başlatıldı."}


def _build_radar(payload, existing=None):
    if not isinstance(payload, dict):
        raise ValueError("Radar verisi geçersiz.")
    stores_payload = payload.get("stores")
    if stores_payload is None:
        store_value = payload.get("store")
        if store_value == ALL_STORES:
            selected_stores = list(STORE_CONFIGS)
        elif store_value in STORE_CONFIGS:
            selected_stores = [store_value]
        else:
            selected_stores = []
    else:
        if not isinstance(stores_payload, list) or any(
            not isinstance(store_name, str) for store_name in stores_payload
        ):
            raise ValueError("Mağaza seçimi geçersiz.")
        selected_stores = list(dict.fromkeys(stores_payload))
        if any(store_name not in STORE_CONFIGS for store_name in selected_stores):
            raise ValueError("Listeden geçerli mağazaları seçin.")
    if not selected_stores:
        raise ValueError("En az bir mağaza seçin.")
    store = (
        ALL_STORES
        if len(selected_stores) == len(STORE_CONFIGS)
        else selected_stores[0]
    )
    default_budget = existing.get("max_price", 0) if existing else 0
    default_minimum = existing.get("min_price", 0) if existing else 0
    budget = float(payload.get("max_price", default_budget))
    minimum_price = float(payload.get("min_price", default_minimum))
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("Maksimum bütçe sıfırdan büyük olmalıdır.")
    if (
        not math.isfinite(minimum_price)
        or minimum_price < 0
        or minimum_price > budget
    ):
        raise ValueError(
            "Minimum fiyat sıfır veya daha büyük ve maksimum fiyattan büyük olamaz."
        )
    default_depth = (
        existing.get("scan_depth", DEFAULT_SCAN_DEPTH)
        if existing
        else DEFAULT_SCAN_DEPTH
    )
    depth_value = payload.get(
        "scan_depth",
        default_depth,
    )
    scan_depth_number = float(depth_value)
    if (
        not math.isfinite(scan_depth_number)
        or not scan_depth_number.is_integer()
        or not 1 <= scan_depth_number <= MAX_SCAN_DEPTH
    ):
        raise ValueError(
            f"Tarama derinliği 1 ile {MAX_SCAN_DEPTH} arasında olmalıdır."
        )
    shock_mode = payload.get(
        "shock_mode", existing.get("shock_mode", "none") if existing else "none"
    )
    shock_threshold = float(
        payload.get(
            "shock_threshold",
            existing.get("shock_threshold", 0) if existing else 0,
        )
    )
    if shock_mode not in ("none", "amount", "percent"):
        raise ValueError("Geçerli bir şok indirim alarmı seçin.")
    if shock_mode == "none":
        shock_threshold = 0
    elif (
        not math.isfinite(shock_threshold)
        or shock_threshold <= 0
        or (shock_mode == "percent" and shock_threshold > 100)
    ):
        raise ValueError(
            "Şok alarm eşiği sıfırdan büyük olmalı; yüzde eşiği en fazla 100 olabilir."
        )
    if "category" in payload:
        (
            query,
            category,
            subcategory,
            feature,
            monitor_filters,
            selected_filters,
        ) = _radar_query_from_selection(payload)
    elif existing is not None and existing.get("category"):
        query = existing["query"]
        category = existing["category"]
        subcategory = existing["subcategory"]
        feature = existing.get("feature")
        monitor_filters = payload.get(
            "monitor_filters", existing.get("monitor_filters", {})
        )
        selected_filters = payload.get(
            "selected_filters", existing.get("selected_filters", {})
        )
    else:
        query = payload.get("query", existing.get("query") if existing else None)
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 120:
            raise ValueError("Arama kelimesi 2 ile 120 karakter arasında olmalıdır.")
        query = query.strip()
        category = subcategory = feature = None
        monitor_filters = {}
        selected_filters = {}
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    return {
        **(existing or {}),
        "id": existing["id"] if existing else str(uuid.uuid4()),
        "store": store,
        "stores": selected_stores,
        "query": query,
        "category": category,
        "subcategory": subcategory,
        "feature": feature,
        "monitor_filters": monitor_filters,
        "selected_filters": selected_filters,
        "min_price": minimum_price,
        "max_price": budget,
        "scan_depth": int(scan_depth_number),
        "shock_mode": shock_mode,
        "shock_threshold": shock_threshold,
        "created_at": (
            existing.get("created_at", timestamp) if existing else timestamp
        ),
        "last_scan": None,
        "status": "Radar güncellendi; arka plan taraması sıraya alındı."
        if existing
        else "Tarama kuyruğuna alındı.",
    }


def _save_and_schedule_radar(radar, *, insert=False):
    radars = load_radars()
    existing_index = next(
        (index for index, item in enumerate(radars) if item["id"] == radar["id"]),
        None,
    )
    if insert:
        radars.insert(0, radar)
    elif existing_index is None:
        raise HTTPException(status_code=404, detail="Radar bulunamadı.")
    else:
        radars[existing_index] = radar
    _save_json_list(RADARS_FILE, radars, RADARS_LOCK)
    threading.Thread(
        target=_scan_added_radar,
        args=(radar["id"],),
        daemon=True,
    ).start()
    return radar


@app.post("/api/radars", status_code=201)
async def post_radar(request: Request):
    payload = await _read_api_json(request)
    try:
        radar = _build_radar(payload)
        _save_and_schedule_radar(radar, insert=True)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return JSONResponse(status_code=201, content=radar)


@app.put("/api/radars/{radar_id}")
async def update_radar(radar_id: str, request: Request):
    payload = await _read_api_json(request)
    try:
        radars = load_radars()
        existing = next((item for item in radars if item["id"] == radar_id), None)
        if existing is None:
            raise HTTPException(status_code=404, detail="Radar bulunamadı.")
        radar = _build_radar(payload, existing)
        _save_and_schedule_radar(radar)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return radar


@app.post("/api/products", status_code=201)
async def post_product(request: Request):
    payload = await _read_api_json(request)
    try:
        url = payload.get("url") if isinstance(payload, dict) else None
        target = float(payload.get("target")) if isinstance(payload, dict) else 0
        if not _valid_product_url(url):
            raise ValueError("Ürün bağlantısı http:// veya https:// ile başlamalı.")
        if not math.isfinite(target) or target <= 0:
            raise ValueError("Hedef fiyat sıfırdan büyük bir sayı olmalıdır.")
        product = {
            "id": str(uuid.uuid4()),
            "url": url.strip(),
            "target": target,
            "current_price": None,
            "last_checked": None,
            "message": "Henüz fiyat kontrol edilmedi.",
            "price_history": [],
        }
        products = load_products()
        products.insert(0, product)
        save_products(products)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    threading.Thread(target=check_products, daemon=True).start()
    return JSONResponse(status_code=201, content=product)


@app.delete("/api/clear")
def delete_all_data():
    CHECK_LOCK.acquire()
    try:
        RADAR_SCAN_LOCK.acquire()
        try:
            save_products([])
            _save_json_list(RADARS_FILE, [], RADARS_LOCK)
            _save_json_list(OFFERS_FILE, [], OFFERS_LOCK)
        finally:
            RADAR_SCAN_LOCK.release()
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    finally:
        CHECK_LOCK.release()
    return {"ok": True}


@app.delete("/api/radars/{radar_id}")
def delete_radar(radar_id: str):
    try:
        RADAR_SCAN_LOCK.acquire()
        try:
            radars = load_radars()
            remaining = [radar for radar in radars if radar["id"] != radar_id]
            if len(remaining) == len(radars):
                raise HTTPException(status_code=404, detail="Radar bulunamadı.")
            _save_json_list(RADARS_FILE, remaining, RADARS_LOCK)
            offers = load_offers()
            remaining_offers = [
                offer for offer in offers if offer.get("radar_id") != radar_id
            ]
            _save_json_list(OFFERS_FILE, remaining_offers, OFFERS_LOCK)
        finally:
            RADAR_SCAN_LOCK.release()
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return {"ok": True}


@app.delete("/api/products/{product_id}")
def delete_product(product_id: str):
    try:
        CHECK_LOCK.acquire()
        try:
            products = load_products()
            remaining = [
                product for product in products if product["id"] != product_id
            ]
            if len(remaining) == len(products):
                raise HTTPException(
                    status_code=404,
                    detail="Ürün takip listesinde bulunamadı.",
                )
            save_products(remaining)
        finally:
            CHECK_LOCK.release()
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return {"ok": True}


def _run_all_checks():
    check_products()
    scan_radars()


def _periodic_checks():
    while True:
        time.sleep(CHECK_INTERVAL_SECONDS)
        _run_all_checks()


def main():
    address = f"http://{HOST}:{PORT}"
    print(f"Arayüz hazır: {address}", flush=True)
    print(f"İlk fiyat kontrolü arka planda başlatıldı. Ürün listesi: {PRODUCTS_FILE}", flush=True)
    print("Ürün eklemeleri products.json dosyasına kaydedilir. Durdurmak için Ctrl+C.", flush=True)
    if OPEN_BROWSER:
        webbrowser.open(address)
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
