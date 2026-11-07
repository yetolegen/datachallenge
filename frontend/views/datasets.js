import { api, apiUrl } from '../api.js';
import { STATUS, act, h, runProgress, toast } from '../dom.js';

function reportBox(rep) {
  return h('div', { class: rep.errors.length ? 'warn-box' : 'panel', style: 'margin-top:10px' },
    h('b', {}, `Набор ${rep.group_id}: `),
    `новых ${rep.imported}, обновлено ${rep.updated}, без изменений ${rep.unchanged}, всего в наборе ${rep.total_in_set}.`,
    rep.errors.length > 0 && h('div', {}, h('b', {}, `Ошибки в ${rep.errors.length} записях:`),
      h('ul', {}, rep.errors.map((e) => h('li', {}, `#${e.record} ${e.id || ''}: ${e.error}`)))),
    rep.warnings.length > 0 && h('details', {}, h('summary', {}, `Предупреждения: ${rep.warnings.length}`),
      h('ul', { class: 'small' }, rep.warnings.map((w) => h('li', {}, w)))));
}

export async function render(main) {
  const [datasets, runs, settings] = await Promise.all([api('/datasets'), api('/runs'), api('/settings')]);
  const out = h('div');
  const afterImport = async (rep) => {
    await render(main);
    main.querySelector('#import-report').replaceChildren(reportBox(rep));
  };

  const gid = h('input', { type: 'text', placeholder: 'например, DEV', 'aria-label': 'group_id' });
  const loadBtn = h('button', { class: 'btn primary', type: 'submit' }, 'Загрузить');
  const loadForm = h('form', { class: 'row', onsubmit: (e) => {
    e.preventDefault();
    act(loadBtn, async () => {
      if (!gid.value.trim()) throw new Error('Введите group_id.');
      const rep = await api('/datasets/import', { method: 'POST', body: { group_id: gid.value.trim() } });
      toast(`Набор ${rep.group_id} загружен`);
      await afterImport(rep);
    });
  } }, gid, loadBtn);

  const file = h('input', { type: 'file', accept: '.json,application/json', 'aria-label': 'JSON-файл' });
  const fileGid = h('input', { type: 'text', placeholder: 'group_id (необязательно)', 'aria-label': 'group_id для файла' });
  const upBtn = h('button', { class: 'btn', type: 'submit' }, 'Загрузить файл');
  const upForm = h('form', { class: 'row', onsubmit: (e) => {
    e.preventDefault();
    act(upBtn, async () => {
      if (!file.files[0]) throw new Error('Выберите JSON-файл.');
      const fd = new FormData();
      fd.append('file', file.files[0]);
      fd.append('group_id', fileGid.value.trim());
      const rep = await api('/datasets/upload', { method: 'POST', form: fd });
      toast(`Файл загружен в набор ${rep.group_id}`);
      await afterImport(rep);
    });
  } }, file, fileGid, upBtn);

  const runRows = runs.map((r) => {
    const cell = h('td', {}, r.status === 'running'
      ? runProgress(r, () => render(main))
      : `${STATUS[r.status] || r.status} · ${r.done}/${r.total} · из кэша ${r.cache_hits} · новых ${r.new_calls} · требуют проверки ${r.needs_review}`);
    return h('tr', {}, h('td', {}, `#${r.id}`), h('td', {}, r.dataset_group_id || `${r.conversations} диалог`),
      h('td', {}, r.version ? `v${r.version}` : '—'), cell,
      h('td', { class: 'small muted hide-narrow' }, (r.started_at || '').replace('T', ' ').slice(0, 19)));
  });

  main.replaceChildren(
    h('h1', {}, 'Наборы'),
    h('div', { class: 'panel' },
      h('h2', {}, 'Загрузить по group_id'),
      h('p', { class: 'small muted' }, 'Данные запрашиваются через сервер у API организаторов. Для контрольного набора введите group_id, который сообщат организаторы.'),
      !settings.api_template_set && h('div', { class: 'warn-box', style: 'margin-bottom:8px' },
        'Адрес API организаторов не задан: добавьте CQA_API_URL_TEMPLATE в .env. Пока можно загрузить JSON-файл.'),
      loadForm,
      h('h2', { style: 'margin-top:16px' }, 'Или загрузить JSON-файл'),
      h('p', { class: 'small muted' }, 'Подходит ответ API целиком, массив записей или одна запись.'),
      upForm,
      h('div', { id: 'import-report' })),
    h('div', { class: 'panel' },
      h('h2', {}, 'Загруженные наборы'),
      datasets.length === 0 ? h('div', { class: 'empty' }, 'Наборов пока нет. Загрузите DEV выше.')
        : h('table', { class: 'list' },
          h('thead', {}, h('tr', {}, h('th', {}, 'Набор'), h('th', {}, 'Диалогов'), h('th', { class: 'hide-narrow' }, 'Загружен'), h('th', {}, ''))),
          h('tbody', {}, datasets.map((d) => {
            const evalBtn = h('button', { class: 'btn primary small', type: 'button' }, 'Оценить');
            evalBtn.onclick = () => act(evalBtn, async () => {
              const run = await api('/runs', { method: 'POST', body: { dataset_group_id: d.group_id } });
              out.replaceChildren(h('div', { class: 'panel' }, h('h2', {}, `Оценка набора ${d.group_id}`),
                runProgress(run, () => { toast(`Набор ${d.group_id} оценён`); render(main); })));
            });
            return h('tr', {},
              h('td', {}, h('a', { href: '#/dialogs' }, d.group_id)),
              h('td', {}, d.conversations),
              h('td', { class: 'hide-narrow small muted' }, (d.imported_at || '').replace('T', ' ').slice(0, 19)),
              h('td', {}, h('div', { class: 'row' }, evalBtn,
                h('a', { class: 'btn small', href: apiUrl(`/export/${encodeURIComponent(d.group_id)}.csv`) }, 'Скачать CSV'))));
          })))),
    out,
    !runs.length ? '' : h('div', { class: 'panel' }, h('h2', {}, 'Запуски оценки'),
      h('table', { class: 'list' },
        h('thead', {}, h('tr', {}, h('th', {}, '№'), h('th', {}, 'Набор'), h('th', {}, 'Чек-лист'), h('th', {}, 'Прогресс'),
          h('th', { class: 'hide-narrow' }, 'Начат'))),
        h('tbody', {}, runRows))),
    h('p', { class: 'small muted' }, `Оценка идёт моделью ${settings.model} (${settings.provider}). Повторный запуск берёт готовые результаты из кэша.`));
}
