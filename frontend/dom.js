import { api } from './api.js';

// Tiny DOM helper. All data goes in as text nodes / attributes, never innerHTML (transcripts and
// model output are untrusted).
export function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  let value;
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'value') value = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'checked' || k === 'disabled' || k === 'selected' || k === 'open') el[k] = !!v;
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : String(kid));
  }
  if (value !== undefined) el.value = value;
  return el;
}

export function toast(msg, kind = 'ok') {
  const t = h('div', { class: `toast ${kind}`, role: kind === 'error' ? 'alert' : 'status' }, msg);
  document.getElementById('toasts').append(t);
  setTimeout(() => t.remove(), kind === 'error' ? 7000 : 3500);
}

// wraps an async action: disables the button, shows errors as toasts
export async function act(btn, fn) {
  if (btn) btn.disabled = true;
  try { return await fn(); } catch (e) { toast(e.message, 'error'); } finally { if (btn) btn.disabled = false; }
}

export const fmt = (n, d = 2) => (n == null ? '—' : Number(n).toFixed(d));
export const scoreClass = (t) => (t == null ? '' : t >= 85 ? 'good' : t >= 60 ? 'mid' : 'bad');
export const SPEAKER = { client: 'Клиент', operator: 'Оператор', bot: 'Бот', system: 'Система' };
export const CHANNEL = { 'agent chat': 'Чат', 'voice call': 'Звонок' };
export const STATUS = {
  not_evaluated: 'Не оценён', running: 'Идёт оценка', done: 'Готово', needs_review: 'Требует проверки',
  interrupted: 'Прервано', done_with_errors: 'Готово, есть ошибки',
};

export function getRole() {
  try { return localStorage.getItem('cqa.role') || 'operator'; } catch { return 'operator'; }
}
export function setRole(r) {
  try { localStorage.setItem('cqa.role', r); } catch { /* private mode: role lives only in memory */ }
}

export function answerLabel(c, verdict, value, status) {
  if (status === 'needs_review') return { text: 'Требует проверки', cls: 'review' };
  if (verdict == null) return { text: '—', cls: 'na' };
  if (verdict === 'na') return { text: 'N/A', cls: 'na' };
  if (c.is_critical) return verdict === 'yes' ? { text: 'Выявлено', cls: 'no' } : { text: 'Не выявлено', cls: 'yes' };
  if (verdict === 'yes') return { text: 'Да', cls: 'yes' };
  if (verdict === 'no') return { text: 'Нет', cls: 'no' };
  const vals = c.scale.steps.map((s) => s.value);
  const max = Math.max(...vals);
  return { text: `${value} из ${max}`, cls: value >= max ? 'yes' : value <= Math.min(...vals) ? 'no' : 'part' };
}

export function answerOptions(c) {
  const opts = [];
  if (c.answer_type === 'scale') {
    [...c.scale.steps].sort((a, b) => b.value - a.value)
      .forEach((s) => opts.push({ verdict: 'scale', value: s.value, label: `${s.value} — ${s.label}` }));
  } else if (c.is_critical) {
    opts.push({ verdict: 'yes', label: 'Выявлено' }, { verdict: 'no', label: 'Не выявлено' });
  } else {
    opts.push({ verdict: 'yes', label: 'Да' }, { verdict: 'no', label: 'Нет' });
  }
  if (c.na_allowed) opts.push({ verdict: 'na', label: 'N/A' });
  return opts;
}

// polls a run and renders progress; onDone fires once, only while still on screen
export function runProgress(run, onDone) {
  const bar = h('progress', { max: Math.max(1, run.total), value: run.done });
  const text = h('div', { class: 'small muted' });
  const box = h('div', { class: 'run-progress' }, bar, text);
  const paint = (r) => {
    bar.max = Math.max(1, r.total);
    bar.value = r.done;
    text.textContent = `${STATUS[r.status] || r.status}: ${r.done} из ${r.total} · из кэша ${r.cache_hits}`
      + ` · новых вызовов модели ${r.new_calls} · требуют проверки ${r.needs_review}`
      + (r.cost ? ` · $${fmt(r.cost, 4)}` : '') + (r.error ? ` · ошибки: ${r.error}` : '');
  };
  paint(run);
  const tick = async () => {
    try {
      const r = await api(`/runs/${run.id}`);
      paint(r);
      if (r.status === 'running') setTimeout(tick, 1000);
      else if (onDone && box.isConnected) onDone(r);
    } catch (e) {
      text.textContent = e.message;
      setTimeout(tick, 3000);
    }
  };
  if (run.status === 'running') setTimeout(tick, 600);
  return box;
}
