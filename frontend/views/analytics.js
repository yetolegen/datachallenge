import { api } from '../api.js';
import { fmt, h, scoreClass } from '../dom.js';

let current = '';

export async function render(main) {
  const datasets = await api('/datasets');
  if (!datasets.length) {
    main.replaceChildren(h('div', { class: 'panel empty' }, 'Нет данных. Загрузите и оцените набор на странице ',
      h('a', { href: '#/datasets' }, 'Наборы'), '.'));
    return;
  }
  if (!current || !datasets.some((d) => d.group_id === current)) current = datasets[0].group_id;
  const a = await api(`/analytics?dataset=${encodeURIComponent(current)}`);
  const sel = h('select', { 'aria-label': 'Набор', value: current, onchange: (e) => { current = e.target.value; render(main); } },
    datasets.map((d) => h('option', { value: d.group_id }, d.group_id)));

  const kpi = (label, value, cls = '') => h('div', { class: 'section-tile' },
    h('div', { class: 'small muted' }, label), h('div', { class: `total-num score ${cls}`, style: 'font-size:28px' }, value));
  const link = (d) => h('a', { href: `#/dialog/${encodeURIComponent(d.id)}` }, d.source_id);
  const maxBucket = Math.max(1, ...a.distribution.map((b) => b.count));
  const crit = [...a.criteria].sort((x, y) => (x.critical - y.critical) || ((x.met_pct ?? 101) - (y.met_pct ?? 101)));

  main.replaceChildren(
    h('div', { class: 'row', style: 'margin-bottom:12px' }, h('h1', { style: 'margin:0' }, 'Аналитика'), sel,
      h('span', { class: 'muted small' }, `оценено ${a.evaluated} из ${a.dialogs} диалогов`)),
    a.evaluated === 0 ? h('div', { class: 'panel empty' }, 'Набор ещё не оценён. Нажмите «Оценить» на странице «Наборы».') : h('div', {},
      h('div', { class: 'sections', style: 'margin-bottom:12px' },
        kpi('Средний итог', fmt(a.avg_total), scoreClass(a.avg_total)),
        kpi('Медиана', fmt(a.median_total), scoreClass(a.median_total)),
        kpi('Грубые ошибки', String(a.critical.length), a.critical.length ? 'bad' : 'good'),
        kpi('Открытые апелляции', String(a.open_appeals)),
        kpi('Исправлено проверяющими', String(a.changed_by_reviewers))),
      h('div', { class: 'dialog-grid' },
        h('div', { class: 'panel' },
          h('h2', {}, 'Где операторы теряют баллы'),
          h('p', { class: 'small muted' }, '«Выполнено» — доля применимых диалогов, где критерий получил полный балл. N/A не учитывается.'),
          h('table', { class: 'list' },
            h('thead', {}, h('tr', {}, h('th', {}, 'Критерий'), h('th', {}, 'Выполнено'), h('th', {}, 'Средний балл'), h('th', {}, 'N/A'))),
            h('tbody', {}, crit.map((c) => h('tr', {},
              h('td', {}, c.title, c.review ? h('span', { class: 'badge mid', style: 'margin-left:6px' }, `проверка: ${c.review}`) : ''),
              h('td', { style: 'min-width:140px' }, c.critical
                ? h('span', { class: `badge ${c.applicable - c.met ? 'bad' : 'good'}` }, `нарушений: ${c.applicable - c.met}`)
                : c.met_pct == null ? '—' : h('div', {}, `${fmt(c.met_pct, 0)}% (${c.met}/${c.applicable})`,
                  h('div', { class: 'bar' }, h('i', { style: `width:${c.met_pct}%` })))),
              h('td', {}, c.avg_points_pct == null ? '—' : `${fmt(c.avg_points_pct, 0)}%`),
              h('td', { class: 'muted' }, c.na)))))),
        h('div', {},
          h('div', { class: 'panel' }, h('h2', {}, 'Распределение итогов'),
            a.distribution.map((b) => h('div', { class: 'row', style: 'margin:4px 0' },
              h('span', { style: 'width:70px' }, b.label),
              h('div', { class: 'bar', style: 'flex:1;height:14px' }, h('i', { style: `width:${100 * b.count / maxBucket}%` })),
              h('span', { class: 'small', style: 'width:30px;text-align:right' }, b.count)))),
          h('div', { class: 'panel' }, h('h2', {}, 'По языкам'),
            h('table', { class: 'list' }, h('tbody', {}, a.languages.map((l) => h('tr', {},
              h('td', {}, l.language), h('td', {}, `${l.dialogs} диалогов`),
              h('td', {}, h('span', { class: `score ${scoreClass(l.avg_total)}` }, fmt(l.avg_total)))))))),
          h('div', { class: 'panel' }, h('h2', {}, 'Требуют внимания'),
            a.critical.length ? h('div', { style: 'margin-bottom:8px' }, h('b', {}, 'Грубые ошибки: '),
              a.critical.map((d, i) => [i ? ', ' : '', link(d)])) : '',
            h('div', {}, h('b', {}, 'Самые низкие итоги: '),
              a.worst.map((d, i) => [i ? ', ' : '', link(d), ` (${fmt(d.total)})`])))))));
}
