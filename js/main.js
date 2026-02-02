// Конфигурация API
const API_BASE_URL = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
  ? 'http://127.0.0.1:8000/api'
  : '/api';

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

// Загрузка полного каталога
async function loadFullCatalog() {
  try {
    // Сначала загружаем общие настройки (футер, контакты)
    const settingsResponse = await fetch(`${API_BASE_URL}/site-content/`);
    if (settingsResponse.ok) {
      const data = await settingsResponse.json();
      renderSettings(data.settings);
    }

    // Загружаем товары
    const response = await fetch(`${API_BASE_URL}/products/`);
    if (!response.ok) throw new Error('Ошибка API');
    
    const data = await response.json();
    // DRF returns { count: ..., next: ..., previous: ..., results: [...] } for paginated responses
    // or just [...] if pagination is disabled. Default is PageNumberPagination.
    const products = data.results || data; 
    
    console.log('Полный каталог загружен:', products);
    renderProducts(products, '#full-catalog-grid');
    
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
  
  // Обновляем телефон в шапке
  const headerPhone = document.querySelector('.header-phone');
  if (headerPhone && settings.telegram_bot_link) {
    headerPhone.href = settings.telegram_bot_link;
    headerPhone.textContent = settings.phone;
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
      <li>${hero.benefit_1}</li>
      <li>${hero.benefit_2}</li>
      <li>${hero.benefit_3}</li>
    `;
  }
}

// Рендеринг промо баннера
function renderPromo(promo) {
  const promoBanner = document.querySelector('.promo-banner');
  if (!promoBanner) return;
  
  if (!promo || !promo.is_active) {
    promoBanner.style.display = 'none';
    return;
  }
  
  const promoContent = promoBanner.querySelector('.promo-content');
  if (promoContent) {
    promoContent.innerHTML = `
      <span class="promo-icon">${promo.icon}</span>
      <div>
        <strong>${promo.title}</strong>
        <span>${promo.text}</span>
      </div>
      <a href="${promo.button_link}" class="btn btn-primary btn-small" target="_blank">${promo.button_text}</a>
    `;
  }
}

// Рендеринг категорий
function renderCategories(categories) {
  const container = document.querySelector('#occasions .grid');
  if (!container || !categories || !categories.length) return;
  
  container.innerHTML = categories.map(cat => `
    <article class="card category-card">
      <img src="${cat.image || 'https://via.placeholder.com/400x300?text=' + encodeURIComponent(cat.name)}" alt="${cat.name}">
      <h3>${cat.name}</h3>
      <p>${cat.description || ''}</p>
    </article>
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
      <li>${delivery.benefit_1}</li>
      <li>${delivery.benefit_2}</li>
      <li>${delivery.benefit_3}</li>
    `;
  }
  
  const steps = deliverySection.querySelector('.steps');
  if (steps) {
    steps.innerHTML = `
      <li>${delivery.step_1}</li>
      <li>${delivery.step_2}</li>
      <li>${delivery.step_3}</li>
    `;
  }
}

// Рендеринг отзывов
function renderReviews(reviews) {
  const container = document.querySelector('.section-muted:last-of-type .grid');
  if (!container || !reviews || !reviews.length) return;
  
  container.innerHTML = reviews.map(review => `
    <article class="card review-card">
      <p>«${review.text}»</p>
      <span class="review-author">${review.name}</span>
    </article>
  `).join('');
}

// Функция для рендеринга продуктов в каталог
function renderProducts(products, containerSelector) {
  const container = document.querySelector(containerSelector);
  if (!container) return;
  
  container.innerHTML = products.map(product => {
    // Формируем HTML для цены
    let priceHtml = '';
    if (!product.hide_price) {
      priceHtml = `<span class="product-price">${product.price} ₽</span>`;
    }
    
    return `
    <article class="card product-card">
      <div class="product-photo">
        <img src="${product.image || 'https://via.placeholder.com/400x300'}" alt="${product.name}">
      </div>
      <h3>${product.name}</h3>
      <p class="product-desc">${product.description || ''}</p>
      <div class="product-meta">
        ${priceHtml}
        <a href="https://t.me/your_bot" class="btn btn-small" target="_blank">Заказать</a>
      </div>
    </article>
  `}).join('');
}
