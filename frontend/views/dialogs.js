import { api } from '../api.js';
import { CHANNEL, STATUS, fmt, h, scoreClass } from '../dom.js';

const state = { dataset: '', lang: '', channel: '', status: '', q: '', sort: 'source_id', dir: 1 };

export async function render(main) {
  const datasets = await api('/datasets');
  if (!datasets.length) {
    main.replaceChildren(h('div', { class: 'panel empty' },
      h('p', {}, 'Диалогов пока нет.'),
      h('p', {}, 'Загрузите набор по group_id на странице ', h('a', { href: '#/datasets' }, 'Наборы'), '.')));
    return;
  }
  const tableWrap = h('div');
  const count = h('span', { class: 'muted small' });
  const sel = (key, label, opts) => h('label', { class: 'field' }, label,
    h('select', { value: state[key], onchange: (e) => { state[key] = e.target.value; load(); } },
      opts.map(([v, t]) => h('option', { value: v }, t))));

  const search = h('input', { type: 'search', placeholder: 'id или текст реплики', value: state.q });
  let timer;
  search.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { state.q = search.value.trim(); load(); }, 300);
  });

  main.replaceChildren(
    h('div', { class: 'row' }, h('h1', {}, 'Диалоги'), h('span', { class: 'spacer' }), count),
    h('div', { class: 'filters' },
      sel('dataset', 'Набор', [['', 'Все наборы'], ...datasets.map((d) => [d.group_id, `${d.group_id} (${d.conversations})`])]),
      sel('lang', 'Язык', [['', 'Все'], ['RU', 'RU'], ['KZ', 'KZ'], ['EN', 'EN']]),
      sel('channel', 'Канал', [['', 'Все'], ['agent chat', 'Чат'], ['voice call', 'Звонок']]),
      sel('status', 'Статус', [['', 'Все'], ['not_evaluated', 'Не оценён'], ['done', 'Оценён'],
        ['needs_review', 'Требует проверки'], ['appealed', 'Есть апелляция'], ['critical', 'Грубая ошибка']]),
      h('label', { class: 'field' }, 'Поиск', search)),
    tableWrap);

  async function load() {
    const p = new URLSearchParams(Object.entries(state).filter(([k, v]) => v && k !== 'sort' && k !== 'dir'));
    let rows;
    try { rows = await api(`/conversations?${p}`); } catch (e) {
      tableWrap.replaceChildren(h('div', { class: 'error-box' }, e.message));
      return;
    }
    count.textContent = `${rows.length} диалогов`;
    rows.sort((a, b) => {
      const x = a[state.sort] ?? -1, y = b[state.sort] ?? -1;
      return (x > y ? 1 : x < y ? -1 : 0) * state.dir;
    });
    if (!rows.length) {
      tableWrap.replaceChildren(h('div', { class: 'panel empty' }, 'Ничего не найдено. Измените фильтры.'));
      return;
    }
    const th = (key, label) => h('th', { 'aria-sort': state.sort === key ? (state.dir > 0 ? 'ascending' : 'descending') : null },
      h('button', { type: 'button', onclick: () => { state.dir = state.sort === key ? -state.dir : 1; state.sort = key; load(); } },
        label, state.sort === key ? (state.dir > 0 ? ' ↑' : ' ↓') : ''));
    tableWrap.replaceChildren(h('table', { class: 'list' },
      h('thead', {}, h('tr', {}, th('source_id', 'Диалог'), h('th', { class: 'hide-narrow' }, 'Набор'), th('language', 'Язык'),
        th('channel', 'Канал'), th('total', 'Итог'), h('th', {}, 'Статус'), h('th', { class: 'hide-narrow' }, 'Чек-лист'))),
      h('tbody', {}, rows.map((r) => {
        const href = `#/dialog/${encodeURIComponent(r.id)}`;
        return h('tr', { class: 'click', onclick: (e) => { if (e.target.tagName !== 'A') location.hash = href; } },
          h('td', {}, h('a', { href }, r.source_id)),
          h('td', { class: 'hide-narrow muted' }, r.dataset_group_id),
          h('td', {}, r.language),
          h('td', {}, CHANNEL[r.channel] || r.channel),
          h('td', {}, h('span', { class: `score ${scoreClass(r.total)}` }, r.total == null ? '—' : fmt(r.total))),
          h('td', {}, h('div', { class: 'row' },
            h('span', { class: `badge ${r.eval_status === 'needs_review' ? 'mid' : r.eval_status === 'done' ? 'accent' : ''}` },
              STATUS[r.eval_status] || r.eval_status),
            r.critical_failed && h('span', { class: 'badge bad' }, 'Грубая ошибка'),
            r.provisional && r.eval_status !== 'needs_review' && h('span', { class: 'badge mid' }, 'Предварительно'),
            r.open_appeals > 0 && h('span', { class: 'badge mid' }, `Апелляция: ${r.open_appeals}`))),
          h('td', { class: 'hide-narrow muted' }, r.checklist_version ? `v${r.checklist_version}` : '—'));
      }))));
  }
  await load();
}
