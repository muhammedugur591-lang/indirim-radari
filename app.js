const app = document.querySelector("#app");
const searchForm = document.querySelector("#search-form");
const searchInput = document.querySelector("#search-input");
const currencyFormats = new Map([
  ["USD", new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" })],
  ["TRY", new Intl.NumberFormat("tr-TR", { style: "currency", currency: "TRY" })],
]);
const stores = ["Amazon Türkiye", "Hepsiburada", "Trendyol"];
let activeChart;
let refreshTimer;

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[character]);
const price = (value, currency = "USD") => {
  const formatter = currencyFormats.get(currency)
    || new Intl.NumberFormat("en-US", { style: "currency", currency });
  return formatter.format(Number(value));
};

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `İstek başarısız (${response.status})`);
  }
  return response.json();
}

function showError(error) {
  app.innerHTML = `<section class="notice error-notice"><strong>Bir sorun oluştu.</strong><p>${escapeHtml(error.message)}</p><button class="button button-muted" onclick="window.location.reload()">Tekrar dene</button></section>`;
}

function productCard(product) {
  const discount = Math.max(0, Math.round((1 - product.current_lowest_price / product.old_price) * 100));
  return `<article class="product-card">
    <a class="product-image" href="/urun/${product.id}" data-link>
      <img src="${escapeHtml(product.image_url)}" alt="${escapeHtml(product.title)}" data-title="${escapeHtml(product.title)}" loading="lazy">
      ${discount > 0 ? `<span class="discount-badge">%${discount} indirim</span>` : ""}
    </a>
    <div class="product-info">
      <span class="product-brand">${escapeHtml(product.brand)} · ${escapeHtml(product.category_name || "")}</span>
      <a class="product-title" href="/urun/${product.id}" data-link>${escapeHtml(product.title)}</a>
      <div class="product-price-row"><strong>${price(product.current_lowest_price, product.price_currency)}</strong><del>${price(product.old_price, product.price_currency)}</del></div>
      <small class="price-source-note">Tahmini demo fiyat · ${escapeHtml(product.price_currency)}</small>
      <div class="product-card-bottom"><span>En düşük tahmin · ${escapeHtml(product.lowest_store_name)}</span><a href="${escapeHtml(product.lowest_store_url)}" target="_blank" rel="noopener noreferrer" aria-label="${escapeHtml(product.lowest_store_name)} mağaza arama sonuçları">Mağazada ara ↗</a></div>
    </div>
  </article>`;
}

function productGrid(products, emptyMessage = "Bu kriterlere uygun ürün bulunamadı.") {
  return products.length
    ? `<div class="product-grid">${products.map(productCard).join("")}</div>`
    : `<div class="empty-state"><span>⌕</span><h3>Sonuç bulunamadı</h3><p>${escapeHtml(emptyMessage)}</p></div>`;
}

async function renderHome() {
  const data = await api("/api/home");
  app.innerHTML = `
    <section class="hero">
      <div class="hero-copy"><span class="eyebrow"><span class="live-dot"></span> Akıllı alışverişin adresi</span>
        <h1>Aradığın ürün,<br><em>en iyi fiyatıyla.</em></h1>
        <p>Teknoloji ürünlerini mağazalar arasında karşılaştır, bütçene uygun fırsatları keşfet.</p>
        <a class="button button-primary" href="/tum-kategoriler" data-link>Kataloğu keşfet <span>→</span></a>
      </div>
      <div class="hero-art" aria-hidden="true"><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="hero-device">⌕</div><span class="float-tag tag-one">↓ Fiyat alarmı</span><span class="float-tag tag-two">$ Demo teklif</span></div>
    </section>
    <section class="category-strip">
      <div class="section-heading"><div><span class="eyebrow">Keşfet</span><h2>Popüler kategoriler</h2></div><a href="/tum-kategoriler" data-link class="text-link">Tüm kategoriler <span>→</span></a></div>
      <div class="category-quick-grid">${data.categories.slice(0, 8).map((category) => `<a class="category-chip" href="/kategori/${encodeURIComponent(category.id)}" data-link><span>${escapeHtml(category.icon)}</span><strong>${escapeHtml(category.name)}</strong></a>`).join("")}</div>
    </section>
    <section class="catalog-section">
      <div class="section-heading"><div><span class="eyebrow">Bugünün fırsatları</span><h2>Günün şok indirimleri</h2></div><span class="section-note">En yüksek indirim oranına göre</span></div>
      ${productGrid(data.deals)}
    </section>
    <section class="catalog-section">
      <div class="section-heading"><div><span class="eyebrow">Senin için seçtik</span><h2>Popüler ürünler</h2></div><a href="/tum-kategoriler" data-link class="text-link">Kataloğun tamamı <span>→</span></a></div>
      ${productGrid(data.popular)}
    </section>
    <p class="demo-disclaimer">${escapeHtml(data.catalog_note)} Kaynak: ${escapeHtml(data.catalog_source)}. Mağaza bağlantıları ürün aramasıdır.</p>`;
}

async function renderCategories() {
  const categories = await api("/api/categories");
  app.innerHTML = `<div class="page-heading"><span class="eyebrow">Alışveriş rehberi</span><h1>Tüm kategoriler</h1><p>İhtiyacına uygun kategoriyi seç, ürünleri ve mağaza fiyatlarını karşılaştır.</p></div>
    <div class="category-grid">${categories.map((category) => `<a class="category-tile" href="/kategori/${encodeURIComponent(category.id)}" data-link>
      <span class="category-illustration">${escapeHtml(category.icon)}</span><span class="category-tile-copy"><strong>${escapeHtml(category.name)}</strong><small>${category.product_count} ürün</small></span><span class="category-arrow">↗</span>
    </a>`).join("")}</div>`;
}

function readFiltersFromUrl() {
  const params = new URLSearchParams(location.search);
  return {
    q: params.get("q") || "",
    category: location.pathname.startsWith("/kategori/") ? decodeURIComponent(location.pathname.slice("/kategori/".length)) : "",
    min: params.get("min_price") || "",
    max: params.get("max_price") || "",
    brand: params.get("brand") || "",
    store: params.get("store") || "",
    sort: params.get("sort") || "relevance",
  };
}

async function renderListing() {
  const filters = readFiltersFromUrl();
  if (filters.q) searchInput.value = filters.q;
  const params = new URLSearchParams({ limit: "200", sort: filters.sort });
  if (filters.q) params.set("q", filters.q);
  if (filters.category) params.set("category", filters.category);
  if (filters.min) params.set("min_price", filters.min);
  if (filters.max) params.set("max_price", filters.max);
  if (filters.brand) params.set("brand", filters.brand);
  if (filters.store) params.set("store", filters.store);

  const [result, categories, brands] = await Promise.all([
    api(`/api/products?${params}`),
    api("/api/categories"),
    api(`/api/brands${filters.category ? `?category=${encodeURIComponent(filters.category)}` : ""}`),
  ]);
  const category = categories.find((item) => item.id === filters.category);
  const title = filters.q ? `“${filters.q}” arama sonuçları` : category?.name || "Tüm ürünler";
  const currentMax = filters.max || "10000";
  app.innerHTML = `<div class="listing-heading"><div><span class="eyebrow">${filters.q ? "Arama sonuçları" : "Ürün kataloğu"}</span><h1>${escapeHtml(title)}</h1><p>${result.total} ürün listeleniyor</p></div>
    <label class="sort-control">Sırala <select id="sort-select"><option value="relevance" ${filters.sort === "relevance" ? "selected" : ""}>Önerilen</option><option value="price_asc" ${filters.sort === "price_asc" ? "selected" : ""}>En düşük fiyat</option><option value="price_desc" ${filters.sort === "price_desc" ? "selected" : ""}>En yüksek fiyat</option><option value="discount" ${filters.sort === "discount" ? "selected" : ""}>En yüksek indirim</option></select></label>
  </div>
  <div class="listing-layout">
    <aside class="filter-panel">
      <div class="filter-heading"><h2>Filtreler</h2><button id="clear-filters" class="clear-button" type="button">Temizle</button></div>
      <fieldset><legend>Bütçe aralığı (USD)</legend><div class="range-values"><label><span>Min. fiyat</span><input id="min-price" type="number" min="0" step="10" placeholder="0" value="${escapeHtml(filters.min)}"></label><label><span>Maks. fiyat</span><input id="max-price" type="number" min="0" step="10" placeholder="10.000" value="${escapeHtml(filters.max)}"></label></div>
        <input id="budget-range" class="budget-range" type="range" min="0" max="10000" step="10" value="${escapeHtml(currentMax)}" aria-label="Maksimum fiyat">
        <div class="range-caption"><span>$0</span><span id="range-label">${price(Number(currentMax))}</span></div>
      </fieldset>
      <fieldset><legend>Demo mağaza tahminleri</legend><div class="check-list">${stores.map((store) => `<label class="check-option"><input type="radio" name="store-filter" value="${escapeHtml(store)}" ${filters.store === store ? "checked" : ""}><span>${escapeHtml(store)}</span></label>`).join("")}<label class="check-option"><input type="radio" name="store-filter" value="" ${!filters.store ? "checked" : ""}><span>Tüm mağazalar</span></label></div></fieldset>
      <fieldset><legend>Marka</legend><select id="brand-select" class="filter-select"><option value="">Tüm markalar</option>${brands.map((brand) => `<option value="${escapeHtml(brand)}" ${filters.brand === brand ? "selected" : ""}>${escapeHtml(brand)}</option>`).join("")}</select></fieldset>
      <div class="demo-filter-note">DummyJSON fiyatları USD demo verisidir. Mağaza fiyatları tahmindir; gerçek satıcı verisi değildir.</div>
    </aside>
    <section class="listing-results" aria-label="Ürün sonuçları"><div class="result-summary"><span><strong>${result.total}</strong> ürün</span><span>USD · tahmini demo fiyatlar</span></div>${productGrid(result.items)}</section>
  </div>`;
  bindListingFilters();
}

function bindListingFilters() {
  const apply = () => {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => {
      const filters = readFiltersFromUrl();
      const params = new URLSearchParams();
      const min = document.querySelector("#min-price").value;
      const max = document.querySelector("#max-price").value;
      const brand = document.querySelector("#brand-select").value;
      const store = document.querySelector('input[name="store-filter"]:checked')?.value || "";
      const sort = document.querySelector("#sort-select").value;
      if (filters.q) params.set("q", filters.q);
      if (min) params.set("min_price", min);
      if (max && max !== "10000") params.set("max_price", max);
      if (brand) params.set("brand", brand);
      if (store) params.set("store", store);
      if (sort !== "relevance") params.set("sort", sort);
      const query = params.toString();
      const path = filters.category ? `/kategori/${encodeURIComponent(filters.category)}` : "/arama";
      history.replaceState({}, "", query ? `${path}?${query}` : path);
      renderListing().catch(showError);
    }, 40);
  };
  document.querySelector("#min-price").addEventListener("change", apply);
  document.querySelector("#max-price").addEventListener("change", apply);
  document.querySelector("#brand-select").addEventListener("change", apply);
  document.querySelector("#sort-select").addEventListener("change", apply);
  document.querySelectorAll('input[name="store-filter"]').forEach((input) => input.addEventListener("change", apply));
  document.querySelector("#budget-range").addEventListener("input", (event) => {
    const maxInput = document.querySelector("#max-price");
    maxInput.value = event.target.value === "10000" ? "" : event.target.value;
    document.querySelector("#range-label").textContent = price(Number(event.target.value));
    apply();
  });
  document.querySelector("#clear-filters").addEventListener("click", () => {
    const path = readFiltersFromUrl().category
      ? `/kategori/${encodeURIComponent(readFiltersFromUrl().category)}`
      : "/arama";
    history.pushState({}, "", path);
    renderListing().catch(showError);
  });
}

async function renderProduct(productId) {
  const product = await api(`/api/products/${encodeURIComponent(productId)}`);
  const initialHistory = await api(`/api/products/${encodeURIComponent(productId)}/history?period=6m`);
  app.innerHTML = `  <div class="breadcrumbs"><a href="/" data-link>Ana sayfa</a><span>/</span><a href="/kategori/${encodeURIComponent(product.category)}" data-link>${escapeHtml(product.category_name)}</a><span>/</span><span>Ürün detayı</span></div>
    <section class="product-detail">
      <div class="detail-image-wrap"><img class="detail-image" src="${escapeHtml(product.image_url)}" alt="${escapeHtml(product.title)}" data-title="${escapeHtml(product.title)}"></div>
      <div class="detail-copy"><span class="eyebrow">${escapeHtml(product.brand)} · Ürün fiyat karşılaştırması</span><h1>${escapeHtml(product.title)}</h1><p class="detail-description">${escapeHtml(product.description)}</p>
        <div class="detail-best-price"><span>En düşük tahmini demo fiyat</span><strong>${price(product.current_lowest_price, product.price_currency)}</strong><small>${escapeHtml(product.lowest_store_name)} tahmini · ${escapeHtml(product.price_currency)}</small></div>
        <p class="detail-source-note">Katalog fiyatı DummyJSON demo API’sinden alınır. Mağaza fiyatları ve fiyat geçmişi simüle edilmiştir.</p>
        <a class="button button-primary" href="#offers">Satıcı fiyatlarını karşılaştır <span>↓</span></a>
      </div>
    </section>
    <section class="price-analysis">
      <div class="section-heading"><div><span class="eyebrow">Fiyat takibi</span><h2>Fiyat analizi</h2></div><div class="period-tabs" role="group" aria-label="Grafik dönemi">
        <button data-period="1w">1 Hafta</button><button data-period="1m">1 Ay</button><button data-period="6m" class="active">6 Ay</button><button data-period="1y">1 Yıl</button>
      </div></div>
      <div class="chart-panel"><div class="analysis-stats"><div><span>Dönem içi en yüksek (demo)</span><strong id="history-high">${price(initialHistory.highest, product.price_currency)}</strong></div><div><span>Dönem içi en düşük (demo)</span><strong id="history-low">${price(initialHistory.lowest, product.price_currency)}</strong></div><div><span>Şu anki tahmin</span><strong id="history-current">${price(product.current_lowest_price, product.price_currency)}</strong></div></div>
        <div class="chart-container"><canvas id="price-chart" aria-label="Ürün fiyat geçmişi grafiği"></canvas></div>
        <div class="price-insight">✦ <strong id="price-insight-label">Son 6 ayın en düşük simüle fiyatı</strong><span>Canlı mağaza fiyat geçmişi değildir.</span></div>
      </div>
    </section>
    <section id="offers" class="offers-section"><div class="section-heading"><div><span class="eyebrow">Tahmini demo mağaza teklifleri</span><h2>Satıcı fiyat tahminleri</h2></div><span class="section-note">${product.offers.length} tahmin listeleniyor</span></div>
      <div class="offers-list">${product.offers.map((offer, index) => `<article class="offer-row"><span class="offer-rank">${index + 1}</span><div class="offer-store"><div class="store-logo">${escapeHtml(offer.store_name)} · Tahmin</div><a class="offer-url" href="${escapeHtml(offer.product_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(offer.product_url)}</a></div><strong class="offer-price">${price(offer.price, product.price_currency)}</strong><a class="button button-store" href="${escapeHtml(offer.product_url)}" target="_blank" rel="noopener noreferrer">Mağazada ara ↗</a></article>`).join("")}</div>
      <p class="demo-disclaimer">Bu tahmini fiyatlar DummyJSON fiyatından türetilmiştir; Amazon, Hepsiburada veya Trendyol tarafından sağlanmaz. Butonlar mağaza arama sayfasını açar, doğrudan ürün sayfası değildir.</p>
    </section>`;
  drawPriceChart(initialHistory.points);
  document.querySelectorAll("[data-period]").forEach((button) => button.addEventListener("click", async () => {
    document.querySelectorAll("[data-period]").forEach((tab) => tab.classList.toggle("active", tab === button));
    try {
      const historyData = await api(`/api/products/${encodeURIComponent(productId)}/history?period=${button.dataset.period}`);
      document.querySelector("#history-high").textContent = price(historyData.highest, historyData.currency);
      document.querySelector("#history-low").textContent = price(historyData.lowest, historyData.currency);
      document.querySelector("#history-current").textContent = price(product.current_lowest_price, product.price_currency);
      const periodName = { "1w": "1 haftanın", "1m": "1 ayın", "6m": "6 ayın", "1y": "1 yılın" }[button.dataset.period];
      document.querySelector("#price-insight-label").textContent = `Son ${periodName} en düşük simüle fiyatı`;
      drawPriceChart(historyData.points);
    } catch (error) {
      showError(error);
    }
  }));
}

function drawPriceChart(points) {
  const canvas = document.querySelector("#price-chart");
  if (!canvas || typeof Chart === "undefined") return;
  if (activeChart) activeChart.destroy();
  const context = canvas.getContext("2d");
  const gradient = context.createLinearGradient(0, 0, 0, 280);
  gradient.addColorStop(0, "rgba(239, 68, 68, .25)");
  gradient.addColorStop(1, "rgba(239, 68, 68, 0)");
  activeChart = new Chart(context, {
    type: "line",
    data: {
      labels: points.map((point) => new Date(`${point.recorded_on}T12:00:00`).toLocaleDateString("tr-TR", { day: "numeric", month: "short" })),
      datasets: [{
        data: points.map((point) => point.price),
        borderColor: "#f05252",
        backgroundColor: gradient,
        fill: true,
        pointRadius: points.length > 40 ? 0 : 2,
        pointHoverRadius: 5,
        borderWidth: 2,
        tension: 0.32,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (context) => price(context.parsed.y) } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: "#858b98", maxTicksLimit: 7 } },
        y: { grid: { color: "rgba(255,255,255,.06)" }, ticks: { color: "#858b98", callback: (value) => price(value) } },
      },
    },
  });
}

document.addEventListener("error", (event) => {
  const image = event.target;
  if (!(image instanceof HTMLImageElement) || !image.dataset.title || image.dataset.fallbackApplied) return;
  image.dataset.fallbackApplied = "true";
  const title = escapeHtml(image.dataset.title.slice(0, 40));
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="480" viewBox="0 0 800 480"><rect width="800" height="480" fill="#20232b"/><circle cx="400" cy="205" r="92" fill="#292c35"/><path d="M354 205h92m-46-46v92" stroke="#ef6468" stroke-width="12" stroke-linecap="round"/><text x="400" y="365" fill="#d8dae0" font-family="Arial,sans-serif" font-size="24" text-anchor="middle">${title}</text></svg>`;
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}, true);

async function renderRoute() {
  const path = location.pathname;
  if (path === "/tum-kategoriler") return renderCategories();
  const productMatch = path.match(/^\/urun\/(\d+)\/?$/);
  if (productMatch) return renderProduct(productMatch[1]);
  if (path.startsWith("/kategori/") || path === "/arama") return renderListing();
  return renderHome();
}

function navigate(href) {
  const target = new URL(href, location.origin);
  if (target.origin !== location.origin) return;
  history.pushState({}, "", `${target.pathname}${target.search}${target.hash}`);
  window.scrollTo({ top: 0, behavior: "instant" });
  renderRoute().catch(showError);
}

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-link]");
  if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  navigate(link.href);
});
window.addEventListener("popstate", () => renderRoute().catch(showError));
searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = searchInput.value.trim();
  if (query) navigate(`/arama?q=${encodeURIComponent(query)}`);
});
document.querySelector("#current-year").textContent = new Date().getFullYear();
renderRoute().catch(showError);
