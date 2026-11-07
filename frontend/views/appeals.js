import { api } from '../api.js';
import { SPEAKER, act, answerLabel, answerOptions, getRole, h, toast } from '../dom.js';

// reviewer decision form; used here and inline on the dialog card
export function decisionForm(appeal, onDone) {
  const c = appeal.criterion;
  const opts = answerOptions(c);
  const reason = h('textarea', { placeholder: 'Причина решения (обязательно)', 'aria-label': 'Причина решения' });
  const pick = h('select', { 'aria-label': 'Новая оценка', hidden: true },
    opts.map((o, i) => h('option', { value: String(i) }, o.label)));
  const confirm = h('button', { class: 'btn', type: 'button' }, 'Подтвердить');
  const change = h('button', { class: 'btn primary', type: 'button' }, 'Изменить');
  let changing = false;

  const send = (btn, action) => act(btn, async () => {
    if (!reason.value.trim()) throw new Error('Укажите причину решения.');
    const o = opts[Number(pick.value)];
    await api(`/appeals/${appeal.id}/decision`, {
      method: 'POST',
      body: { action, reason: reason.value, ...(action === 'changed' ? { new_verdict: o.verdict, new_value: o.value ?? null } : {}) },
    });
    onDone();
  });
  confirm.onclick = () => send(confirm, 'upheld');
  change.onclick = () => {
    if (!changing) {  // first click reveals the value picker
      changing = true;
      pick.hidden = false;
      change.textContent = 'Сохранить изменение';
      pick.focus();
      return;
    }
    send(change, 'changed');
  };
  return h('div', { class: 'inline-form' },
    h('div', { class: 'small muted' }, 'Решение проверяющего'),
    pick, reason, h('div', { class: 'row' }, confirm, change));
}

export async function render(main, route) {
  const status = route.q.get('status') ?? 'open';
  const list = await api(`/appeals?status=${status}`);
  const reviewer = getRole() === 'reviewer';
  const tab = (s, label) => h('a', { href: `#/appeals?status=${s}`, class: `btn small ${s === status ? 'primary' : ''}` }, label);

  main.replaceChildren(
    h('div', { class: 'row', style: 'margin-bottom:12px' }, h('h1', { style: 'margin:0' }, 'Апелляции'), h('span', { class: 'spacer' }),
      tab('open', 'Открытые'), tab('resolved', 'Рассмотренные')),
    reviewer || status !== 'open' ? '' : h('div', { class: 'warn-box', style: 'margin-bottom:12px' },
      'Решения по апелляциям принимает проверяющий. Переключите роль в шапке на «Проверяющий».'),
    list.length === 0
      ? h('div', { class: 'panel empty' }, status === 'open'
        ? 'Открытых апелляций нет. Оператор может подать апелляцию в карточке диалога.'
        : 'Рассмотренных апелляций пока нет.')
      : h('div', {}, list.map((a) => card(a, reviewer))));
}

function card(a, reviewer) {
  const c = a.criterion || { title: a.criterion_id, answer_type: 'binary' };
  const orig = answerLabel(c, a.verdict, a.value, a.result_status);
  const cited = new Set(a.evidence_ids);
  const anchor = a.evidence_ids[0] || (a.checked_range && a.checked_range.from);
  const link = `#/dialog/${encodeURIComponent(a.conversation_id)}${anchor ? `?u=${anchor}` : ''}`;
  const right = a.status === 'open'
    ? (reviewer ? decisionForm(a, () => { toast('Решение сохранено, итог пересчитан'); window.dispatchEvent(new Event('cqa:refresh')); }) : '')
    : h('div', { class: 'history' },
      h('div', {}, h('b', {}, a.action === 'changed' ? 'Изменено на: ' : 'Оценка подтверждена'),
        a.action === 'changed' ? answerLabel(c, a.new_verdict, a.new_value, 'ok').text : ''),
      h('div', { class: 'small' }, h('b', {}, 'Причина: '), a.reason));

  return h('div', { class: 'panel' },
    h('div', { class: 'row' },
      h('h2', { style: 'margin:0' }, c.title),
      h('span', { class: `answer ${orig.cls}` }, `Оценка ИИ: ${orig.text}`),
      h('span', { class: 'spacer' }),
      h('a', { href: link }, `Открыть диалог ${a.conversation_id}`)),
    h('div', { class: 'appeal', style: 'margin-top:10px' },
      h('div', {},
        h('div', { class: 'small muted' }, 'Обоснование ИИ'),
        h('div', { class: 'explain' }, a.explanation),
        h('div', { class: 'small muted' }, 'Комментарий оператора'),
        h('div', { class: 'explain' }, a.comment),
        h('div', { class: 'small muted' }, 'Фрагмент диалога'),
        h('div', { class: 'excerpt' }, a.excerpt.map((u) => h('div', { class: `utt ${u.speaker} ${cited.has(u.uid) ? 'hl' : ''}` },
          h('div', { class: 'meta' }, u.uid),
          h('div', {}, h('span', { class: 'who' }, SPEAKER[u.speaker] || u.speaker), h('div', { class: 'text' }, u.text)))))),
      h('div', {}, right)));
}
