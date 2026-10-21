/**
 * Be Media AI — Agence Web & Intelligence Artificielle
 * JavaScript Principal
 */

document.addEventListener('DOMContentLoaded', () => {

    // ── Custom cursor ──
    const cursor = document.getElementById('cursor');
    const follower = document.getElementById('cursorFollower');
    let mx = 0, my = 0, fx = 0, fy = 0;

    document.addEventListener('mousemove', e => { mx = e.clientX; my = e.clientY; });

    function animateCursor() {
        fx += (mx - fx) * 0.12;
        fy += (my - fy) * 0.12;
        if (cursor) {
            cursor.style.left = mx + 'px';
            cursor.style.top = my + 'px';
        }
        if (follower) {
            follower.style.left = fx + 'px';
            follower.style.top = fy + 'px';
        }
        requestAnimationFrame(animateCursor);
    }
    animateCursor();

    document.querySelectorAll('[data-hover]').forEach(el => {
        el.addEventListener('mouseenter', () => { cursor?.classList.add('hover'); follower?.classList.add('hover'); });
        el.addEventListener('mouseleave', () => { cursor?.classList.remove('hover'); follower?.classList.remove('hover'); });
    });

    // ── Scroll progress bar ──
    const progressBar = document.getElementById('scrollProgress');
    window.addEventListener('scroll', () => {
        if (!progressBar) return;
        const h = document.documentElement.scrollHeight - window.innerHeight;
        const progress = window.pageYOffset / h;
        progressBar.style.transform = `scaleX(${progress})`;
    });

    // ── Navbar scroll effect ──
    const navbar = document.getElementById('navbar');
    window.addEventListener('scroll', () => {
        if (navbar) navbar.classList.toggle('scrolled', window.pageYOffset > 80);
    });

    // ── Mobile menu ──
    const menuToggle = document.getElementById('menuToggle');
    const mobileMenu = document.getElementById('mobileMenu');
    if (menuToggle && mobileMenu) {
        menuToggle.addEventListener('click', () => mobileMenu.classList.toggle('open'));
        mobileMenu.querySelectorAll('.mobile-link').forEach(link => {
            link.addEventListener('click', () => mobileMenu.classList.remove('open'));
        });
    }
    window.closeMobile = () => mobileMenu?.classList.remove('open');

    // ── 3D Card tilt ──
    const card3d = document.getElementById('card3d');
    if (card3d) {
        const heroVisual = card3d.parentElement;
        heroVisual.addEventListener('mousemove', e => {
            const rect = heroVisual.getBoundingClientRect();
            const x = (e.clientX - rect.left) / rect.width - 0.5;
            const y = (e.clientY - rect.top) / rect.height - 0.5;
            card3d.style.transform = `rotateY(${x * 20}deg) rotateX(${-y * 20}deg)`;
        });
        heroVisual.addEventListener('mouseleave', () => {
            card3d.style.transform = 'rotateY(0) rotateX(0)';
        });
    }

    // ── Service cards spotlight ──
    document.querySelectorAll('.service-card').forEach(card => {
        card.addEventListener('mousemove', e => {
            const rect = card.getBoundingClientRect();
            card.style.setProperty('--mx', ((e.clientX - rect.left) / rect.width * 100) + '%');
            card.style.setProperty('--my', ((e.clientY - rect.top) / rect.height * 100) + '%');
        });
    });

    // ── Hero canvas — soft particles ──
    const canvas = document.getElementById('hero-canvas');
    let particles = [];
    let animFrame;

    function resizeCanvas() {
        if (!canvas) return;
        canvas.width = canvas.parentElement.offsetWidth;
        canvas.height = canvas.parentElement.offsetHeight;
        initParticles();
    }

    class Particle {
        constructor() { this.reset(); }
        reset() {
            this.x = Math.random() * (canvas?.width || 800);
            this.y = Math.random() * (canvas?.height || 600);
            this.vx = (Math.random() - 0.5) * 0.3;
            this.vy = (Math.random() - 0.5) * 0.3;
            this.r = Math.random() * 1.5 + 0.5;
            this.o = Math.random() * 0.25 + 0.05;
        }
        update() {
            this.x += this.vx; this.y += this.vy;
            if (this.x < 0 || this.x > canvas.width) this.vx *= -1;
            if (this.y < 0 || this.y > canvas.height) this.vy *= -1;
        }
        draw() {
            ctx.beginPath();
            ctx.arc(this.x, this.y, this.r, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(196, 168, 130, ${this.o})`;
            ctx.fill();
        }
    }

    const ctx = canvas?.getContext('2d');
    if (ctx && canvas) {
        function initParticles() {
            particles = [];
            const count = Math.min(60, Math.floor(canvas.width * canvas.height / 20000));
            for (let i = 0; i < count; i++) particles.push(new Particle());
        }

        function animate() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            particles.forEach(p => { p.update(); p.draw(); });
            for (let i = 0; i < particles.length; i++) {
                for (let j = i + 1; j < particles.length; j++) {
                    const dx = particles[i].x - particles[j].x;
                    const dy = particles[i].y - particles[j].y;
                    const dist = Math.sqrt(dx*dx + dy*dy);
                    if (dist < 130) {
                        ctx.beginPath();
                        ctx.moveTo(particles[i].x, particles[i].y);
                        ctx.lineTo(particles[j].x, particles[j].y);
                        ctx.strokeStyle = `rgba(196, 168, 130, ${0.04 * (1 - dist/130)})`;
                        ctx.lineWidth = 0.5;
                        ctx.stroke();
                    }
                }
            }
            animFrame = requestAnimationFrame(animate);
        }
        resizeCanvas();
        window.addEventListener('resize', resizeCanvas);
        animate();
    }

    // ── Scroll reveal ──
    const revealObserver = new IntersectionObserver(entries => {
        entries.forEach(e => { if (e.isIntersecting) e.target.classList.add('active'); });
    }, { threshold: 0.1, rootMargin: '0px 0px -40px 0px' });
    document.querySelectorAll('.reveal, .reveal-left, .reveal-scale').forEach(el => revealObserver.observe(el));

    // ── Counter animation ──
    const counterObserver = new IntersectionObserver(entries => {
        entries.forEach(e => {
            if (e.isIntersecting) {
                const el = e.target;
                const target = parseInt(el.dataset.count);
                let current = 0;
                const step = target / 50;
                const timer = setInterval(() => {
                    current += step;
                    if (current >= target) { current = target; clearInterval(timer); }
                    el.textContent = Math.floor(current) + (target === 98 ? '%' : '+');
                }, 20);
                counterObserver.unobserve(el);
            }
        });
    }, { threshold: 0.5 });
    document.querySelectorAll('[data-count]').forEach(el => counterObserver.observe(el));

    // ── Scroll to top ──
    const scrollTopBtn = document.getElementById('scrollTop');
    if (scrollTopBtn) {
        window.addEventListener('scroll', () => {
            scrollTopBtn.classList.toggle('visible', window.pageYOffset > 600);
        });
        scrollTopBtn.addEventListener('click', () => {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
    }

    // ── Newsletter form ──
    window.handleNewsletter = function(e) {
        e.preventDefault();
        const form = document.getElementById('newsletterForm');
        const success = document.getElementById('newsletterSuccess');
        if (form) form.style.display = 'none';
        if (success) success.style.display = 'block';
    };

    // ── Contact Modal ──
    window.openContactModal = function() {
        const modal = document.getElementById('contactModal');
        if (modal) {
            modal.classList.add('open');
            document.body.style.overflow = 'hidden';
        }
    };
    window.closeContactModal = function() {
        const modal = document.getElementById('contactModal');
        if (modal) {
            modal.classList.remove('open');
            document.body.style.overflow = '';
        }
    };
    window.handleContact = function(e) {
        e.preventDefault();
        const form = document.getElementById('contactForm');
        const success = document.getElementById('contactSuccess');
        if (form) form.style.display = 'none';
        if (success) success.style.display = 'block';
    };
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeContactModal(); });
});
