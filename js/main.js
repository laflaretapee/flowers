// Конфигурация API
const API_BASE_URL = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
  ? 'http://127.0.0.1:8000/api'
  : '/api';

function getApiOrigin() {
  try {
    // Absolute API URL (dev mode)
    return new URL(API_BASE_URL).origin;
  } catch (e) {
    // Relative API URL (prod mode) - assume same origin
    return window.location.origin;
  }
}

function resolveMediaUrl(value) {
  if (!value) return '';
  const str = String(value);
  if (str.startsWith('/')) return `${getApiOrigin()}${str}`;
  return str;
}

// Global settings cache
let siteSettings = {
  telegram_bot_link: 'https://t.me/flowersraevka_bot'
};

let allCatalogProducts = [];

function buildBotOrderLink(baseLink, productId) {
  const fallback = `https://t.me/flowersraevka_bot?start=product_${productId}`;
  if (!baseLink) return fallback;

  try {
    const normalized = baseLink.startsWith('http') ? baseLink : `https://${baseLink}`;
    const url = new URL(normalized);
    if (!url.hostname.includes('t.me')) return fallback;
    url.searchParams.set('start', `product_${productId}`);
    return url.toString();
  } catch (error) {
    if (baseLink.includes('t.me/')) {
      const separator = baseLink.includes('?') ? '&' : '?';
      return `${baseLink}${separator}start=product_${productId}`;
    }
  }

  return fallback;
}

function buildBotCustomLink(baseLink) {
  const fallback = 'https://t.me/flowersraevka_bot?start=custom';
  if (!baseLink) return fallback;

  try {
    const normalized = baseLink.startsWith('http') ? baseLink : `https://${baseLink}`;
    const url = new URL(normalized);
    if (!url.hostname.includes('t.me')) return fallback;
    url.searchParams.set('start', 'custom');
    return url.toString();
  } catch (error) {
    if (baseLink.includes('t.me/')) {
      const separator = baseLink.includes('?') ? '&' : '?';
      return `${baseLink}${separator}start=custom`;
    }
  }

  return fallback;
}

function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value).replace(/[&<>"']/g, (char) => {
    const map = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    };
    return map[char] || char;
  });
}

// Инициализация сайта
document.addEventListener('DOMContentLoaded', function() {
  console.log("Сайт цветочного магазина запущен");
  
  initBurgerMenu();
  initSmoothScroll();
  
  // Определяем, на какой мы странице
  if (document.getElementById('full-catalog-grid')) {
    loadFullCatalog();
  } else {
    loadSiteContent();
  }
});

function parsePriceValue(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(String(value).replace(',', '.'));
  return Number.isFinite(parsed) ? parsed : null;
}

async function fetchAllProducts(url) {
  const collected = [];
  let nextUrl = url;

  while (nextUrl) {
    const response = await fetch(nextUrl);
    if (!response.ok) throw new Error('Ошибка API');
    const data = await response.json();

    if (Array.isArray(data)) {
      collected.push(...data);
      nextUrl = null;
      continue;
    }

    const results = Array.isArray(data.results) ? data.results : [];
    collected.push(...results);

    if (!data.next) {
      nextUrl = null;
      continue;
    }

    if (data.next.startsWith('http')) {
      nextUrl = data.next;
    } else {
      nextUrl = `${getApiOrigin()}${data.next}`;
    }
  }

  return collected;
}

function updateCatalogCount(count) {
  const counter = document.querySelector('[data-catalog-count]');
  if (!counter) return;
  counter.textContent = count ? `Найдено: ${count}` : 'Ничего не найдено';
}

function applyCatalogFilters() {
  const minInput = document.querySelector('#price-min');
  const maxInput = document.querySelector('#price-max');

  const minValue = minInput ? parsePriceValue(minInput.value) : null;
  const maxValue = maxInput ? parsePriceValue(maxInput.value) : null;

  const filtered = allCatalogProducts.filter((product) => {
    const hasPrice = !product.hide_price && product.price !== null && product.price !== undefined && product.price !== '';
    const priceValue = hasPrice ? parsePriceValue(product.price) : null;

    if (minValue !== null || maxValue !== null) {
      if (priceValue === null) return false;
      if (minValue !== null && priceValue < minValue) return false;
      if (maxValue !== null && priceValue > maxValue) return false;
    }
    return true;
  });

  if (!filtered.length) {
    const container = document.querySelector('#full-catalog-grid');
    if (container) {
      container.innerHTML = '<p class="empty-msg">По выбранным фильтрам ничего не найдено.</p>';
    }
  } else {
    renderProducts(filtered, '#full-catalog-grid');
  }
  updateCatalogCount(filtered.length);
}

function initCatalogFilters() {
  const filters = document.querySelector('.catalog-filters');
  if (!filters) return;

  const applyButton = filters.querySelector('.catalog-filter-apply');
  const resetButton = filters.querySelector('.catalog-filter-reset');
  const minInput = filters.querySelector('#price-min');
  const maxInput = filters.querySelector('#price-max');

  if (applyButton) {
    applyButton.addEventListener('click', applyCatalogFilters);
  }

  if (resetButton) {
    resetButton.addEventListener('click', () => {
      if (minInput) minInput.value = '';
      if (maxInput) maxInput.value = '';
      applyCatalogFilters();
    });
  }

  [minInput, maxInput].forEach((input) => {
    if (!input) return;
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        applyCatalogFilters();
      }
    });
  });
}

function initCustomBouquetSuggestion() {
  const container = document.getElementById('full-catalog-grid');
  if (!container) return;

  let scrollHits = 0;
  let lastScrollY = window.scrollY;
  let lastTrigger = 0;
  let shown = false;

  const showToast = () => {
    const toast = document.createElement('div');
    toast.className = 'custom-bouquet-toast';
    const link = buildBotCustomLink(siteSettings.telegram_bot_link);
    toast.innerHTML = `
      <div class="custom-bouquet-toast__content">
        <div class="custom-bouquet-toast__title">Хотите собрать свой букет?</div>
        <div class="custom-bouquet-toast__text">Расскажите нам пожелания — мы соберём уникальную композицию.</div>
        <a href="${escapeHtml(link)}" class="btn btn-small" target="_blank" rel="noopener noreferrer">Собрать букет</a>
      </div>
      <button class="custom-bouquet-toast__close" type="button" aria-label="Скрыть">×</button>
    `;
    document.body.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('is-visible'));

    const closeButton = toast.querySelector('.custom-bouquet-toast__close');
    if (closeButton) {
      closeButton.addEventListener('click', () => {
        toast.classList.remove('is-visible');
        setTimeout(() => toast.remove(), 200);
      });
    }
  };

  const onScroll = () => {
    if (shown) return;
    const currentY = window.scrollY;
    if (currentY - lastScrollY > 200) {
      const now = Date.now();
      if (now - lastTrigger > 400) {
        scrollHits += 1;
        lastTrigger = now;
      }
      lastScrollY = currentY;
    } else if (currentY < lastScrollY) {
      lastScrollY = currentY;
    }

    if (scrollHits >= 2) {
      shown = true;
      showToast();
      window.removeEventListener('scroll', onScroll);
    }
  };

  window.addEventListener('scroll', onScroll, { passive: true });
}

// Загрузка полного каталога
async function loadFullCatalog() {
  const params = new URLSearchParams(window.location.search);
  const categoryId = params.get('category');
  const productsUrl = categoryId
    ? `${API_BASE_URL}/products/?category=${encodeURIComponent(categoryId)}`
    : `${API_BASE_URL}/products/`;

  const settingsPromise = (async () => {
    try {
      const settingsResponse = await fetch(`${API_BASE_URL}/site-content/`);
      if (settingsResponse.ok) {
        const data = await settingsResponse.json();
        renderSettings(data.settings);
      }
    } catch (error) {
      console.log('Не удалось загрузить настройки сайта:', error);
    }
  })();

  try {
    const products = await fetchAllProducts(productsUrl);
    allCatalogProducts = products;

    console.log('Полный каталог загружен:', products);
    initCatalogFilters();
    applyCatalogFilters();
    initCustomBouquetSuggestion();
    await settingsPromise;
  } catch (error) {
    console.error('Ошибка загрузки каталога:', error);
    const container = document.querySelector('#full-catalog-grid');
    if (container) {
      container.innerHTML = '<p class="error-msg">Не удалось загрузить товары. Попробуйте позже.</p>';
    }
  }
}

// Бургер меню
function initBurgerMenu() {
  const burger = document.querySelector('.burger');
  const nav = document.querySelector('.nav');
  
  if (burger && nav) {
    burger.addEventListener('click', function() {
      nav.classList.toggle('nav-open');
      burger.classList.toggle('burger-open');
    });
    
    // Закрытие при клике на ссылку
    const navLinks = nav.querySelectorAll('a');
    navLinks.forEach(link => {
      link.addEventListener('click', function() {
        nav.classList.remove('nav-open');
        burger.classList.remove('burger-open');
      });
    });
  }
}

// Плавная прокрутка к якорям
function initSmoothScroll() {
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
      const href = this.getAttribute('href');
      if (href !== '#' && href.startsWith('#')) {
        e.preventDefault();
        const target = document.querySelector(href);
        if (target) {
          target.scrollIntoView({
            behavior: 'smooth',
            block: 'start'
          });
          // Закрываем меню после перехода
          const nav = document.querySelector('.nav');
          const burger = document.querySelector('.burger');
          if (nav && burger) {
            nav.classList.remove('nav-open');
            burger.classList.remove('burger-open');
          }
        }
      }
    });
  });
}

// Загрузка всего контента сайта
async function loadSiteContent() {
  try {
    const response = await fetch(`${API_BASE_URL}/site-content/`);
    if (!response.ok) {
      console.log('API недоступен, используем статический контент');
      return;
    }
    const data = await response.json();
    console.log('Контент загружен:', data);
    
    renderSettings(data.settings);
    renderHero(data.hero);
    renderPromo(data.promo);
    renderCategories(data.categories);
    renderProducts(data.products, '#catalog .grid');
    renderDelivery(data.delivery);
    renderReviews(data.reviews);
  } catch (error) {
    console.log('Используем статический контент:', error.message);
  }
}

// Рендеринг настроек сайта
function renderSettings(settings) {
  if (!settings) return;
  
  // Cache settings globally for use in other functions
  siteSettings = { ...siteSettings, ...settings };
  
  // Обновляем телефон в шапке
  const headerPhone = document.querySelector('.header-phone');
  if (headerPhone) {
    if (settings.phone) headerPhone.textContent = settings.phone;
    if (settings.telegram_bot_link) headerPhone.href = settings.telegram_bot_link;
  }
  
  // Обновляем футер
  const footerLogo = document.querySelector('.footer-logo-link');
  if (footerLogo) footerLogo.setAttribute('aria-label', settings.site_name);
  
  const footerPhone = document.querySelector('.footer-phone');
  if (footerPhone) footerPhone.textContent = settings.phone;
  
  const footerAddress = document.querySelector('.footer-address');
  if (footerAddress && settings.address) footerAddress.textContent = settings.address;
  
  // Обновляем соцсети
  const instagramLink = document.querySelector('.social-link.instagram');
  if (instagramLink) {
    if (settings.instagram_link) {
      instagramLink.href = settings.instagram_link;
      instagramLink.style.display = 'inline-flex';
    } else {
      instagramLink.style.display = 'none';
    }
  }

  const telegramLink = document.querySelector('.social-link.telegram');
  if (telegramLink) {
    if (settings.telegram_channel_link) {
      telegramLink.href = settings.telegram_channel_link;
      telegramLink.style.display = 'inline-flex';
    } else {
      telegramLink.style.display = 'none';
    }
  }

  const vkLink = document.querySelector('.social-link.vk');
  if (vkLink) {
    if (settings.vk_link) {
      vkLink.href = settings.vk_link;
      vkLink.style.display = 'inline-flex';
    } else {
      vkLink.style.display = 'none';
    }
  }
  
  const footerCopyright = document.querySelector('.copyright span:last-child');
  if (footerCopyright) footerCopyright.textContent = settings.footer_text;
}

// Рендеринг Hero секции
function renderHero(hero) {
  if (!hero) return;
  
  const heroLabel = document.querySelector('.hero-label');
  if (heroLabel) heroLabel.textContent = hero.label;
  
  const heroTitle = document.querySelector('.hero h1');
  if (heroTitle) heroTitle.textContent = hero.title;
  
  const heroSubtitle = document.querySelector('.hero-subtitle');
  if (heroSubtitle) heroSubtitle.textContent = hero.subtitle;
  
  const heroActions = document.querySelector('.hero-actions');
  if (heroActions) {
    const primaryBtn = heroActions.querySelector('.btn-primary');
    const ghostBtn = heroActions.querySelector('.btn-ghost');
    if (primaryBtn) {
      primaryBtn.textContent = hero.button_text;
      primaryBtn.href = hero.button_link;
    }
    if (ghostBtn) {
      ghostBtn.textContent = hero.secondary_button_text;
      if (hero.secondary_button_link) ghostBtn.href = hero.secondary_button_link;
    }
  }
  
  const heroBg = document.querySelector('.hero-bg');
  if (heroBg && hero.image) {
    heroBg.style.backgroundImage = `url('${hero.image}')`;
  }
  
  const heroBenefits = document.querySelector('.hero-benefits');
  if (heroBenefits) {
    heroBenefits.innerHTML = `
      <li>${escapeHtml(hero.benefit_1)}</li>
      <li>${escapeHtml(hero.benefit_2)}</li>
      <li>${escapeHtml(hero.benefit_3)}</li>
    `;
  }
}

// Рендеринг промо баннера
function renderPromo(promo) {
  const promoBanner = document.querySelector('.promo-banner');
  if (!promoBanner) return;
  
  if (!promo || !promo.is_active) {
    // Keep static fallback banner from HTML when promo is not configured in API.
    promoBanner.style.display = '';
    return;
  }
  promoBanner.style.display = '';
  
  const promoContent = promoBanner.querySelector('.promo-content');
  if (promoContent) {
    const icon = escapeHtml(promo.icon);
    const title = escapeHtml(promo.title);
    const text = escapeHtml(promo.text);
    const buttonText = escapeHtml(promo.button_text || 'Подробнее');
    const buttonLink = promo.button_link ? escapeHtml(promo.button_link) : '';
    const buttonHtml = buttonLink
      ? `<a href="${buttonLink}" class="btn btn-primary btn-small" target="_blank" rel="noopener noreferrer">${buttonText}</a>`
      : '';

    promoContent.innerHTML = `
      <span class="promo-icon">${icon}</span>
      <div>
        <strong>${title}</strong>
        <span>${text}</span>
      </div>
      ${buttonHtml}
    `;
  }
}

// Рендеринг категорий
function renderCategories(categories) {
  const container = document.querySelector('#occasions .grid');
  if (!container || !categories || !categories.length) return;
  
  container.innerHTML = categories.map((cat, index) => `
    <a class="card category-card category-link" href="catalog.html?category=${encodeURIComponent(cat.id)}" aria-label="Открыть категорию ${escapeHtml(cat.name)}">
      <img
        src="${cat.image ? escapeHtml(resolveMediaUrl(cat.image)) : 'https://via.placeholder.com/400x300?text=' + encodeURIComponent(cat.name || '')}"
        alt="${escapeHtml(cat.name)}"
        loading="${index < 2 ? 'eager' : 'lazy'}"
        decoding="async"
      >
      <h3>${escapeHtml(cat.name)}</h3>
      <p>${escapeHtml(cat.description || '')}</p>
    </a>
  `).join('');
}

// Рендеринг информации о доставке
function renderDelivery(delivery) {
  if (!delivery) return;
  
  const deliverySection = document.querySelector('#delivery');
  if (!deliverySection) return;
  
  const title = deliverySection.querySelector('.section-title');
  if (title) title.textContent = delivery.title;
  
  const subtitle = deliverySection.querySelector('.section-subtitle');
  if (subtitle) subtitle.textContent = delivery.subtitle;
  
  const list = deliverySection.querySelector('.list');
  if (list) {
    list.innerHTML = `
      <li>${escapeHtml(delivery.benefit_1)}</li>
      <li>${escapeHtml(delivery.benefit_2)}</li>
      <li>${escapeHtml(delivery.benefit_3)}</li>
    `;
  }
  
  const steps = deliverySection.querySelector('.steps');
  if (steps) {
    steps.innerHTML = `
      <li>${escapeHtml(delivery.step_1)}</li>
      <li>${escapeHtml(delivery.step_2)}</li>
      <li>${escapeHtml(delivery.step_3)}</li>
    `;
  }
}

// Рендеринг отзывов
function renderReviews(reviews) {
  const container = document.querySelector('.section-muted:last-of-type .grid');
  if (!container || !reviews || !reviews.length) return;

  const cardHtml = (review) => {
    const ratingValue = Math.max(0, Math.min(5, Number(review.rating || 0)));
    const stars = ratingValue ? `${'★'.repeat(ratingValue)}${'☆'.repeat(5 - ratingValue)}` : '';
    const author = escapeHtml(review.name || '');
    const initial = author ? author.trim().slice(0, 1).toUpperCase() : 'А';
    const avatar = review.avatar_url ? escapeHtml(review.avatar_url) : '';

    return `
      <article class="card review-card">
        <div class="review-head">
          ${avatar
            ? `<img class="review-avatar" src="${avatar}" alt="${author}">`
            : `<div class="review-avatar review-avatar-fallback" aria-hidden="true">${escapeHtml(initial)}</div>`
          }
          <div class="review-head-text">
            <div class="review-author-row">
              <span class="review-author">${author}</span>
              ${stars ? `<span class="review-rating" aria-label="Рейтинг ${ratingValue} из 5">${stars}</span>` : ''}
            </div>
            ${review.product_name ? `<div class="review-product">${escapeHtml(review.product_name)}</div>` : ''}
          </div>
        </div>
        <p class="review-body">${escapeHtml(review.text)}</p>
      </article>
    `;
  };

  const baseCards = reviews.map(cardHtml).join('');
  const duplicatedCards = reviews.length >= 3 ? (reviews.map(cardHtml).join('')) : '';

  container.innerHTML = `
    <div class="reviews-carousel" role="region" aria-label="Отзывы клиентов">
      <div class="reviews-track">
        ${baseCards}
        ${duplicatedCards}
      </div>
    </div>
  `;
}

// Функция для рендеринга продуктов в каталог
function renderProducts(products, containerSelector) {
  const container = document.querySelector(containerSelector);
  if (!container) return;
  
  container.innerHTML = products.map((product, index) => {
    // Формируем HTML для цены
    let priceHtml = '';
    if (!product.hide_price) {
      priceHtml = `<span class="product-price">${escapeHtml(product.price)} ₽</span>`;
    }

    const resolvedImageUrl = product.image ? escapeHtml(resolveMediaUrl(product.image)) : 'https://via.placeholder.com/400x300';
    const productName = escapeHtml(product.name);
    const productDesc = escapeHtml(product.short_description || product.description || '');
    const orderLink = escapeHtml(buildBotOrderLink(siteSettings.telegram_bot_link, product.id));
    
    return `
    <article class="card product-card">
      <div class="product-photo">
        <img
          src="${resolvedImageUrl}"
          alt="${productName}"
          loading="${index < 3 ? 'eager' : 'lazy'}"
          decoding="async"
          fetchpriority="${index === 0 ? 'high' : 'auto'}"
        >
      </div>
      <h3>${productName}</h3>
      <p class="product-desc">${productDesc}</p>
      <div class="product-meta">
        ${priceHtml}
        <a href="${orderLink}" class="btn btn-small" target="_blank" rel="noopener noreferrer">Заказать</a>
      </div>
    </article>
  `}).join('');
}
