// API base: ?apiBase= -> D365 `data` param -> window.CQA_CONFIG -> same origin.
function resolveBase() {
  const p = new URLSearchParams(location.search);
  if (p.get('apiBase')) return p.get('apiBase');
  const data = p.get('data');
  if (data) {
    try { const d = JSON.parse(data); if (d.apiBase) return d.apiBase; } catch { /* not JSON */ }
    const inner = new URLSearchParams(data);
    if (inner.get('apiBase')) return inner.get('apiBase');
  }
  return (window.CQA_CONFIG && window.CQA_CONFIG.apiBase) || '';
}

export const BASE = resolveBase().replace(/\/$/, '');

export async function api(path, { method = 'GET', body, form } = {}) {
  const opts = { method, headers: {} };
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.body = JSON.stringify(body);
    opts.headers['Content-Type'] = 'application/json';
  }
  let r;
  try {
    r = await fetch(`${BASE}/api${path}`, opts);
  } catch {
    throw new Error('Сервер недоступен. Проверьте, что backend запущен.');
  }
  const isJson = (r.headers.get('content-type') || '').includes('json');
  const data = isJson ? await r.json().catch(() => null) : await r.text();
  if (!r.ok) throw new Error((data && (data.detail || data.error)) || `Ошибка ${r.status}`);
  return data;
}

export const apiUrl = (path) => `${BASE}/api${path}`;
