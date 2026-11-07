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
    // bot/system lines are context: collapsed to one line, click to read
    const quiet = u.speaker === 'bot' || u.speaker === 'system';
    uttEls[u.uid] = h('div', { class: `utt ${u.speaker}`, id: `utt-${u.uid}`,
      onclick: quiet ? (e) => { if (e.target.tagName !== 'BUTTON') e.currentTarget.classList.toggle('open'); } : null },
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
    h('span', { class: 'muted small' }, `${conv.language} · ${CHANNEL[conv.channel] || conv.channel} · ${conv.dataset_group_id}`),
    conv.revision > 1 && h('span', { class: 'badge mid' }, `Ревизия ${conv.revision}`),
    h('span', { class: 'spacer' }), evalSel, runBtn);

  if (!view) {
    evalBox.append(h('div', { class: 'empty' }, 'Диалог ещё не оценён. Нажмите «Оценить диалог» — будет применён активный чек-лист.'));
  } else {
    const b = view.breakdown;
    const tot = b.total;
    evalBox.append(
      !b.critical_failed ? '' : h('div', { class: 'critical-banner', role: 'alert' },
        'Грубая ошибка: ', view.items.filter((i) => b.critical_ids.includes(i.criterion.id)).map((i) => i.criterion.title).join(', ')),
      h('div', { class: 'total-box' },
        h('div', {},
          h('div', { class: `total-num score ${scoreClass(tot)}` }, tot == null ? 'N/A' : fmt(tot),
            tot != null && h('small', {}, ` / ${b.scale_max}`)),
          h('div', { class: 'small muted' }, `чек-лист v${view.checklist_version.version}`,
            b.provisional ? ' · предварительно' : ''),
          view.original_total !== tot && h('div', { class: 'small' }, 'ИИ ', h('b', {}, fmt(view.original_total)),
            ' → после апелляции ', h('b', {}, fmt(tot)))),
        !b.sections.length ? '' : h('div', { class: 'sec-list' }, b.sections.map((s) => h('div', { class: 'sec-mini' },
          h('span', {}, s.title),
          h('div', { class: 'bar' }, h('i', { style: `width:${s.percent ?? 0}%` })),
          h('span', { class: 'num' }, s.points == null ? '—' : `${fmt(s.points, 0)}/${fmt(s.max_points, 0)}`))))),
      h('div', { class: 'crit-list' }, view.items.map(critRow)));
  }

  // one scannable line per criterion; details on demand. Opening a row also highlights its evidence.
  function critRow(it) {
    const c = it.criterion, r = it.result, s = it.score, f = it.final;
    if (!r) {
      return h('div', { class: 'crit' }, h('div', { class: 'crit-line' }, h('span', { class: 'crit-title' }, c.title),
        h('span', { class: 'answer na' }, 'Не оценён')));
    }
    const lab = answerLabel(c, f.verdict, f.value, f.status);
    const pts = c.is_critical || f.verdict === 'na' || !s || s.points == null ? '' : `${fmt(s.points, 1)}/${fmt(s.max_points, 1)}`;
    const openAppeal = it.appeals.find((a) => a.status === 'open');
    const startOpen = !!openAppeal || it.appeals.length > 0 || f.status !== 'ok';
    const wrap = h('div', { class: `crit${startOpen ? ' open' : ''}`, id: `crit-${c.id}` });
    const line = h('button', { class: 'crit-line', type: 'button', 'aria-expanded': String(startOpen) },
      h('span', { class: 'caret', 'aria-hidden': 'true' }, '▸'),
      h('span', { class: 'crit-title' }, c.title),
      openAppeal && h('span', { class: 'dot', title: 'Открытая апелляция' }),
      h('span', { class: 'points' }, pts),
      h('span', { class: `answer ${lab.cls}` }, lab.text));
    line.onclick = () => {
      const open = wrap.classList.toggle('open');
      line.setAttribute('aria-expanded', String(open));
      if (open) focusUtts(r.evidence_ids, r.evidence_ids.length ? null : r.checked_range);
    };

    const actions = h('div');
    if (role === 'operator' && !openAppeal) {
      const btn = h('button', { class: 'btn small', type: 'button' }, 'Подать апелляцию');
      btn.onclick = () => { btn.hidden = true; actions.append(appealForm(r, () => { btn.hidden = false; })); };
      actions.append(btn);
    }
    if (role === 'reviewer' && openAppeal) {
      actions.append(decisionForm({ ...openAppeal, criterion: c }, () => { toast('Решение сохранено, итог пересчитан'); reload(); }));
    }
    const eff = s && s.effective_weight;
    const weight = c.is_critical ? 'без баллов' : f.verdict === 'na' ? 'вес перераспределён'
      : `вес ${c.weight}${eff != null && eff !== c.weight ? ` → ${fmt(eff, 1)}` : ''}`;
    const meta = [r.status === 'ok' ? `уверенность ${CONF[r.confidence] || r.confidence}` : 'модель не дала корректный ответ',
      r.votes && `голоса ${r.votes}`, weight].filter(Boolean).join(' · ');

    wrap.append(line, h('div', { class: 'crit-body' },
      h('div', { class: 'explain' }, r.explanation),
      f.verdict === 'na' && r.na_reason && h('div', { class: 'small' }, h('b', {}, 'N/A: '), r.na_reason),
      h('div', { class: 'chips' },
        r.evidence_ids.map((uid) => h('button', { class: 'chip', type: 'button', onclick: () => focusUtts([uid]),
          title: byUid[uid] ? byUid[uid].text.slice(0, 160) : '' },
          `${uid} · ${SPEAKER[byUid[uid]?.speaker] || ''}${isCall ? ' ▶' : ''}`)),
        r.checked_range && h('button', { class: 'chip range', type: 'button', onclick: () => focusUtts([], r.checked_range) },
          `участок ${r.checked_range.from}–${r.checked_range.to}`)),
      h('div', { class: 'crit-foot' }, h('span', { class: 'small muted' }, meta), h('span', { class: 'spacer' }),
        h('button', { class: 'btn link', type: 'button', onclick: () => showTrace(r.id, c.title) }, 'Как получена оценка')),
      it.appeals.length > 0 && h('div', { class: 'history' }, it.appeals.map((a) => appealHistory(a, c, it.original))),
      actions));
    return wrap;
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
