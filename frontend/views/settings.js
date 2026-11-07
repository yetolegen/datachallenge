import { api } from '../api.js';
import { act, fmt, h, toast } from '../dom.js';

const sec = (ms) => (ms == null ? '—' : fmt(ms / 1000, 1));

export async function render(main) {
  const [s, cost] = await Promise.all([api('/settings'), api('/stats/cost')]);
  const f = {};
  const input = (key, label, type = 'text', extra = {}) => h('label', { class: 'field' }, label,
    f[key] = h('input', { type, value: s[key] ?? '', ...extra }));
  const models = h('datalist', { id: 'model-list' });
  const status = h('div', { class: 'small' });

  const PROVIDERS = [['ollama', 'Локальная модель (Ollama)'], ['openai', 'Облако (OpenAI-совместимый API)'], ['fake', 'Тестовый (без модели)']];
  const THINK = [['', 'Не передавать (модель без режима размышлений)'], ['false', 'Выключено'], ['true', 'Включено (медленнее)']];
  const sel = (key, opts) => (f[key] = h('select', { value: s[key] ?? '' }, opts.map(([v, t]) => h('option', { value: v }, t))));
  sel('provider', PROVIDERS);
  sel('provider_kz', PROVIDERS);
  sel('ollama_think', THINK);
  sel('ollama_think_kz', THINK);

  // cloud presets only fill the base URL; the key for that service goes into OPENAI_API_KEY in .env
  const PRESETS = [
    ['Gemini', 'https://generativelanguage.googleapis.com/v1beta/openai/'],
    ['OpenAI', 'https://api.openai.com/v1'],
    ['OpenRouter', 'https://openrouter.ai/api/v1'],
  ];
  const presetBtns = PRESETS.map(([name, url]) => h('button', { class: 'btn small', type: 'button', onclick: () => {
    f.openai_base_url.value = url;
    toast(`Адрес ${name} подставлен. Укажите модель, ключ этого сервиса — в .env (OPENAI_API_KEY), затем «Сохранить».`);
  } }, name));

  const check = h('button', { class: 'btn', type: 'button' }, 'Проверить подключение');
  check.onclick = () => act(check, async () => {
    const r = await api('/llm/models');
    models.replaceChildren(...r.models.map((m) => h('option', { value: m })));
    status.replaceChildren(r.ok
      ? h('span', { style: 'color:var(--good)' }, `Подключено. Доступно моделей: ${r.models.length}${r.models.length ? ` (${r.models.slice(0, 12).join(', ')}${r.models.length > 12 ? '…' : ''})` : ''}`)
      : h('span', { style: 'color:var(--bad)' }, r.detail));
  });
  const grid = (...kids) => h('div', { style: 'display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-bottom:14px' }, ...kids);
  const save = h('button', { class: 'btn primary', type: 'submit' }, 'Сохранить');
  const form = h('form', { onsubmit: (e) => {
    e.preventDefault();
    act(save, async () => {
      const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value]));
      await api('/settings', { method: 'PUT', body });
      toast('Настройки сохранены. Новые оценки пойдут через выбранную модель.');
    });
  } },
  h('h3', {}, 'Основная модель — диалоги на русском и английском'),
  grid(
    h('label', { class: 'field' }, 'Где работает', f.provider),
    input('model', 'Модель', 'text', { list: 'model-list', placeholder: 'qwen3:8b' }),
    h('label', { class: 'field' }, 'Режим размышлений', f.ollama_think)),
  h('h3', {}, 'Казахские диалоги (KZ)'),
  grid(
    h('label', { class: 'field' }, 'Где работает', f.provider_kz),
    input('model_kz', 'Модель для KZ (пусто — как основная)', 'text', { list: 'model-list', placeholder: 'sherkala' }),
    h('label', { class: 'field' }, 'Режим размышлений', f.ollama_think_kz)),
  h('h3', {}, 'Подключение'),
  grid(
    input('ollama_url', 'Адрес Ollama'),
    input('ollama_num_ctx', 'Контекст Ollama, токенов', 'number'),
    h('label', { class: 'field' }, 'Адрес облачного API', input('openai_base_url', '').lastChild,
      h('div', { class: 'row', style: 'margin-top:4px' }, presetBtns)),
    input('concurrency', 'Параллельных запросов (локально 1–2, облако 4–8)', 'number'),
    input('timeout_s', 'Таймаут запроса, сек', 'number')),
  models,
  h('p', { class: 'small muted' }, `Ключ облачного API хранится только в .env на сервере: ${s.openai_key_set ? 'задан' : 'не задан'}. Версия промпта: ${s.prompt_version}.`),
  h('div', { class: 'row' }, save, check), status);

  const dl = (pairs) => h('dl', { class: 'kv' }, pairs.flatMap(([k, v]) => [h('dt', {}, k), h('dd', {}, v)]));
  main.replaceChildren(
    h('h1', {}, 'Настройки'),
    h('div', { class: 'panel' }, h('h2', {}, 'Модель'), form),
    h('div', { class: 'panel' }, h('h2', {}, 'Стоимость и надёжность'),
      cost.calls === 0 ? h('div', { class: 'empty' }, 'Вызовов модели ещё не было. Запустите оценку набора.')
        : dl([
          ['Модели', cost.models.join(', ')],
          ['Вызовов модели', cost.calls],
          ['Средняя стоимость одной оценки', `$${fmt(cost.avg_cost_per_evaluation, 5)}`],
          ['Всего потрачено', `$${fmt(cost.total_cost, 4)}`],
          ['Токенов на диалог (все критерии)', cost.avg_tokens_per_dialog
            ? `${cost.avg_tokens_per_dialog.in} вход / ${cost.avg_tokens_per_dialog.out} выход` : '—'],
          ['Задержка вызова p50 / p95', `${sec(cost.latency_ms_p50)} / ${sec(cost.latency_ms_p95)} с`],
          ['Невалидных ответов модели', `${fmt((cost.invalid_rate || 0) * 100, 1)}%`],
          ['Критериев «требует проверки»', `${fmt((cost.needs_review_rate || 0) * 100, 1)}%`],
        ]),
      h('p', { class: 'small muted' }, 'Одна оценка — все критерии чек-листа для одного диалога, включая повторные вызовы. Локальная модель стоит $0 (только электричество).')));
}
