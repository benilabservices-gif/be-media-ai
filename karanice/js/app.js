/* ═══════════════════════════════════════════════════════
   KARA NICE — Main Application Script
   ═══════════════════════════════════════════════════════ */

// ── Product Catalog ──
const PRODUCTS = [
    {
        id: 'dawa-soap',
        name: 'Dawa Soap',
        tag: 'Phare',
        desc: 'Notre savon antiseptique emblématique. Doux pour les enfants, efficace contre les imperfections adultess. Action purifiante et réparatrice.',
        price: 3500,
        category: 'savon',
        emoji: '🧼',
        img: 'images/product-dawa.webp',
        badge: 'Bestseller'
    },
    {
        id: 'savon-noir',
        name: 'Savon Noir Artisanal',
        tag: 'Tradition',
        desc: 'Savon noir traditionnel béninois, riche en huile de palme brute. Purifiant, adoucissant et parfaite pour le bain rituel afro-caribéen.',
        price: 2500,
        category: 'savon',
        emoji: '🫧',
        img: 'images/product-savon-noir.webp'
    },
    {
        id: 'karite-pur',
        name: 'Beurre de Karité Pur',
        tag: 'Hydratation',
        desc: 'Beurre de karité 100% pur, non raffiné. Hydratation intense pour la peau et les cheveux. Multi-usages : visage, corps, cheveux.',
        price: 5000,
        category: 'soin',
        emoji: '🧈',
        img: 'images/product-karite.webp'
    },
    {
        id: 'lavande',
        name: 'Savon Lavande Apaisant',
        tag: 'Détente',
        desc: 'Savon artisanal à lhuile essentielle de lavande. Apaise les peaux sensibles et iritées. Parfait pour un moment de douceur quotidienne.',
        price: 3000,
        category: 'savon',
        emoji: '💜',
        img: 'images/product-lavande.webp'
    },
    {
        id: 'miel',
        name: 'Savon Miel & lait',
        tag: 'Nourrissant',
        desc: 'Alliance royale de miel pur et lait de chèvre. Nutritif, émollient, laisse la peau douce et parfumée.',
        price: 3000,
        category: 'savon',
        emoji: '🍯',
        img: 'images/product-miel.webp'
    },
    {
        id: 'huile-multi',
        name: 'Huile Multi-Soins',
        tag: 'Huile',
        desc: 'Mélange dhuiles végétales (avocat, amande douce, jojoba) pour le corps, les cheveux et le massage. 100% naturel.',
        price: 4500,
        category: 'huile',
        emoji: '🫒',
        img: 'images/product-huile.webp'
    },
    {
        id: 'coffret-dawa',
        name: 'Coffret Dawa Family',
        tag: 'Coffret',
        desc: '3 pains de Dawa Soap + 1 beurre de karité 50g. Le coffret idéal pour toute la famille. Idéal en cadeau.',
        price: 14000,
        category: 'coffret',
        emoji: '🎁',
        img: 'images/product-coffret-dawa.webp',
        badge: 'Coffret'
    },
    {
        id: 'shampoing',
        name: 'Shampooing Naturel',
        tag: 'Cheveux',
        desc: 'Shampooing artisanal sans SLS, enrichi en beurre de karité et huile de coco. Nettoyage doux pour cheveux crépus.',
        price: 4000,
        category: 'soin',
        emoji: '🧴',
        img: 'images/product-shampoing.webp'
    }
];

// ── Cart State ──
let cart = JSON.parse(localStorage.getItem('karanice_cart') || '[]');
let selectedPayment = 'cod';

// ── Init ──
document.addEventListener('DOMContentLoaded', () => {
    renderCart();
    updateCartCount();
    initRevealObserver();
    initScrollNav();
    init3DSoap();
    initMarquee();
    initParallax();

    // Render home products if on homepage
    if (document.getElementById('productsGrid')) {
        renderHomeProducts();
    }

    // Render shop grid if on boutique page
    if (document.getElementById('shopGrid')) {
        renderShopGrid('all');
    }
});

// ── Reveal Observer ──
function initRevealObserver() {
    if (!('IntersectionObserver' in window)) return;
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(e => {
            if (e.isIntersecting) {
                e.target.classList.add('active');
            }
        });
    }, { threshold: 0.1, rootMargin: '0px 0px -30px 0px' });

    document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
}

// ── Scroll Nav ──
function initScrollNav() {
    const nav = document.getElementById('mainNav');
    if (!nav) return;
    window.addEventListener('scroll', () => {
        nav.classList.toggle('scrolled', window.pageYOffset > 60);
    });
}

// ── 3D Soap Interaction ──
function init3DSoap() {
    const scene = document.getElementById('soapScene');
    if (!scene) return;

    scene.addEventListener('mousemove', (e) => {
        const rect = scene.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width - 0.5;
        const y = (e.clientY - rect.top) / rect.height - 0.5;

        const mainSoap = document.getElementById('mainSoap');
        if (mainSoap) {
            mainSoap.style.animationPlayState = 'paused';
            mainSoap.style.transform = `translate(-50%, -50%) rotateX(${-y * 25}deg) rotateY(${x * 25}deg)`;
        }
    });

    scene.addEventListener('mouseleave', () => {
        const mainSoap = document.getElementById('mainSoap');
        if (mainSoap) {
            mainSoap.style.animationPlayState = 'running';
            mainSoap.style.transform = '';
        }
    });
}

// ── Marquee Clone ──
function initMarquee() {
    const inner = document.getElementById('marqueeInner');
    if (!inner) return;
    // Clone items for seamless loop
    inner.innerHTML += inner.innerHTML;
}

// ── Parallax on scroll ──
function initParallax() {
    const visuals = document.querySelectorAll('.kn-about-visual, .kn-hero-visual');
    if (!visuals.length) return;

    let ticking = false;
    window.addEventListener('scroll', () => {
        if (!ticking) {
            window.requestAnimationFrame(() => {
                visuals.forEach(el => {
                    const rect = el.getBoundingClientRect();
                    const speed = 0.03;
                    const yPos = rect.top * speed;
                    el.style.transform = `translateY(${yPos}px)`;
                });
                ticking = false;
            });
            ticking = true;
        }
    });
}

// ── Navigation ──
function showPage(pageId) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const page = document.getElementById('page-' + pageId);
    if (page) page.classList.add('active');
    window.scrollTo({ top: 0 });
}

function scrollToSection(id) {
    const target = document.getElementById(id);
    if (!target) return;
    const navHeight = document.getElementById('mainNav')?.offsetHeight || 0;
    const top = Math.max(0, target.getBoundingClientRect().top + window.scrollY - navHeight - 16);
    window.scrollTo({ top, behavior: 'smooth' });
}

// Handle anchor links
document.addEventListener('click', (e) => {
    const link = e.target.closest('a[data-scroll]');
    if (!link) return;
    e.preventDefault();
    const id = link.getAttribute('href').slice(1);
    scrollToSection(id);
});

function toggleMobileMenu() {
    const menu = document.getElementById('mobileMenu');
    if (menu) menu.classList.toggle('open');
}

// ── Home Products ──
function renderHomeProducts() {
    const grid = document.getElementById('productsGrid');
    if (!grid) return;
    const featured = PRODUCTS.filter(p => p.badge || ['dawa-soap', 'savon-noir', 'karite-pur'].includes(p.id)).slice(0, 4);
    grid.innerHTML = featured.map((p, i) => createProductCard(p, i)).join('');
    initRevealObserver();
}

// ── Shop Grid ──
function renderShopGrid(category) {
    const grid = document.getElementById('shopGrid');
    if (!grid) return;

    // Update active button
    document.querySelectorAll('.kn-cat-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.cat === category);
    });

    const filtered = category === 'all'
        ? PRODUCTS
        : PRODUCTS.filter(p => p.category === category);

    grid.innerHTML = filtered.map((p, i) => createProductCard(p, i)).join('');
    initRevealObserver();
}

function filterProducts(cat) {
    renderShopGrid(cat);
}

// ── Product Card HTML ──
function createProductCard(product, index) {
    const badge = product.badge
        ? `<div style="position:absolute;top:1rem;left:1rem;background:var(--accent);color:var(--text-inverse);font-size:11px;font-weight:700;padding:0.25rem 0.75rem;border-radius:var(--radius-full);letter-spacing:0.05em;text-transform:uppercase;z-index:2;">${product.badge}</div>`
        : '';
    const imgHTML = product.img
        ? `<div class="kn-product-img kn-img-shimmer" style="height:220px;position:relative;overflow:hidden;">
               <img src="${product.img}" alt="${product.name}" loading="lazy"
                    onerror="this.style.display='none'; this.parentElement.classList.add('kn-product-img-placeholder'); this.parentElement.innerHTML='<span style=font-size:4rem>${product.emoji}</span>';">
           </div>`
        : `<div class="kn-product-img-placeholder" style="height:220px;position:relative;overflow:hidden;display:flex;align-items:center;justify-content:center;font-size:4rem;">
               ${product.emoji}
           </div>`;
    return `
        <div class="card-3d-wrap reveal delay-${Math.min(index % 3 + 1, 4)}" data-id="${product.id}">
            <div class="kn-product-card" style="overflow:hidden;padding:0;position:relative;">
                ${badge}
                ${imgHTML}
                <div class="kn-product-body">
                    <div class="kn-product-tag">${product.tag}</div>
                    <h3 class="kn-product-name">${product.name}</h3>
                    <p class="kn-product-desc">${product.desc}</p>
                    <div class="kn-product-footer">
                        <div class="kn-product-price">${product.price.toLocaleString('fr-FR')} FCFA</div>
                        <button class="kn-product-btn" onclick="addToCart('${product.id}')" title="Ajouter au panier">+</button>
                    </div>
                </div>
            </div>
        </div>
    `;
}

// ── Cart Functions ──
function addToCart(productId) {
    const product = PRODUCTS.find(p => p.id === productId);
    if (!product) return;

    const existing = cart.find(item => item.id === productId);
    if (existing) {
        existing.qty += 1;
    } else {
        cart.push({ id: productId, qty: 1 });
    }

    saveCart();
    updateCartCount();
    showToast(`${product.name} ajouté au panier`);
}

function removeFromCart(productId) {
    cart = cart.filter(item => item.id !== productId);
    saveCart();
    updateCartCount();
    renderCart();
}

function updateQty(productId, delta) {
    const item = cart.find(i => i.id === productId);
    if (!item) return;
    item.qty += delta;
    if (item.qty <= 0) {
        removeFromCart(productId);
        return;
    }
    saveCart();
    updateCartCount();
    renderCart();
}

function saveCart() {
    localStorage.setItem('karanice_cart', JSON.stringify(cart));
}

function getCartTotal() {
    return cart.reduce((sum, item) => {
        const product = PRODUCTS.find(p => p.id === item.id);
        return sum + (product ? product.price * item.qty : 0);
    }, 0);
}

function updateCartCount() {
    const countEl = document.getElementById('cartCount');
    if (!countEl) return;
    const total = cart.reduce((sum, item) => sum + item.qty, 0);
    countEl.textContent = total;
    countEl.classList.toggle('empty', total === 0);
    countEl.classList.add('pop');
    setTimeout(() => countEl.classList.remove('pop'), 300);
}

function renderCart() {
    const container = document.getElementById('cartItems');
    const footer = document.getElementById('cartFooter');
    const totalEl = document.getElementById('cartTotal');
    const orderTotalEl = document.getElementById('orderTotal');
    const checkoutBtn = document.getElementById('checkoutBtn');

    if (!container) return;

    if (cart.length === 0) {
        container.innerHTML = `
            <div class="kn-cart-empty">
                <div class="kn-cart-empty-icon">🧺</div>
                <p>Votre panier est vide</p>
                <a href="index.html#produits" class="btn btn-outline btn-sm" style="margin-top:1rem;" onclick="toggleCart();">Parcourir les produits</a>
            </div>
        `;
        if (footer) footer.style.display = 'none';
        return;
    }

    if (footer) footer.style.display = 'block';

    container.innerHTML = cart.map(item => {
        const product = PRODUCTS.find(p => p.id === item.id);
        if (!product) return '';
        return `
            <div class="kn-cart-item">
                <div class="kn-cart-item-img">${product.emoji}</div>
                <div class="kn-cart-item-info">
                    <div class="kn-cart-item-name">${product.name}</div>
                    <div class="kn-cart-item-price">${product.price.toLocaleString('fr-FR')} FCFA</div>
                    <div class="kn-cart-item-qty">
                        <button class="kn-qty-btn" onclick="updateQty('${product.id}', -1)">−</button>
                        <span class="kn-qty-val">${item.qty}</span>
                        <button class="kn-qty-btn" onclick="updateQty('${product.id}', 1)">+</button>
                    </div>
                </div>
                <button class="kn-cart-item-remove" onclick="removeFromCart('${product.id}')">✕</button>
            </div>
        `;
    }).join('');

    const total = getCartTotal();
    const totalStr = total.toLocaleString('fr-FR') + ' FCFA';
    if (totalEl) totalEl.textContent = totalStr;
    if (orderTotalEl) orderTotalEl.textContent = totalStr;
}

function toggleCart() {
    const overlay = document.getElementById('cartOverlay');
    if (!overlay) return;
    overlay.classList.toggle('open');
    renderCart();
}

// ── Checkout ──
function openCheckout() {
    if (cart.length === 0) return;
    toggleCart();
    const modal = document.getElementById('checkoutModal');
    if (modal) {
        modal.classList.add('open');
        document.getElementById('checkoutForm').classList.remove('hidden');
        document.getElementById('checkoutSuccess').classList.add('hidden');
        updateCartCount();
        renderCart();
    }
}

function closeCheckout() {
    const modal = document.getElementById('checkoutModal');
    if (modal) modal.classList.remove('open');
}

function selectPayment(el, method) {
    document.querySelectorAll('.kn-payment-option').forEach(opt => opt.classList.remove('selected'));
    el.classList.add('selected');
    el.querySelector('input[type="radio"]').checked = true;
    selectedPayment = method;
}

function submitOrder() {
    const name = document.getElementById('checkName')?.value.trim();
    const phone = document.getElementById('checkPhone')?.value.trim();
    const city = document.getElementById('checkCity')?.value;
    const address = document.getElementById('checkAddress')?.value.trim();
    const notes = document.getElementById('checkNotes')?.value.trim();

    if (!name || !phone || !city) {
        showToast('Veuillez remplir les champs obligatoires', '⚠️');
        return;
    }

    // Build WhatsApp message
    const total = getCartTotal();
    const items = cart.map(item => {
        const p = PRODUCTS.find(pr => pr.id === item.id);
        return `• ${p.name} ×${item.qty} = ${(p.price * item.qty).toLocaleString('fr-FR')} FCFA`;
    }).join('\n');

    const paymentNames = {
        cod: 'Paiement à la livraison',
        moov: 'Moov Money',
        mtncel: 'MTN/CeltiPlus',
        wave: 'Wave'
    };

    const message = `🛒 *Nouvelle commande Kara Nice*

👤 *Client:* ${name}
📞 *Téléphone:* ${phone}
🏙️ *Ville:* ${city}
📍 *Adresse:* ${address || 'Non précisée'}
💳 *Paiement:* ${paymentNames[selectedPayment] || 'À la livraison'}

📦 *Produits:*
${items}

💰 *Total:* ${total.toLocaleString('fr-FR')} FCFA
${notes ? '\n📝 *Notes:* ' + notes : ''}`;

    // Show success
    document.getElementById('checkoutForm').classList.add('hidden');
    document.getElementById('checkoutSuccess').classList.remove('hidden');

    // Clear cart
    cart = [];
    saveCart();
    updateCartCount();

    // Open WhatsApp after a short delay
    setTimeout(() => {
        const encoded = encodeURIComponent(message);
        window.open(`https://wa.me/2290195517485?text=${encoded}`, '_blank');
    }, 1500);
}

// ── Toast ──
function showToast(msg, icon = '✓') {
    const toast = document.getElementById('toast');
    const toastMsg = document.getElementById('toastMsg');
    const toastIcon = document.getElementById('toastIcon');
    if (!toast) return;
    toastMsg.textContent = msg;
    toastIcon.textContent = icon;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3000);
}

// ── Scroll to Top ──
function initScrollTop() {
    const btn = document.getElementById('scrollTopBtn');
    if (!btn) return;
    window.addEventListener('scroll', () => {
        btn.classList.toggle('visible', window.scrollY > 500);
    });
}

function scrollToTop() {
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ── Contact Form ──
function submitContact(e) {
    e.preventDefault();
    const name = document.getElementById('contactName')?.value.trim();
    const phone = document.getElementById('contactPhone')?.value.trim();
    const email = document.getElementById('contactEmail')?.value.trim();
    const subject = document.getElementById('contactSubject')?.value;
    const message = document.getElementById('contactMessage')?.value.trim();

    if (!name || !phone || !message) {
        showToast('Veuillez remplir les champs obligatoires', '⚠️');
        return;
    }

    const subjectNames = {
        'commande': 'Commande',
        'formation': 'Formation',
        'partenariat': 'Partenariat',
        'autre': 'Autre demande'
    };

    const waMessage = `📩 *Nouveau message — Kara Nice*

👤 *Nom:* ${name}
📞 *Téléphone:* ${phone}
📧 *E-mail:* ${email || 'Non renseigné'}
📋 *Sujet:* ${subjectNames[subject] || subject || 'Non spécifié'}

💬 *Message:*
${message}`;

    document.getElementById('contactForm').classList.add('hidden');
    document.getElementById('contactSuccess').classList.remove('hidden');

    setTimeout(() => {
        const encoded = encodeURIComponent(waMessage);
        window.open(`https://wa.me/2290195517485?text=${encoded}`, '_blank');
    }, 1200);
}

// ── Init on DOM ready ──
document.addEventListener('DOMContentLoaded', () => {
    initScrollTop();
});
