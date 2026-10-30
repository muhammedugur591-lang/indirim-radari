from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import (
    BrowserContext,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent
PRODUCTS_FILE = ROOT / "urunler_amazon.json"
REQUEST_DELAY_SECONDS = 1.5
BRANDS = {
    "apple": "Apple",
    "samsung": "Samsung",
    "xiaomi": "Xiaomi",
    "redmi": "Redmi",
    "poco": "POCO",
    "google": "Google",
    "huawei": "Huawei",
    "honor": "Honor",
    "oneplus": "OnePlus",
    "oppo": "OPPO",
    "vivo": "Vivo",
    "motorola": "Motorola",
    "nokia": "Nokia",
    "realme": "realme",
    "sony": "Sony",
    "asus": "ASUS",
    "tecno": "TECNO",
    "infinix": "Infinix",
    "nothing": "Nothing",
}


class ProductPageError(RuntimeError):
    pass


class AccessChallengeError(ProductPageError):
    pass


def product_url(product: dict) -> str:
    link = product.get("link")
    if isinstance(link, str) and link.strip():
        return link.strip()

    stores = product.get("stores")
    if isinstance(stores, list) and stores and isinstance(stores[0], dict):
        url = stores[0].get("product_url")
        if isinstance(url, str) and url.strip():
            return url.strip()

    raise ProductPageError("Ürün bağlantısı 'link' veya 'stores[0].product_url' alanında yok.")


def visible_brand_match(value: str) -> str | None:
    lowered = value.casefold()
    for brand, canonical in BRANDS.items():
        if re.search(rf"(?<!\w){re.escape(brand)}(?!\w)", lowered):
            return canonical
    return None


def extract_brand(byline: str, title: str) -> str:
    brand = visible_brand_match(byline)
    if brand:
        return brand

    cleaned_byline = re.sub(
        r"^\s*(?:visit the|marka\s*:?)\s*|\s+(?:store|mağazası)\s*$",
        "",
        byline,
        flags=re.IGNORECASE,
    ).strip()
    if cleaned_byline:
        return cleaned_byline

    brand = visible_brand_match(title)
    if brand:
        return brand

    first_word = title.strip().split(maxsplit=1)
    if first_word:
        return first_word[0].strip(" ,;:-")
    raise ProductPageError("Marka bilgisi sayfada veya ürün başlığında bulunamadı.")


def parse_price(whole: str, fraction: str) -> float:
    whole_digits = re.sub(r"\D", "", whole)
    fraction_digits = re.sub(r"\D", "", fraction)
    if not whole_digits:
        raise ProductPageError(f"Fiyatın tam sayı kısmı okunamadı: {whole!r}")

    decimal_part = (fraction_digits or "0")[:2].rjust(2, "0")
    return float(f"{int(whole_digits)}.{decimal_part}")


def parse_display_price(value: str) -> float:
    normalized = re.sub(r"\s+", "", value).replace("\u00a0", "")
    match = re.search(r"(?<!\d)(\d[\d.]*)(?:,(\d{1,2}))?(?!\d)", normalized)
    if not match:
        raise ProductPageError(f"Görüntülenen fiyat okunamadı: {value!r}")

    whole = match.group(1).replace(".", "")
    fraction = (match.group(2) or "0").rjust(2, "0")
    amount = float(f"{int(whole)}.{fraction}")
    if amount <= 0:
        raise ProductPageError(f"Görüntülenen fiyat geçersiz: {value!r}")
    return amount


def read_price_from_source(source: Locator) -> float:
    whole = source.locator(".a-price-whole").first
    fraction = source.locator(".a-price-fraction").first
    if whole.count():
        return parse_price(
            whole.inner_text().strip(),
            fraction.inner_text().strip() if fraction.count() else "",
        )

    offscreen = source.locator(".a-offscreen").first
    display_text = offscreen.inner_text().strip() if offscreen.count() else ""
    if not display_text:
        hidden_text = source.locator("[aria-hidden='true']").first
        display_text = (
            hidden_text.inner_text().strip()
            if hidden_text.count()
            else source.inner_text().strip()
        )
    return parse_display_price(display_text)


def read_price(page: Page) -> float:
    selectors = (
        "#apex_price .a-price",
        "#corePriceDisplay_desktop_feature_div .a-price",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#price_inside_buybox",
    )
    for selector in selectors:
        source = page.locator(selector).first
        if not source.count() or not source.is_visible():
            continue
        return read_price_from_source(source)

    raise ProductPageError("Görünür güncel fiyat .a-price-whole/.a-price-fraction alanlarında bulunamadı.")


def read_offer_price(page: Page) -> float:
    offer = page.locator("#aod-offer").first
    try:
        offer.wait_for(state="visible", timeout=15_000)
    except PlaywrightTimeoutError as exc:
        raise ProductPageError("Amazon teklif listesinde görünür bir satıcı teklifi yok.") from exc

    price = offer.locator(".a-price").first
    if not price.count():
        raise ProductPageError("Amazon teklif listesinde fiyat alanı bulunamadı.")
    return read_price_from_source(price)


def check_for_access_challenge(page: Page) -> None:
    title = page.title().casefold()
    if page.locator("#captchacharacters").count() or "robot check" in title:
        raise AccessChallengeError("Amazon CAPTCHA/robot kontrolü gösterdi; otomasyon durduruldu.")

    body = page.locator("body").inner_text(timeout=5000).casefold()
    if "enter the characters you see below" in body or "type the characters you see" in body:
        raise AccessChallengeError("Amazon CAPTCHA/robot kontrolü gösterdi; otomasyon durduruldu.")


def scrape_product(page: Page, url: str, title: str) -> tuple[float, str, str]:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except PlaywrightTimeoutError as exc:
        raise ProductPageError(f"Ürün sayfası zaman aşımına uğradı: {url}") from exc

    check_for_access_challenge(page)
    try:
        page.locator("#productTitle:visible").first.wait_for(
            state="visible",
            timeout=30_000,
        )
    except PlaywrightTimeoutError as exc:
        check_for_access_challenge(page)
        raise ProductPageError(f"Ürün başlığı yüklenmedi: {url}") from exc

    check_for_access_challenge(page)
    image = page.locator("#landingImage").first
    image.wait_for(state="visible", timeout=15_000)
    image_url = (
        image.get_attribute("src")
        or image.get_attribute("data-old-hires")
        or image.get_attribute("data-a-dynamic-image")
    )
    if not image_url or image_url.startswith("data:"):
        image_url = image.get_attribute("data-old-hires")
    if not image_url or image_url.startswith("data:"):
        raise ProductPageError("Ürün ana görselinin URL'si #landingImage üzerinde bulunamadı.")

    byline = page.locator("#bylineInfo").first
    byline_text = byline.inner_text().strip() if byline.count() else ""
    brand = extract_brand(byline_text, title)

    try:
        price = read_price(page)
    except ProductPageError as primary_error:
        offer_link = page.locator("#buybox a[href*='offer-listing']").first
        if not offer_link.count():
            raise primary_error
        href = offer_link.get_attribute("href")
        if not href:
            raise primary_error
        offer_url = urljoin(url, href)
        page.goto(offer_url, wait_until="domcontentloaded", timeout=60_000)
        check_for_access_challenge(page)
        price = read_offer_price(page)

    return price, image_url, brand


def write_products(products: list[dict]) -> None:
    temporary_file = PRODUCTS_FILE.with_suffix(".json.tmp")
    temporary_file.write_text(
        json.dumps(products, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_file.replace(PRODUCTS_FILE)


def updated_product(product: dict, url: str, price: float, image: str, brand: str) -> dict:
    return {
        "title": product["title"],
        "category": "Telefon",
        "brand": brand,
        "main_image": image,
        "price": price,
        "stores": [
            {
                "store_name": "Amazon",
                "price": price,
                "product_url": url,
            }
        ],
    }


def commit_and_push() -> None:
    commands = (
        ["git", "add", "urunler_amazon.json"],
        ["git", "commit", "-m", "Amazon verileri güncellendi"],
        ["git", "push"],
    )
    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    products = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8-sig"))
    if not isinstance(products, list):
        raise ProductPageError("urunler_amazon.json kök değeri bir JSON dizisi olmalı.")

    failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context: BrowserContext = browser.new_context(locale="tr-TR")
        page = context.new_page()
        try:
            for index, product in enumerate(products):
                if not isinstance(product, dict) or not isinstance(product.get("title"), str):
                    failures.append(f"{index + 1}. kayıt: geçerli ürün başlığı yok.")
                    continue

                fallback_record = {"title": product["title"]}
                try:
                    url = product_url(product)
                    fallback_record["link"] = url
                    price, image, brand = scrape_product(page, url, product["title"])
                    products[index] = updated_product(product, url, price, image, brand)
                    write_products(products)
                    print(
                        f"[{index + 1}/{len(products)}] {product['title']} | {price:.2f} TL",
                        flush=True,
                    )
                except AccessChallengeError:
                    raise
                except Exception as exc:
                    products[index] = fallback_record
                    write_products(products)
                    failures.append(f"{index + 1}. kayıt ({product.get('title', 'başlıksız')}): {exc}")
                    print(
                        f"[{index + 1}/{len(products)}] {product.get('title', 'başlıksız')} | HATA: {exc}",
                        flush=True,
                    )

                if index + 1 < len(products):
                    time.sleep(REQUEST_DELAY_SECONDS)
        finally:
            context.close()
            browser.close()

    if failures:
        summary = "\n".join(failures)
        raise ProductPageError(
            f"{len(failures)} ürün güncellenemedi; eksik verilerle commit/push yapılmadı:\n{summary}"
        )

    commit_and_push()
    print(f"{len(products)} ürün güncellendi ve GitHub'a gönderildi.", flush=True)


if __name__ == "__main__":
    main()
