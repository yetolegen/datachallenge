import { api } from './api.js';
import { getRole, h, setRole } from './dom.js';

const routes = {
  dialogs: () => import('./views/dialogs.js'),
  dialog: () => import('./views/dialog.js'),
  analytics: () => import('./views/analytics.js'),
  appeals: () => import('./views/appeals.js'),
  checklist: () => import('./views/checklist.js'),
  datasets: () => import('./views/datasets.js'),
  settings: () => import('./views/settings.js'),
};

export function parseHash() {
  const [path, qs] = location.hash.replace(/^#/, '').split('?');
  return { parts: path.split('/').filter(Boolean).map(decodeURIComponent), q: new URLSearchParams(qs || '') };
}

async function go() {
  const route = parseHash();
  const name = routes[route.parts[0]] ? route.parts[0] : 'dialogs';
  const navName = name === 'dialog' ? 'dialogs' : name;
  document.querySelectorAll('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.route === navName));
  const main = document.getElementById('view');
  main.replaceChildren(h('div', { class: 'empty' }, 'Загрузка…'));
  try {
    const mod = await routes[name]();
    await mod.render(main, route);
  } catch (e) {
    main.replaceChildren(h('div', { class: 'error-box' }, `Не удалось открыть страницу: ${e.message}`));
  }
  refreshAppealCount();
}

export async function refreshAppealCount() {
  try {
    const open = await api('/appeals?status=open');
    const el = document.getElementById('appeal-count');
    el.textContent = open.length;
    el.hidden = open.length === 0;
  } catch { /* badge is optional */ }
}

const roleSel = document.getElementById('role');
roleSel.value = getRole();
document.body.dataset.role = roleSel.value;
roleSel.addEventListener('change', () => {
  setRole(roleSel.value);
  document.body.dataset.role = roleSel.value;
  go();
});
window.addEventListener('hashchange', go);
window.addEventListener('cqa:refresh', go);
go();
