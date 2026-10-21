// ═══════════════════════════════════════════════════════
// ÇA ME DIT TIC — Atelier Pratique
// Landing Page JavaScript
// ═══════════════════════════════════════════════════════

(function () {
    'use strict';

    // ── Configuration ──
    // Endpoint pour l'inscription à l'atelier. Laisser vide pour un mode démo.
    // Quand un endpoint sera disponible, le définir ici :
    // const EVENT_API_BASE = 'https://api.benilab.co/api/v1';
    const EVENT_API_BASE = '';

    // ── DOM refs ──
    const nav = document.getElementById('ticNav');
    const mobileMenu = document.getElementById('ticMobileMenu');
    const revealElements = document.querySelectorAll('.tic-reveal');
    const faqItems = document.querySelectorAll('.tic-faq-item');
    const registrationForm = document.getElementById('ticRegistrationForm');
    const formSubmitBtn = document.getElementById('ticSubmitBtn');
    const formLoading = document.getElementById('ticFormLoading');
    const formSuccess = document.getElementById('ticFormSuccess');
    const formAlert = document.getElementById('ticFormAlert');

    // ── Nav scroll ──
    function handleNavScroll() {
        if (nav) nav.classList.toggle('scrolled', window.pageYOffset > 60);
    }
    window.addEventListener('scroll', handleNavScroll, { passive: true });
    handleNavScroll();

    // ── Mobile menu ──
    window.toggleMobileMenu = function () {
        mobileMenu.classList.toggle('open');
        document.body.style.overflow = mobileMenu.classList.contains('open') ? 'hidden' : '';
    };
    window.closeMobileMenu = function () {
        mobileMenu.classList.remove('open');
        document.body.style.overflow = '';
    };

    // ── Smooth scroll for anchor links ──
    document.addEventListener('click', function (e) {
        const link = e.target.closest('a[href^="#"]');
        if (!link) return;
        const targetId = link.getAttribute('href');
        if (targetId === '#') return;
        const target = document.querySelector(targetId);
        if (!target) return;
        e.preventDefault();
        closeMobileMenu();
        const navHeight = nav ? nav.offsetHeight : 72;
        const top = target.getBoundingClientRect().top + window.pageYOffset - navHeight - 16;
        window.scrollTo({ top: top, behavior: 'smooth' });
    });

    // ── Reveal on scroll ──
    let revealObserver = null;
    try {
        revealObserver = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add('active');
                    revealObserver.unobserve(entry.target);
                }
            });
        }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });
    } catch (e) {
        revealObserver = { observe: function () {}, unobserve: function () {} };
    }
    revealElements.forEach(function (el) { revealObserver.observe(el); });

    // ── FAQ accordion ──
    faqItems.forEach(function (item) {
        var question = item.querySelector('.tic-faq-question');
        question.addEventListener('click', function () {
            var isOpen = item.classList.contains('open');
            faqItems.forEach(function (i) { i.classList.remove('open'); });
            if (!isOpen) item.classList.add('open');
        });
    });

    // ── Form handling ──
    function showAlert(message, type) {
        if (!formAlert) return;
        formAlert.className = 'tic-form-alert tic-form-alert-' + type + ' show';
        formAlert.textContent = message;
    }

    function clearAlert() {
        if (formAlert) formAlert.classList.remove('show');
    }

    function setError(input, show) {
        if (!input) return;
        var errorEl = input.parentElement.querySelector('.tic-form-error');
        if (show) {
            input.classList.add('error');
            if (errorEl) errorEl.classList.add('show');
        } else {
            input.classList.remove('error');
            if (errorEl) errorEl.classList.remove('show');
        }
    }

    function validateEmail(email) {
        return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
    }

    function validatePhone(phone) {
        return phone.replace(/\s/g, '').length >= 8;
    }

    // Clear errors on input
    var formInputs = registrationForm ? registrationForm.querySelectorAll('.tic-form-input, .tic-form-select') : [];
    formInputs.forEach(function (input) {
        input.addEventListener('input', function () {
            setError(input, false);
        });
    });

    if (registrationForm) {
        registrationForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            clearAlert();

            var fullName = document.getElementById('ticFullName');
            var whatsapp = document.getElementById('ticWhatsapp');
            var email = document.getElementById('ticEmail');
            var profile = document.getElementById('ticProfile');
            var consent = document.getElementById('ticConsent');

            var isValid = true;

            if (!fullName.value.trim()) {
                setError(fullName, true);
                isValid = false;
            } else {
                setError(fullName, false);
            }

            if (!whatsapp.value.trim() || !validatePhone(whatsapp.value.trim())) {
                setError(whatsapp, true);
                isValid = false;
            } else {
                setError(whatsapp, false);
            }

            if (email.value.trim() && !validateEmail(email.value.trim())) {
                setError(email, true);
                isValid = false;
            } else {
                setError(email, false);
            }

            if (!profile.value) {
                setError(profile, true);
                isValid = false;
            } else {
                setError(profile, false);
            }

            if (!consent.checked) {
                showAlert('Veuillez confirmer avoir lu les informations d\'inscription.', 'error');
                isValid = false;
            }

            if (!isValid) return;

            // Prevent double submission
            formSubmitBtn.disabled = true;
            formLoading.classList.add('show');

            var formData = {
                full_name: fullName.value.trim(),
                whatsapp: whatsapp.value.trim(),
                email: email.value.trim() || null,
                profile: profile.value
            };

            try {
                if (EVENT_API_BASE) {
                    var csrfResp = await fetch(EVENT_API_BASE + '/auth/csrf', { credentials: 'include' });
                    var csrfData = await csrfResp.json();
                    var csrfToken = csrfData.csrf_token || '';

                    var resp = await fetch(EVENT_API_BASE + '/events/registrations', {
                        method: 'POST',
                        credentials: 'include',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRF-Token': csrfToken
                        },
                        body: JSON.stringify(formData)
                    });

                    var data = await resp.json();
                    if (!resp.ok) {
                        throw new Error(data.detail || 'Une erreur est survenue.');
                    }
                } else {
                    // Demo mode: simulate network delay then show success
                    await new Promise(function (resolve) { setTimeout(resolve, 1200); });
                    // In demo mode we log but don't claim successful server registration
                    console.log('[Atelier TIC] Inscription (démo) — données reçues:', formData);
                }

                // Show success
                registrationForm.style.display = 'none';
                formSuccess.classList.add('show');
            } catch (err) {
                showAlert(err.message || 'Une erreur est survenue. Veuillez réessayer.', 'error');
                formSubmitBtn.disabled = false;
                formLoading.classList.remove('show');
            }
        });
    }

    // ── Scroll to registration section ──
    window.scrollToRegistration = function () {
        var target = document.getElementById('ticRegistration');
        if (target) {
            var navHeight = nav ? nav.offsetHeight : 72;
            var top = target.getBoundingClientRect().top + window.pageYOffset - navHeight - 16;
            window.scrollTo({ top: top, behavior: 'smooth' });
        }
    };

    // ── Scroll to program section ──
    window.scrollToProgram = function () {
        var target = document.getElementById('ticProgram');
        if (target) {
            var navHeight = nav ? nav.offsetHeight : 72;
            var top = target.getBoundingClientRect().top + window.pageYOffset - navHeight - 16;
            window.scrollTo({ top: top, behavior: 'smooth' });
        }
    };

})();
