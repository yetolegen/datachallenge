import { api, apiUrl } from '../api.js';
import { parseHash, refreshAppealCount } from '../app.js';
import { CHANNEL, SPEAKER, STATUS, act, answerLabel, fmt, getRole, h, runProgress, scoreClass, toast } from '../dom.js';
import { decisionForm } from './appeals.js';

const CONF = { high: 'высокая', medium: 'средняя', low: 'низкая' };
const reduceMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const mmss = (s) => (s == null ? '' : `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`);

export async function render(main, route) {
  const id = route.parts[1];
  const evalId = route.q.get('eval');
  const data = await api(`/conversations/${encodeURIComponent(id)}${evalId ? `?eval=${evalId}` : ''}`);
  const { conversation: conv, utterances: utts, view, evaluations } = data;
  const byUid = Object.fromEntries(utts.map((u) => [u.uid, u]));
  const isCall = !!conv.audio_url;
  const role = getRole();
  const reload = async () => {
    const y = window.scrollY;
    await render(main, parseHash());
    window.scrollTo(0, y);
    refreshAppealCount();
  };

  // ---------- transcript + audio ----------
  let audio = null;
  let correction = conv.audio_offset_correction_sec || 0;
  const seek = (u, play = true) => {
    if (!audio || !u || u.offset_sec == null) return;
    audio.currentTime = Math.max(0, u.offset_sec + correction);
    if (play) audio.play().catch(() => {});
  };
  const uttEls = {};
  const list = h('div', {}, utts.map((u) => {
    uttEls[u.uid] = h('div', { class: `utt ${u.speaker}`, id: `utt-${u.uid}` },
      h('div', { class: 'meta' }, u.uid,
        isCall && h('div', {}, h('button', { class: 'seek', type: 'button', title: 'Прослушать с этого места',
          'aria-label': `Прослушать запись с реплики ${u.uid}`, onclick: () => seek(u) }, '▶'))),
      h('div', {},
        h('span', { class: 'who' }, SPEAKER[u.speaker] || u.speaker), ' ',
        h('span', { class: 'mono muted', title: u.raw_timestamp }, isCall ? mmss(u.offset_sec) : u.raw_timestamp),
        h('div', { class: 'text' }, u.text)));
    return uttEls[u.uid];
  }));

  function focusUtts(uids, range, updateUrl = true) {
    Object.values(uttEls).forEach((e) => e.classList.remove('hl', 'in-range'));
    if (range && byUid[range.from] && byUid[range.to]) {
      const a = byUid[range.from].idx, b = byUid[range.to].idx;
      utts.forEach((u) => { if (u.idx >= a && u.idx <= b) uttEls[u.uid].classList.add('in-range'); });
    }
    uids.forEach((x) => uttEls[x] && uttEls[x].classList.add('hl'));
    const target = uttEls[uids[0]] || (range && uttEls[range.from]);
    if (target) target.scrollIntoView({ block: 'center', behavior: reduceMotion() ? 'auto' : 'smooth' });
    const anchor = uids[0] || (range && range.from);
    if (updateUrl && anchor) {
      const q = new URLSearchParams({ ...(evalId ? { eval: evalId } : {}), u: anchor });
      history.replaceState(null, '', `#/dialog/${encodeURIComponent(id)}?${q}`);
    }
    if (isCall && anchor) seek(byUid[anchor], false);
  }

  let audioBar = null;
  if (isCall) {
    const note = h('div', { class: 'small muted' }, 'Нажмите ▶ у реплики или ссылку в обосновании, чтобы перейти к фрагменту записи.');
    audio = h('audio', { controls: true, preload: 'none', src: apiUrl(`/audio/${encodeURIComponent(id)}`) });
    audio.addEventListener('error', () => {
      note.textContent = 'Не удалось загрузить запись. Ссылка организаторов могла устареть — обновите страницу.';
    });
    const offIn = h('input', { type: 'number', step: '0.5', value: correction, style: 'width:90px', 'aria-label': 'Сдвиг записи в секундах' });
    const save = h('button', { class: 'btn small', type: 'button' }, 'Сохранить сдвиг');
    save.onclick = () => act(save, async () => {
      const r = await api(`/conversations/${encodeURIComponent(id)}`, { method: 'PATCH', body: { audio_offset_correction_sec: offIn.value } });
      correction = r.audio_offset_correction_sec;
      toast('Сдвиг записи сохранён');
    });
    audioBar = h('div', { class: 'audio-bar' }, audio, note,
      h('div', { class: 'row small muted' }, 'Сдвиг записи относительно транскрипта, сек:', offIn, save));
  }

  // ---------- results ----------
  const evalBox = h('div', { class: 'panel' });
  const runBtn = h('button', { class: 'btn primary', type: 'button' }, view ? 'Оценить заново' : 'Оценить диалог');
  runBtn.onclick = () => act(runBtn, async () => {
    const run = await api('/evaluations', { method: 'POST', body: { conversation_id: id } });
    evalBox.replaceChildren(h('h2', {}, 'Идёт оценка'), runProgress(run, () => { toast('Оценка готова'); reload(); }));
  });
  const evalSel = evaluations.length > 1 && h('select', {
    'aria-label': 'Версия чек-листа', value: String(view ? view.evaluation.id : ''),
    onchange: (e) => { location.hash = `#/dialog/${encodeURIComponent(id)}?eval=${e.target.value}`; },
  }, evaluations.map((e) => h('option', { value: String(e.id) }, `Чек-лист v${e.version}`)));
  // previous/next in the order of the last dialog list the reviewer looked at
  let order = [];
  try { order = JSON.parse(sessionStorage.getItem('cqa.list') || '[]'); } catch { /* storage blocked */ }
  const pos = order.indexOf(id);
  const navTo = (cid) => h('a', { class: 'btn small', href: `#/dialog/${encodeURIComponent(cid)}` });
  const prev = pos > 0 && navTo(order[pos - 1]);
  const next = pos >= 0 && pos < order.length - 1 && navTo(order[pos + 1]);
  if (prev) prev.textContent = '‹ Предыдущий';
  if (next) next.textContent = 'Следующий ›';
  const head = h('div', { class: 'card-head' },
    h('a', { href: '#/dialogs', class: 'small' }, '← Диалоги'),
    prev || '', next || '', pos >= 0 ? h('span', { class: 'small muted' }, `${pos + 1} из ${order.length}`) : '',
    h('h1', {}, conv.source_id),
    h('span', { class: 'badge' }, conv.language),
    h('span', { class: 'badge' }, CHANNEL[conv.channel] || conv.channel),
    h('span', { class: 'badge' }, `Набор ${conv.dataset_group_id}`),
    conv.revision > 1 && h('span', { class: 'badge mid' }, `Ревизия ${conv.revision}`),
    h('span', { class: 'spacer' }), evalSel, runBtn);

  if (!view) {
    evalBox.append(h('div', { class: 'empty' }, 'Диалог ещё не оценён. Нажмите «Оценить диалог» — будет применён активный чек-лист.'));
  } else {
    const b = view.breakdown;
    const tot = b.total;
    evalBox.append(
      !b.critical_failed ? '' : h('div', { class: 'critical-banner', role: 'alert' },
        'Грубая ошибка: ', view.items.filter((i) => b.critical_ids.includes(i.criterion.id)).map((i) => i.criterion.title).join(', '),
        '. Отмечена отдельно, на итог не влияет.'),
      h('div', { class: 'total-box' },
        h('div', { class: `total-num score ${scoreClass(tot)}` }, tot == null ? 'не применимо' : fmt(tot),
          tot != null && h('small', {}, ` / ${b.scale_max}`)),
        h('div', {},
          h('div', { class: 'row' },
            h('span', { class: 'badge accent' }, `Чек-лист v${view.checklist_version.version}`),
            h('span', { class: `badge ${view.evaluation.status === 'needs_review' ? 'mid' : ''}` },
              STATUS[view.evaluation.status] || view.evaluation.status),
            b.provisional && h('span', { class: 'badge mid', title: 'Есть критерии, требующие проверки — они не учтены в итоге' },
              'Итог предварительный')),
          view.original_total !== tot && h('div', { class: 'small', style: 'margin-top:6px' },
            'Оценка ИИ: ', h('b', {}, fmt(view.original_total)), ' → после решений проверяющего: ', h('b', {}, fmt(tot))),
          h('div', { class: 'small muted', style: 'margin-top:4px' },
            `Модель: ${view.models.join(', ') || '—'} · стоимость оценки $${fmt(view.cost, 4)}`))),
      !b.sections.length ? '' : h('div', { class: 'sections' }, b.sections.map((s) => h('div', { class: 'section-tile' },
        h('div', { class: 'small muted' }, s.title),
        h('div', { class: 'score' }, s.points == null ? 'не применимо' : `${fmt(s.points)} из ${fmt(s.max_points)}`),
        h('div', { class: 'bar' }, h('i', { style: `width:${s.percent ?? 0}%` }))))),
      h('div', { style: 'margin-top:14px' }, view.items.map(critRow)));
  }

  function critRow(it) {
    const c = it.criterion, r = it.result, s = it.score, f = it.final;
    if (!r) {
      return h('div', { class: 'crit' }, h('div', { class: 'crit-head' }, h('h3', {}, c.title), h('span', { class: 'answer na' }, 'Не оценён')));
    }
    const lab = answerLabel(c, f.verdict, f.value, f.status);
    let pts;
    if (c.is_critical) pts = 'без баллов';
    else if (f.verdict === 'na') pts = 'исключён, вес перераспределён';
    else if (s && s.points != null) pts = `${fmt(s.points)} из ${fmt(s.max_points)}`;
    else pts = 'не учтён в итоге';
    const weight = c.is_critical ? '' : (s && s.effective_weight != null && s.effective_weight !== c.weight
      ? `вес ${c.weight} → ${fmt(s.effective_weight)}` : `вес ${c.weight}`);
    const openAppeal = it.appeals.find((a) => a.status === 'open');
    const actions = h('div');
    if (role === 'operator' && !openAppeal) {
      const btn = h('button', { class: 'btn small', type: 'button' }, 'Подать апелляцию');
      btn.onclick = () => { btn.hidden = true; actions.append(appealForm(r, () => { btn.hidden = false; })); };
      actions.append(btn);
    }
    if (role === 'reviewer' && openAppeal) {
      actions.append(decisionForm({ ...openAppeal, criterion: c }, () => { toast('Решение сохранено, итог пересчитан'); reload(); }));
    }
    return h('div', { class: 'crit', id: `crit-${c.id}` },
      h('div', { class: 'crit-head' },
        h('h3', {}, c.title),
        h('span', { class: `answer ${lab.cls}` }, lab.text),
        h('span', { class: 'points muted small' }, pts, weight && ` · ${weight}`)),
      h('div', { class: 'explain' }, r.explanation),
      f.verdict === 'na' && r.na_reason && h('div', { class: 'small' }, h('b', {}, 'Причина N/A: '), r.na_reason),
      h('div', { class: 'chips' },
        r.evidence_ids.map((uid) => h('button', { class: 'chip', type: 'button', onclick: () => focusUtts([uid]),
          title: byUid[uid] ? byUid[uid].text.slice(0, 160) : '' },
          `${uid} · ${SPEAKER[byUid[uid]?.speaker] || ''}${isCall ? ' ▶' : ''}`)),
        r.checked_range && h('button', { class: 'chip range', type: 'button', onclick: () => focusUtts([], r.checked_range) },
          `Проверенный участок ${r.checked_range.from}–${r.checked_range.to}`)),
      h('div', { class: 'small muted', style: 'margin-top:4px' },
        r.status === 'ok' ? `Уверенность модели: ${CONF[r.confidence] || r.confidence}${r.votes ? ` · голоса ${r.votes}` : ''}`
          : 'Модель не дала корректный ответ — нужна оценка проверяющего через апелляцию.',
        r.reasoning && h('details', { class: 'why' }, h('summary', {}, 'Ход рассуждения модели'), h('div', {}, r.reasoning)),
        h('button', { class: 'btn small', type: 'button', style: 'margin-top:4px', onclick: () => showTrace(r.id, c.title) },
          'Как получена оценка')),
      it.appeals.length > 0 && h('div', { class: 'history' }, it.appeals.map((a) => appealHistory(a, c, it.original))),
      actions);
  }

  async function showTrace(rid, title) {
    let t;
    try { t = await api(`/criterion-results/${rid}/trace`); } catch (e) { toast(e.message, 'error'); return; }
    const when = (s) => (s || '').replace('T', ' ').slice(0, 19);
    const dlg = h('dialog', { 'aria-label': `Как получена оценка: ${title}` },
      h('div', { class: 'row' }, h('h2', { style: 'margin:0' }, `Как получена оценка: ${title}`), h('span', { class: 'spacer' }),
        h('button', { class: 'btn', type: 'button', onclick: () => dlg.close() }, 'Закрыть')),
      h('dl', { class: 'kv', style: 'margin:10px 0' },
        h('dt', {}, 'Модель'), h('dd', {}, t.model || '—'),
        h('dt', {}, 'Версия промпта'), h('dd', {}, t.prompt_version || '—'),
        h('dt', {}, 'Оценка выполнена'), h('dd', {}, `${when(t.evaluated_at)} UTC${t.run_id ? ` · запуск №${t.run_id}` : ''}`),
        t.votes ? [h('dt', {}, 'Голоса модели'), h('dd', {}, t.votes)] : '',
        h('dt', {}, 'Отпечаток текста диалога'), h('dd', { class: 'mono' }, (t.conversation_hash || '').slice(0, 16)),
        h('dt', {}, 'Отпечаток критерия'), h('dd', { class: 'mono' }, (t.criterion_hash || '').slice(0, 16))),
      h('p', { class: 'small muted' }, 'Каждый вызов модели записывается. Если текст диалога или формулировка критерия меняются, отпечаток меняется и оценка выполняется заново.'),
      h('h3', {}, `Вызовы модели (${t.calls.length})`),
      t.calls.length ? h('table', { class: 'list' },
        h('thead', {}, h('tr', {}, h('th', {}, 'Время (UTC)'), h('th', {}, 'Модель'), h('th', {}, 'Токены вход/выход'),
          h('th', {}, 'Время ответа'), h('th', {}, 'Статус'))),
        h('tbody', {}, t.calls.map((c) => h('tr', {}, h('td', {}, when(c.created_at)), h('td', {}, c.model),
          h('td', {}, `${c.in_tokens} / ${c.out_tokens}`), h('td', {}, `${(c.latency_ms / 1000).toFixed(1)} с`),
          h('td', {}, c.status === 'ok' ? 'ок' : `${c.status}${c.error ? `: ${c.error.slice(0, 120)}` : ''}`)))))
        : h('div', { class: 'small muted' }, 'Нет записанных вызовов.'),
      t.prompt && h('details', { style: 'margin-top:10px' }, h('summary', {}, 'Промпт, отправленный модели'),
        h('h3', {}, 'Системный'), h('pre', { class: 'prompt' }, t.prompt.system),
        h('h3', {}, 'Сообщение'), h('pre', { class: 'prompt' }, t.prompt.user)),
      t.raw_output && h('details', { style: 'margin-top:6px' }, h('summary', {}, 'Сырой ответ модели'),
        h('pre', { class: 'prompt' }, t.raw_output)));
    dlg.addEventListener('close', () => dlg.remove());
    document.body.append(dlg);
    dlg.showModal();
  }

  function appealHistory(a, c, original) {
    const orig = answerLabel(c, original.verdict, original.value, original.status);
    if (a.status === 'open') {
      return h('div', {}, h('b', {}, 'Апелляция подана: '), a.comment, h('span', { class: 'muted small' }, ' · ждёт решения проверяющего'));
    }
    const fin = a.action === 'changed' ? answerLabel(c, a.new_verdict, a.new_value, 'ok') : orig;
    return h('div', {},
      h('div', { class: 'small muted' }, `Апелляция: ${a.comment}`),
      h('div', { class: 'flow' },
        'Исходно ', h('span', { class: `answer ${orig.cls}` }, orig.text), ' → ',
        a.action === 'changed' ? 'изменено на ' : 'подтверждено ', h('span', { class: `answer ${fin.cls}` }, fin.text)),
      h('div', { class: 'small' }, h('b', {}, 'Причина решения: '), a.reason));
  }

  function appealForm(r, onCancel) {
    const ta = h('textarea', { placeholder: 'С чем вы не согласны и почему?', 'aria-label': 'Комментарий к апелляции' });
    const send = h('button', { class: 'btn primary small', type: 'button' }, 'Подать апелляцию');
    const box = h('div', { class: 'inline-form' }, ta, h('div', { class: 'row' }, send,
      h('button', { class: 'btn small', type: 'button', onclick: () => { box.remove(); onCancel(); } }, 'Отмена')));
    send.onclick = () => act(send, async () => {
      if (!ta.value.trim()) throw new Error('Добавьте комментарий к апелляции.');
      await api(`/criterion-results/${r.id}/appeals`, { method: 'POST', body: { comment: ta.value } });
      toast('Апелляция подана');
      reload();
    });
    setTimeout(() => ta.focus(), 0);
    return box;
  }

  const warn = conv.warnings.length > 0 && h('div', { class: 'warn-box', style: 'margin-bottom:12px' },
    `Предупреждения импорта: ${conv.warnings.join('; ')}`);
  main.replaceChildren(head, warn || '',
    h('div', { class: 'dialog-grid' },
      h('div', { class: 'panel transcript' }, audioBar || '', list),
      h('div', {}, evalBox)));

  const deep = route.q.get('u');
  if (deep && byUid[deep]) focusUtts([deep], null, false);
}
