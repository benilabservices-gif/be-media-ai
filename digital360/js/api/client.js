/**
 * BENILAB Digital360 — API Client
 *
 * Fetch wrapper avec gestion CSRF, cookies de session,
 * et parsing automatique des erreurs application/problem+json.
 *
 * Mock support : activez avec window.D360_API_MODE = 'mock'
 * pour développer sans backend. Les mocks sont dans js/api/mocks/.
 */

const API_BASE = '/api/v1';
const MOCK_MODE = window.D360_API_MODE === 'mock';

// ── Helpers ──

function isProblemResponse(headers, body) {
    const ct = headers.get('content-type') || '';
    return ct.includes('application/problem+json');
}

async function parseResponse(res) {
    const headers = res.headers;
    const bodyText = await res.text();

    if (!res.ok) {
        if (bodyText && isProblemResponse(headers, bodyText)) {
            try {
                const problem = JSON.parse(bodyText);
                const err = new Error(problem.title || `HTTP ${res.status}`);
                err.status = res.status;
                err.code = problem.code;
                err.detail = problem.detail;
                err.errors = problem.errors;
                err.requestId = problem.request_id;
                throw err;
            } catch (_) {
                throw new Error(`HTTP ${res.status}: ${bodyText.slice(0, 200)}`);
            }
        }
        throw new Error(`HTTP ${res.status}: ${bodyText.slice(0, 200)}`);
    }

    if (!bodyText) return null;
    if (isProblemResponse(headers, bodyText)) {
        return JSON.parse(bodyText);
    }
    try {
        return JSON.parse(bodyText);
    } catch (_) {
        return bodyText;
    }
}

function getCsrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]*)/);
    return match ? match[1] : '';
}

function defaultHeaders(extra = {}) {
    const h = { 'Content-Type': 'application/json', ...extra };
    const csrf = getCsrfToken();
    if (csrf) h['X-CSRF-Token'] = csrf;
    return h;
}

// ── Generic request ──

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

// ── Public endpoints (no auth needed) ──

export const api = {
    // Diagnostic
    getQuestionnaire()     { return request('/public/questionnaire'); },
    createDiagnostic()     { return request('/public/diagnostics', { method: 'POST' }); },
    saveAnswers(id, answers) {
        return request(`/public/diagnostics/${id}/answers`, {
            method: 'PUT', body: { answers }
        });
    },
    saveContact(id, contact) {
        return request(`/public/diagnostics/${id}/contact`, {
            method: 'PUT', body: contact
        });
    },
    completeDiagnostic(id) {
        return request(`/public/diagnostics/${id}/complete`, {
            method: 'POST'
        });
    },
    getDiagnosticResult(id) {
        return request(`/public/diagnostics/${id}/result`);
    },

    // Catalog / pricing
    getCatalog(currency) {
        return request(`/public/catalog${currency ? `?currency=${currency}` : ''}`);
    },

    // Org (authenticated)
    getMe()               { return request('/me'); },
    getOrg(orgId)         { return request(`/orgs/${orgId}`); },
    getPassport(orgId)    { return request(`/orgs/${orgId}/passport`); },
    getActionPlan(orgId)  { return request(`/orgs/${orgId}/action-plan`); },
    getEntitlements(orgId){ return request(`/orgs/${orgId}/entitlements`); },
    getWebsiteProjects(orgId) {
        return request(`/orgs/${orgId}/website-projects`);
    },
    claimDiagnostic(diagId) {
        return request('/orgs', {
            method: 'POST',
            body: { diagnostic_id: diagId }
        });
    },
};

// ── Currency formatting ──
// Prices come from the API as { amount, currency }.
// Display rules: XOF/XAF → "NNN FCFA HT", EUR → "NN,NN € HT"

export function formatPrice(amount, currency) {
    if (amount == null) return '—';
    const amt = typeof amount === 'object' ? amount.amount : amount;
    const cur = typeof amount === 'object' ? (amount.currency || currency) : currency;

    if (cur === 'EUR') {
        return new Intl.NumberFormat('fr-FR', {
            style: 'decimal', minimumFractionDigits: 2, maximumFractionDigits: 2
        }).format(amt / 100) + ' € HT';
    }
    // XOF / XAF
    return new Intl.NumberFormat('fr-FR').format(amt) + ' FCFA HT';
}

export function formatPriceShort(amount, currency) {
    if (amount == null) return '—';
    const amt = typeof amount === 'object' ? amount.amount : amount;
    const cur = typeof amount === 'object' ? (amount.currency || currency) : currency;
    if (cur === 'EUR') return (amt / 100).toFixed(2).replace('.', ',') + ' €';
    return amt.toLocaleString('fr-FR') + ' F';
}

// ── Mock mode loader ──

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
