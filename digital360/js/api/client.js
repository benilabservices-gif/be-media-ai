/**
 * BENILAB Digital360 — API Client
 *
 * Fetch wrapper avec gestion CSRF (jeton en mémoire), cookies de session,
 * et parsing automatique des erreurs application/problem+json.
 *
 * Configuration :
 *   window.D360_API_BASE  → URL de base de l'API (défaut : http://localhost:8000/api/v1)
 *   window.D360_API_MODE  → 'mock' pour activer les mocks locaux
 *
 * Authentification pas à pas (voir platform/contracts/README.md) :
 *   1. api.getCsrf()         — au chargement de la page
 *   2. api.register(...) ou api.login(...) — crée la session
 *   3. api.getMe()           — construit l'interface (user, memberships, permissions)
 */

// ── Configuration ──
const API_BASE = window.D360_API_BASE || 'http://localhost:8000/api/v1';
const MOCK_MODE = window.D360_API_MODE === 'mock';

// ── CSRF token stocké en mémoire (plus fiable que document.cookie) ──
let _csrfToken = '';

/**
 * Appelez ce script au chargement de chaque page pour récupérer le jeton CSRF.
 * Les requêtes POST/PUT/PATCH/DELETE l'enveront automatiquement dans l'en-tête X-CSRF-Token.
 */
export async function getCsrf() {
    try {
        const res = await fetch(API_BASE + '/auth/csrf', { credentials: 'include' });
        const data = await res.json();
        _csrfToken = data.csrf_token || '';
        return data;
    } catch (e) {
        console.warn('[D360] CSRF fetch failed:', e);
        return { csrf_token: '' };
    }
}

function setCsrfToken(token) {
    _csrfToken = token;
}

function defaultHeaders(extra = {}) {
    const h = { 'Content-Type': 'application/json', ...extra };
    if (_csrfToken) h['X-CSRF-Token'] = _csrfToken;
    return h;
}

// ── Réponse : parse JSON ou renvoie l'erreur enrichie ──

async function parseResponse(res) {
    const headers = res.headers;
    const bodyText = await res.text();

    if (!res.ok) {
        let problem = null;
        const ct = headers.get('content-type') || '';
        if (bodyText && ct.includes('application/problem+json')) {
            try { problem = JSON.parse(bodyText); } catch (_) { /* corps illisible */ }
        }
        const err = new Error(problem?.detail || `HTTP ${res.status}`);
        Object.assign(err, {
            status: res.status,
            code: problem?.code,
            detail: problem?.detail,
            errors: problem?.errors,
            requestId: problem?.request_id,
        });
        throw err;
    }

    if (!bodyText) return null;
    try { return JSON.parse(bodyText); } catch (_) { return bodyText; }
}

// ── Requête générique ──

async function request(path, options = {}) {
    const url = API_BASE + path;
    const opts = {
        credentials: 'include',
        headers: defaultHeaders(options.headers),
        ...options,
        headers: { ...defaultHeaders(options.headers), ...(options.headers || {}) },
    };
    if (options.body && !(options.body instanceof FormData)) {
        opts.body = JSON.stringify(options.body);
    }
    return fetch(url, opts).then(parseResponse);
}

// ── Endpoints Auth ──

const auth = {
    getCsrf,
    register(body) {
        return request('/auth/register', { method: 'POST', body }).then(data => {
            if (data?.csrf_token) setCsrfToken(data.csrf_token);
            return data;
        });
    },
    login(body) {
        return request('/auth/login', { method: 'POST', body }).then(data => {
            if (data?.csrf_token) setCsrfToken(data.csrf_token);
            return data;
        });
    },
    logout() { return request('/auth/logout', { method: 'POST' }); },
    getMe()        { return request('/me'); },
    updateMe(body) { return request('/me', { method: 'PATCH', body }); },
};

// ── Endpoints Public (diagnostic + catalog) ──

const diagnostic = {
    getQuestionnaire()  { return request('/public/questionnaire'); },
    createDiagnostic()  { return request('/public/diagnostics', { method: 'POST' }); },
    saveAnswers(id, answers, token) {
        return request(`/public/diagnostics/${id}/answers`, {
            method: 'PUT',
            headers: token ? { 'X-Diagnostic-Token': token } : {},
            body: { answers }
        });
    },
    saveContact(id, contact, token) {
        return request(`/public/diagnostics/${id}/contact`, {
            method: 'PUT',
            headers: token ? { 'X-Diagnostic-Token': token } : {},
            body: contact
        });
    },
    completeDiagnostic(id, token) {
        return request(`/public/diagnostics/${id}/complete`, {
            method: 'POST',
            headers: token ? { 'X-Diagnostic-Token': token } : {},
        });
    },
    getDiagnosticResult(id, token) {
        return request(`/public/diagnostics/${id}/result`, {
            headers: token ? { 'X-Diagnostic-Token': token } : {},
        });
    },
};

const catalog = {
    getCatalog(currency) {
        return request(`/public/catalog${currency ? `?currency=${currency}` : ''}`);
    },
};

// ── Endpoints Organisation (authentifié) ──

const orgs = {
    createOrg(body) {
        return request('/orgs', { method: 'POST', body });
    },
    getOrg(orgId)         { return request(`/orgs/${orgId}`); },
    updateOrg(orgId, body) { return request(`/orgs/${orgId}`, { method: 'PATCH', body }); },
    getMembers(orgId)     { return request(`/orgs/${orgId}/members`); },
};

// ── Endpoints Admin (staff uniquement) ──

const admin = {
    listOrgs(params = {}) {
        const qs = new URLSearchParams(params).toString();
        return request(`/admin/organizations${qs ? `?${qs}` : ''}`);
    },
    getOrg(id) { return request(`/admin/organizations/${id}`); },
};

// ── Export public ──

export const api = { ...auth, ...diagnostic, ...catalog, ...orgs, ...admin };

// ── Currency formatting ──
// Prices come from the API as { amount, currency } (amount en unité mineure).
// XOF/XAF → "NNN FCFA HT"  ·  EUR → "NN,NN € HT"

export function formatPrice(amount, currency) {
    if (amount == null) return '—';
    const amt = typeof amount === 'object' ? amount.amount : amount;
    const cur = typeof amount === 'object' ? (amount.currency || currency) : currency;
    if (cur === 'EUR') {
        return new Intl.NumberFormat('fr-FR', {
            minimumFractionDigits: 2, maximumFractionDigits: 2
        }).format(amt / 100) + ' € HT';
    }
    return new Intl.NumberFormat('fr-FR').format(amt) + ' FCFA HT';
}

export function formatPriceShort(amount, currency) {
    if (amount == null) return '—';
    const amt = typeof amount === 'object' ? amount.amount : amount;
    const cur = typeof amount === 'object' ? (amount.currency || currency) : currency;
    if (cur === 'EUR') return (amt / 100).toFixed(2).replace('.', ',') + ' €';
    return amt.toLocaleString('fr-FR') + ' F';
}

// ── Mock mode ──

let _mockData = null;

export async function initMockMode() {
    if (!MOCK_MODE) return;
    try {
        const mod = await import('./mocks/index.js');
        _mockData = mod.default;
        console.log('[D360] Mock mode active');
    } catch (e) {
        console.warn('[D360] Mock mode failed to load:', e);
    }
}

export function mockRequest(endpoint) {
    if (!MOCK_MODE || !_mockData) return null;
    return _mockData[endpoint] || null;
}

// Wrap each API call to use mock when available
const _orig = { ...api };
for (const key of Object.keys(api)) {
    api[key] = async function (...args) {
        if (MOCK_MODE) {
            const mock = mockRequest(key);
            if (mock) return Promise.resolve(mock(...args));
        }
        return _orig[key](...args);
    };
}

export default api;
