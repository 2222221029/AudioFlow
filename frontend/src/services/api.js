const DEFAULT_API_BASE = '/api';

let onAuthRequired = null;

function trimSlash(value) {
  return String(value || '').replace(/\/+$/, '');
}

function resolveApiBase() {
  const runtimeBase = globalThis.window?.__AUDIOFLOW_API_BASE__;
  const envBase = import.meta.env.VITE_API_BASE_URL;
  return trimSlash(runtimeBase || envBase || DEFAULT_API_BASE);
}

function normalizeBody(options) {
  if (!options || !('body' in options)) return options || {};
  if (options.body == null || typeof options.body === 'string' || options.body instanceof FormData) {
    return options;
  }
  return {...options, body: JSON.stringify(options.body)};
}

export function apiUrl(path) {
  if (/^https?:\/\//i.test(path)) return path;
  const base = resolveApiBase();
  if (!base) return path;
  if (path === '/health') return `${base.replace(/\/api$/, '')}/health`;
  if (path.startsWith('/api/')) return `${base}${path.slice(4)}`;
  if (path === '/api') return base;
  if (path.startsWith('/')) return `${base}${path}`;
  return `${base}/${path}`;
}

export function setAuthRequiredHandler(handler) {
  onAuthRequired = handler;
}

async function requestJson(path, options = {}) {
  const normalized = normalizeBody(options);
  const headers = normalized.body && !(normalized.body instanceof FormData)
    ? {'Content-Type': 'application/json', ...(normalized.headers || {})}
    : (normalized.headers || {});
  const response = await fetch(apiUrl(path), {...normalized, headers, credentials: 'same-origin'});
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    const error = new Error(data.error || response.statusText || 'Request failed');
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

export async function api(path, options = {}) {
  try {
    return await requestJson(path, options);
  } catch (error) {
    if (error.status === 401 && onAuthRequired && !String(path).startsWith('/api/auth/')) {
      await onAuthRequired();
      return requestJson(path, options);
    }
    throw error;
  }
}

export async function login(username, password) {
  return requestJson('/api/auth/login', {method: 'POST', body: {username, password}});
}

export async function logout() {
  return requestJson('/api/auth/logout', {method: 'POST'}).catch(() => ({}));
}

export async function authStatus() {
  return requestJson('/api/auth/status');
}
