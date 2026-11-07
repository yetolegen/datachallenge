import { api } from '../api.js';
import { act, h, runProgress, toast } from '../dom.js';

const clone = (x) => JSON.parse(JSON.stringify(x));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export async function render(main) {
  const [info, datasets] = await Promise.all([api('/checklist'), api('/datasets')]);
  let base = info.active;            // version the draft started from
  let draft = clone(base.checklist);
  const activeId = info.active_version_id;

  const listBox = h('div');
  const side = h('div', { class: 'panel cl-side' });
  const errorsBox = h('div');
  const changedBox = h('div', { class: 'small' });
  const weightBox = h('div', { class: 'small' });
  let vtimer;

  // criteria whose prompt changes vs the base version (shared rules feed every prompt)
  function changedIds() {
    const old = Object.fromEntries(base.checklist.criteria.map((c) => [c.id, c]));
    const rulesChanged = !same(base.checklist.general_rules, draft.general_rules);
    return draft.criteria.filter((c) => rulesChanged || !same(old[c.id], c)).map((c) => c.id);
  }

  function refreshSide() {
    const w = draft.criteria.filter((c) => !c.is_critical).reduce((s, c) => s + (Number(c.weight) || 0), 0);
    weightBox.replaceChildren(`Сумма весов: ${w}. `, h('span', { class: 'muted' },
      w === 100 ? 'Максимум — 100 баллов.' : 'Итог всё равно приводится к 100 баллам пропорционально весам.'));
    const removed = base.checklist.criteria.filter((c) => !draft.criteria.some((d) => d.id === c.id)).map((c) => c.title);
    const ch = changedIds();
    changedBox.replaceChildren(
      ch.length || removed.length || !same(draft, base.checklist)
        ? h('div', {}, h('b', {}, 'Изменения не сохранены. '),
          ch.length ? `Будут пересчитаны только: ${ch.map((id) => draft.criteria.find((c) => c.id === id).title).join(', ')}.` : '',
          removed.length ? ` Удалены: ${removed.join(', ')}.` : '')
        : h('span', { class: 'muted' }, `Совпадает с версией v${base.version}.`));
    listBox.querySelectorAll('[data-cid]').forEach((el) => { el.hidden = !ch.includes(el.dataset.cid); });
    clearTimeout(vtimer);
    vtimer = setTimeout(async () => {
      try {
        const { errors } = await api('/checklist/validate', { method: 'POST', body: { checklist: draft } });
        errorsBox.replaceChildren(errors.length
          ? h('div', { class: 'error-box' }, h('b', {}, 'Исправьте перед сохранением:'), h('ul', {}, errors.map((e) => h('li', {}, e))))
          : h('div', { class: 'small', style: 'color:var(--good)' }, 'Чек-лист корректен.'));
      } catch (e) { errorsBox.replaceChildren(h('div', { class: 'error-box' }, e.message)); }
    }, 300);
  }

  // bound inputs edit the draft in place without a repaint, so focus is kept
  const bind = (obj, key, el, cast = (v) => v) => {
    el.addEventListener('input', () => { obj[key] = cast(el.value); refreshSide(); });
    return el;
  };
  const text = (label, obj, key, wide = false, area = false) => h('label', { class: `field ${wide ? 'wide' : ''}` }, label,
    bind(obj, key, area ? h('textarea', { value: obj[key] || '' }) : h('input', { type: 'text', value: obj[key] || '' })));
  const num = (label, obj, key) => h('label', { class: 'field' }, label,
    bind(obj, key, h('input', { type: 'number', min: 0, step: 'any', value: obj[key] }), (v) => (v === '' ? null : Number(v))));
  const check = (label, obj, key, after) => {
    const el = h('input', { type: 'checkbox', checked: !!obj[key] });
    el.addEventListener('change', () => { obj[key] = el.checked; refreshSide(); if (after) after(); });
    return h('label', { class: 'check' }, el, label);
  };
  const select = (label, obj, key, opts, after) => {
    const el = h('select', { value: obj[key] }, opts.map(([v, t]) => h('option', { value: v }, t)));
    el.addEventListener('change', () => { obj[key] = el.value; refreshSide(); if (after) after(); });
    return h('label', { class: 'field' }, label, el);
  };

  function stepsEditor(c) {
    const box = h('div', { class: 'steps-edit wide' });
    const paintSteps = () => box.replaceChildren(
      h('div', { class: 'small muted' }, 'Ступени шкалы (верхняя даёт полный вес)'),
      c.scale.steps.map((s, i) => h('div', { class: 'step' },
        bind(s, 'value', h('input', { type: 'number', value: s.value, 'aria-label': 'Балл ступени' }), Number),
        bind(s, 'label', h('input', { type: 'text', value: s.label, 'aria-label': 'Описание ступени' })),
        h('button', { class: 'btn small danger', type: 'button', onclick: () => { c.scale.steps.splice(i, 1); paintSteps(); refreshSide(); } }, 'Удалить'))),
      h('button', { class: 'btn small', type: 'button', onclick: () => { c.scale.steps.push({ value: 0, label: '' }); paintSteps(); refreshSide(); } }, 'Добавить ступень'));
    paintSteps();
    return box;
  }

  function critCard(c, i) {
    const body = h('div', { class: 'body' });
    const paintBody = () => {
      if (c.answer_type === 'scale' && !c.scale) c.scale = { steps: [{ value: 10, label: '' }, { value: 0, label: '' }] };
      body.replaceChildren(
        text('Название', c, 'title'),
        text('ID (латиницей, уникальный)', c, 'id'),
        select('Секция', c, 'section_id', draft.sections.map((s) => [s.id, s.title])),
        select('Тип ответа', c, 'answer_type', [['binary', 'Да / Нет'], ['scale', 'Шкала']], paintBody),
        num('Вес', c, 'weight'),
        select('Доказательство', c, 'evidence_speaker', [['operator', 'Реплика оператора'], ['any', 'Любая реплика или системное сообщение']]),
        h('div', { class: 'row' }, check('N/A разрешён', c, 'na_allowed', paintBody), check('Грубая ошибка (без баллов, флаг)', c, 'is_critical')),
        c.na_allowed && text('Когда ставить N/A', c, 'na_rule', true, true),
        c.answer_type === 'binary' && text('Когда «Да»', c, 'yes_rule', true, true),
        c.answer_type === 'binary' && text('Когда «Нет»', c, 'no_rule', true, true),
        c.answer_type === 'scale' && stepsEditor(c),
        text('Пояснение для модели (уточнения, примеры)', c, 'instructions', true, true),
        h('div', { class: 'row wide' },
          h('button', { class: 'btn small', type: 'button', disabled: i === 0, onclick: () => move(i, -1) }, 'Выше'),
          h('button', { class: 'btn small', type: 'button', disabled: i === draft.criteria.length - 1, onclick: () => move(i, 1) }, 'Ниже'),
          h('button', { class: 'btn small', type: 'button', onclick: () => preview(c) }, 'Предпросмотр промпта'),
          h('span', { class: 'spacer' }),
          h('button', { class: 'btn small danger', type: 'button', onclick: () => { draft.criteria.splice(i, 1); paint(); } }, 'Удалить критерий')));
    };
    paintBody();
    return h('details', { class: 'crit-edit' },
      h('summary', {}, h('b', {}, `${i + 1}. `), c.title || 'Без названия',
        h('span', { class: 'badge' }, c.answer_type === 'scale' ? 'шкала' : 'да/нет'),
        h('span', { class: 'badge' }, c.is_critical ? 'без баллов' : `вес ${c.weight}`),
        c.na_allowed && h('span', { class: 'badge' }, 'N/A'),
        h('span', { class: 'changed-dot', 'data-cid': c.id, title: 'Изменён', hidden: true })),
      body);
  }

  function move(i, d) {
    const [c] = draft.criteria.splice(i, 1);
    draft.criteria.splice(i + d, 0, c);
    paint();
  }

  async function preview(c) {
    try {
      const p = await api('/checklist/preview', { method: 'POST', body: { checklist: draft, criterion_id: c.id } });
      const dlg = h('dialog', {},
        h('div', { class: 'row' }, h('h2', { style: 'margin:0' }, `Промпт: ${c.title}`), h('span', { class: 'spacer' }),
          h('button', { class: 'btn', type: 'button', onclick: () => dlg.close() }, 'Закрыть')),
        h('p', { class: 'small muted' }, `На примере диалога ${p.conversation_id || '—'}. Транскрипт идёт первым и кэшируется, блок критерия — последним.`),
        h('h3', {}, 'Системный промпт'), h('pre', { class: 'prompt' }, p.system),
        h('h3', {}, 'Сообщение'), h('pre', { class: 'prompt' }, p.user));
      dlg.addEventListener('close', () => dlg.remove());
      document.body.append(dlg);
      dlg.showModal();
    } catch (e) { toast(e.message, 'error'); }
  }

  function paint() {
    listBox.replaceChildren(
      h('div', { class: 'panel', style: 'margin-bottom:12px' },
        h('h2', {}, 'Общие правила'),
        h('div', { class: 'row', style: 'align-items:end' },
          text('Название чек-листа', draft, 'name'),
          num('Максимум баллов', draft.scoring, 'scale_max'),
          num('Знаков после запятой', draft.scoring, 'rounding_digits')),
        h('div', { style: 'margin-top:8px' }, text('Правила для всех критериев (попадают в промпт)', draft, 'general_rules', true, true)),
        h('div', { class: 'small muted', style: 'margin-top:8px' }, 'Секции: ',
          draft.sections.map((s) => h('input', { type: 'text', value: s.title, style: 'margin:2px', 'aria-label': 'Название секции',
            oninput: (e) => { s.title = e.target.value; refreshSide(); } })),
          h('button', { class: 'btn small', type: 'button', onclick: () => {
            draft.sections.push({ id: `s${Date.now().toString(36)}`, title: 'Новая секция' });
            paint();
          } }, 'Добавить секцию'))),
      draft.criteria.map(critCard),
      h('button', { class: 'btn', type: 'button', onclick: () => {
        draft.criteria.push({ id: `crit_${Date.now().toString(36)}`, section_id: draft.sections[0]?.id, title: 'Новый критерий',
          answer_type: 'binary', weight: 10, na_allowed: false, na_rule: '', yes_rule: '', no_rule: '', instructions: '',
          scale: null, is_critical: false, evidence_speaker: 'operator' });
        paint();
        listBox.querySelectorAll('details.crit-edit')[draft.criteria.length - 1].open = true;
      } }, 'Добавить критерий'));
    refreshSide();
  }

  // ---------- side: versions, save, apply ----------
  const versionSel = h('select', { 'aria-label': 'Версия чек-листа', value: String(base.id) },
    info.versions.map((v) => h('option', { value: String(v.id) },
      `v${v.version}${v.id === activeId ? ' (активная)' : ''}${v.note ? ` — ${v.note}` : ''}`)));
  versionSel.addEventListener('change', async () => {
    base = await api(`/checklist/versions/${versionSel.value}`);
    draft = clone(base.checklist);
    paint();
  });
  const activateBtn = h('button', { class: 'btn small', type: 'button' }, 'Сделать активной');
  activateBtn.onclick = () => act(activateBtn, async () => {
    await api('/checklist/activate', { method: 'POST', body: { version_id: Number(versionSel.value) } });
    toast('Версия активирована');
    window.dispatchEvent(new Event('cqa:refresh'));
  });
  const note = h('input', { type: 'text', placeholder: 'Что изменено (необязательно)', 'aria-label': 'Комментарий к версии' });
  const saveBtn = h('button', { class: 'btn primary', type: 'button' }, 'Сохранить версию');
  saveBtn.onclick = () => act(saveBtn, async () => {
    const r = await api('/checklist/versions', { method: 'POST', body: { checklist: draft, note: note.value } });
    toast(`Версия v${r.version} сохранена и активна`);
    window.dispatchEvent(new Event('cqa:refresh'));
  });
  const dsSel = h('select', { 'aria-label': 'Набор' }, datasets.map((d) => h('option', { value: d.group_id }, `${d.group_id} (${d.conversations})`)));
  const progressBox = h('div');
  const applyBtn = h('button', { class: 'btn primary', type: 'button', disabled: !datasets.length }, 'Применить к набору');
  applyBtn.onclick = () => act(applyBtn, async () => {
    if (!same(draft, base.checklist)) throw new Error('Сначала сохраните версию — применяется сохранённый чек-лист.');
    if (base.id !== activeId) throw new Error('Применяется активная версия. Сделайте выбранную версию активной.');
    const run = await api('/runs', { method: 'POST', body: { dataset_group_id: dsSel.value, checklist_version_id: base.id } });
    progressBox.replaceChildren(runProgress(run, (r) => toast(`Готово: новых вызовов ${r.new_calls}, из кэша ${r.cache_hits}`)));
  });

  side.append(
    h('h2', {}, 'Версии'),
    h('div', { class: 'row' }, versionSel, activateBtn),
    h('hr'), weightBox, changedBox, h('div', { style: 'margin:8px 0' }, errorsBox),
    h('div', { class: 'inline-form' }, note, saveBtn),
    h('hr'),
    h('h2', {}, 'Применить к набору'),
    datasets.length ? h('div', { class: 'inline-form' }, dsSel, applyBtn,
      h('div', { class: 'small muted' }, 'Неизменённые критерии берутся из кэша, модель вызывается только для новых.'))
      : h('div', { class: 'small muted' }, 'Сначала загрузите набор на странице «Наборы».'),
    progressBox);

  main.replaceChildren(h('h1', {}, 'Чек-лист'), h('div', { class: 'cl-layout' }, listBox, side));
  paint();
}
