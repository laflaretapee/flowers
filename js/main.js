// Инициализация сайта
document.addEventListener('DOMContentLoaded', function() {
  console.log("Сайт цветочного магазина запущен");
  
  initBurgerMenu();
  initSmoothScroll();
  
  // Загрузка каталога с API (будет подключено после создания Django бэкенда)
  // loadCatalog();
});

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

// Функция для загрузки каталога (будет реализована после создания API)
async function loadCatalog() {
  try {
    const response = await fetch('/api/catalog/');
    const products = await response.json();
    // Обновление DOM с продуктами
    console.log('Каталог загружен:', products);
    return products;
  } catch (error) {
    console.error('Ошибка загрузки каталога:', error);
    return [];
  }
}

// Функция для рендеринга продуктов в каталог
function renderProducts(products, containerSelector) {
  const container = document.querySelector(containerSelector);
  if (!container) return;
  
  container.innerHTML = products.map(product => `
    <article class="card product-card">
      <div class="product-photo">
        <img src="${product.image || 'https://via.placeholder.com/400x300'}" alt="${product.name}">
      </div>
      <h3>${product.name}</h3>
      <p class="product-desc">${product.description || ''}</p>
      <div class="product-meta">
        <span class="product-price">${product.price} ₽</span>
        <a href="https://t.me/your_bot" class="btn btn-small" target="_blank">Заказать</a>
      </div>
    </article>
  `).join('');
}
