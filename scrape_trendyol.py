from __future__ import annotations

import argparse
import json
import logging
import math
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent
SOURCE_FILE = ROOT / "urunler.json"
CHECKPOINT_FILE = ROOT / ".trendyol_scrape_progress.json"
LOGGER = logging.getLogger("trendyol_scraper")

EXTRACT_PAGE_JS = r"""
(sourceUrl) => {
  const products = [];
  const visit = (value) => {
    if (!value || typeof value !== "object") return;
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    const type = value["@type"];
    if (type === "Product" || (Array.isArray(type) && type.includes("Product"))) {
      products.push(value);
    }
    Object.values(value).forEach(visit);
  };
  for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
    try { visit(JSON.parse(script.textContent)); } catch {}
  }
  const schema = products[0];
  const product = window["__envoy_product-image-gallery__PROPS"]?.product;
  const winner = product?.merchantListing?.winnerVariant;
  const schemaOffer = Array.isArray(schema?.offers) ? schema.offers[0] : schema?.offers;
  const selectedVariant = product?.variants?.find((variant) => variant.isSelected);
  const amount = winner?.price?.discountedPrice?.value
    ?? winner?.price?.sellingPrice?.value
    ?? schemaOffer?.price
    ?? schemaOffer?.lowPrice
    ?? selectedVariant?.price?.value;
  const currency = winner?.price?.currency ?? schemaOffer?.priceCurrency ?? "TRY";
  const title = product?.name ?? schema?.name ?? document.querySelector("h1")?.innerText;
  const brand = product?.brand?.name
    ?? (typeof schema?.brand === "string" ? schema.brand : schema?.brand?.name)
    ?? product?.webBrand?.name
    ?? schema?.manufacturer?.name
    ?? schema?.manufacturer
    ?? "Belirtilmemiş";
  const description = product?.description
    ?? schema?.description
    ?? document.querySelector('meta[name="description"]')?.content
    ?? "";
  const sourceCategory = product?.category?.name ?? schema?.category ?? "Monitör";
  const specifications = `${title ?? ""} ${description}`;
  const tags = [];
  if (/oyuncu|gaming|gamer/i.test(specifications)) tags.push("Oyuncu");
  if (/\b4k\b|uhd|3840\s*[x×]\s*2160/i.test(specifications)) tags.push("4K");
  const refreshRates = [...specifications.matchAll(/(\d{2,3})\s*hz/ig)]
    .map((match) => Number(match[1]))
    .filter((rate) => rate > 0);
  if (refreshRates.length) tags.push(`${Math.max(...refreshRates)}Hz`);
  if (/ips/i.test(specifications)) tags.push("IPS");
  if (/va panel/i.test(specifications)) tags.push("VA");
  const category = tags.includes("Oyuncu")
    ? "Oyuncu Monitörü"
    : tags.includes("4K")
      ? "4K Monitör"
      : refreshRates.some((rate) => rate >= 144)
        ? `${Math.max(...refreshRates)}Hz Monitör`
        : sourceCategory;

  const images = [];
  const collectImages = (value) => {
    if (typeof value === "string") {
      if (/^https:\/\//.test(value) && /\.(jpe?g|png|webp|avif)(\?|$)/i.test(value)) {
        images.push(value);
      }
    } else if (Array.isArray(value)) {
      value.forEach(collectImages);
    } else if (value && typeof value === "object") {
      Object.values(value).forEach(collectImages);
    }
  };
  collectImages(product?.images ?? schema?.image);
  const gallery = [...new Set(images)].filter((url) => !url.includes("/mnresize/"));
  if (!gallery.length) {
    for (const image of document.images) {
      const url = image.currentSrc || image.src;
      if (
        image.naturalWidth >= 500
        && /^https:\/\//.test(url)
        && /\.(jpe?g|png|webp|avif)(\?|$)/i.test(url)
        && !url.includes("/mnresize/")
      ) gallery.push(url);
    }
  }

  return {
    url: sourceUrl,
    title,
    brand,
    price: Number(amount),
    old_price: Number(winner?.price?.sellingPrice?.value ?? amount),
    currency,
    category,
    tags,
    main_image: gallery[0] ?? "",
    image_gallery: [...new Set(gallery)],
    description,
  };
}
"""


def _atomic_json_write(path: Path, payload: object) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _source_urls(source: list[dict]) -> list[str]:
    urls = []
    seen = set()
    for product in source:
        for offer in product.get("stores", []):
            url = str(offer.get("product_url") or "").strip()
            parsed = urlparse(url)
            if (
                parsed.scheme == "https"
                and parsed.hostname == "www.trendyol.com"
                and url not in seen
            ):
                urls.append(url)
                seen.add(url)
    if not urls:
        raise RuntimeError("urunler.json içinde Trendyol ürün bağlantısı bulunamadı")
    return urls


def _product_id(url: str) -> int:
    matches = re.findall(r"-p-(\d+)(?=[?/#]|$)", url)
    if not matches:
        raise ValueError(f"Trendyol ürün numarası bulunamadı: {url}")
    return int(matches[-1])


def _valid_image(url: object) -> bool:
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    path = urlparse(url).path.lower()
    return (
        path.endswith((".jpg", ".jpeg", ".png", ".webp", ".avif"))
        and "/mnresize/" not in path
        and "search_" not in path
    )


def _load_results(path: Path) -> tuple[dict[str, dict], list[dict]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Tarama sonucu okunamadı: {path}") from error
    results = payload.get("results")
    failures = payload.get("failures", [])
    if not isinstance(results, dict) or not isinstance(failures, list):
        raise RuntimeError("Tarama sonucu beklenen results/failures alanlarını içermiyor")
    return results, failures


def _crawl(
    urls: list[str], checkpoint_path: Path, delay_seconds: float
) -> tuple[dict[str, dict], list[dict]]:
    checkpoint = {"results": {}, "failures": []}
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    results = checkpoint.get("results", {})
    failures = checkpoint.get("failures", [])
    failed_urls = {entry.get("url") for entry in failures}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(locale="tr-TR")
        try:
            for index, url in enumerate(urls, start=1):
                if url in results or url in failed_urls:
                    continue
                try:
                    response = page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=45_000,
                    )
                except PlaywrightError as error:
                    page_text = ""
                    try:
                        page_text = page.locator("body").inner_text(timeout=2_000)
                    except PlaywrightError:
                        pass
                    if "403" in page_text or "429" in page_text:
                        raise RuntimeError(
                            f"Trendyol erişimi reddetti; CAPTCHA veya blok aşılmayacak: {url}"
                        ) from error
                    failures.append({"url": url, "error": str(error)[:300]})
                    LOGGER.warning("Could not open %s: %s", url, error)
                else:
                    status = response.status if response else 0
                    if status in {403, 429}:
                        raise RuntimeError(
                            f"Trendyol HTTP {status} döndürdü; tarama güvenli şekilde durduruldu"
                        )
                    if status == 404:
                        failures.append({"url": url, "error": "HTTP 404"})
                    elif status >= 400 or not response:
                        failures.append(
                            {"url": url, "error": f"HTTP {status or 'no response'}"}
                        )
                    else:
                        try:
                            item = page.evaluate(EXTRACT_PAGE_JS, url)
                            if (
                                not item["title"]
                                or not item["price"]
                                or item["currency"] != "TRY"
                                or not item["main_image"]
                            ):
                                raise ValueError("sayfada fiyat/görsel/TL verisi eksik")
                            results[url] = item
                        except (PlaywrightError, KeyError, TypeError, ValueError) as error:
                            failures.append({"url": url, "error": str(error)[:300]})

                if index % 10 == 0:
                    _atomic_json_write(
                        checkpoint_path,
                        {"results": results, "failures": failures},
                    )
                    LOGGER.info(
                        "%s/%s links processed; %s valid products, %s unavailable",
                        index,
                        len(urls),
                        len(results),
                        len(failures),
                    )
                if delay_seconds:
                    time.sleep(delay_seconds)
        finally:
            browser.close()

    _atomic_json_write(checkpoint_path, {"results": results, "failures": failures})
    return results, failures


def _build_catalog(
    source: list[dict], urls: list[str], results: dict[str, dict], failures: list[dict]
) -> list[dict]:
    failure_by_url = {entry.get("url"): entry for entry in failures}
    missing = [url for url in urls if url not in results and url not in failure_by_url]
    if missing:
        raise RuntimeError(
            f"{len(missing)} ürün bağlantısı taranmadı; JSON ve veritabanı değiştirilmedi"
        )
    if failures:
        non_404 = [
            entry for entry in failures if entry.get("error") != "HTTP 404"
        ]
        if non_404:
            raise RuntimeError(
                f"{len(non_404)} sayfa tam okunamadı; bloklar aşılmadan JSON güncellenmedi"
            )

    source_by_url = {}
    for product in source:
        for offer in product.get("stores", []):
            url = str(offer.get("product_url") or "").strip()
            source_by_url.setdefault(url, product)

    grouped: dict[int, dict] = {}
    unavailable: dict[int, dict] = {}
    for url in urls:
        source_item = source_by_url[url]
        if url in failure_by_url:
            product_id = _product_id(url)
            unavailable.setdefault(
                product_id,
                {
                    "id": product_id,
                    "title": str(source_item.get("title") or url),
                    "category": str(source_item.get("category") or "Monitör"),
                    "brand": str(source_item.get("brand") or "Belirtilmemiş"),
                    "main_image": "",
                    "image_gallery": [],
                    "description": "Trendyol ürün sayfası HTTP 404 döndürdü.",
                    "tags": [],
                    "source_url": url,
                    "scrape_status": "unavailable",
                    "stores": [
                        {
                            "store_name": "Trendyol",
                            "price": None,
                            "currency": "TRY",
                            "product_url": url,
                        }
                    ],
                },
            )
            continue

        data = results[url]
        try:
            price = float(data["price"])
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError(f"Trendyol fiyatı geçersiz: {url}") from error
        if (
            not str(data.get("title") or "").strip()
            or not math.isfinite(price)
            or price <= 0
            or str(data.get("currency") or "").upper() != "TRY"
            or not _valid_image(data.get("main_image"))
        ):
            raise RuntimeError(
                f"Gerçek TL fiyatı veya özgün ürün görseli alınamadı: {url}"
            )
        product_id = _product_id(url)
        product = grouped.get(product_id)
        if product is None:
            category_name = str(data["category"]).strip() or "Monitör"
            if category_name.casefold() == "oyuncu monitör":
                category_name = "Oyuncu Monitörü"
            product = {
                "id": product_id,
                "title": str(data["title"]).strip(),
                "category": category_name,
                "brand": str(data["brand"]).strip() or "Belirtilmemiş",
                "main_image": data["main_image"],
                "image_gallery": data.get("image_gallery", []),
                "description": str(data.get("description") or "").strip(),
                "tags": data.get("tags", []),
                "source_url": url,
                "stores": [],
            }
            grouped[product_id] = product
        product["stores"].append(
            {
                "store_name": "Trendyol",
                "price": price,
                "old_price": float(data.get("old_price") or data["price"]),
                "currency": "TRY",
                "product_url": url,
            }
        )

    for product_id, product in unavailable.items():
        if product_id not in grouped:
            grouped[product_id] = product
    return list(grouped.values())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trendyol ürün sayfalarını Chromium ile tarayıp SQLite kataloğunu güncelle."
    )
    parser.add_argument("--input", type=Path, default=SOURCE_FILE)
    parser.add_argument("--checkpoint", type=Path, default=CHECKPOINT_FILE)
    parser.add_argument("--results-file", type=Path)
    parser.add_argument("--delay", type=float, default=0.75)
    parser.add_argument("--allow-unavailable", action="store_true")
    parser.add_argument("--no-import", action="store_true")
    args = parser.parse_args()
    if args.delay < 0:
        parser.error("--delay sıfırdan küçük olamaz")

    try:
        source = json.loads(args.input.read_text(encoding="utf-8-sig"))
        if not isinstance(source, list):
            raise RuntimeError("urunler.json bir ürün listesi olmalıdır")
        urls = _source_urls(source)
        if args.results_file:
            results, failures = _load_results(args.results_file)
        else:
            results, failures = _crawl(urls, args.checkpoint, args.delay)
        if failures and not args.allow_unavailable:
            raise RuntimeError(
                f"{len(failures)} ürün sayfası kullanılamıyor. "
                "Yalnızca 404 sayfaları atlamak için --allow-unavailable kullanın."
            )

        catalog = _build_catalog(source, urls, results, failures)
        _atomic_json_write(args.input, catalog)
        if not args.no_import:
            from main import initialize_database

            initialize_database()
        available = sum(
            item.get("scrape_status") != "unavailable" for item in catalog
        )
        print(
            f"Tamamlandı: {available} ürün, "
            f"{len(catalog) - available} erişilemeyen ürün; "
            f"{len(urls)} benzersiz bağlantı işlendi."
        )
    except (OSError, json.JSONDecodeError, RuntimeError, ValueError) as error:
        LOGGER.error("%s", error)
        raise SystemExit(1) from error


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
