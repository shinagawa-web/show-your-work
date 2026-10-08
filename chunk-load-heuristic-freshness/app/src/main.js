const VERSION = import.meta.env.VITE_APP_VERSION;

const saved = JSON.parse(sessionStorage.getItem('app-log') || '[]');
window.__app = { version: VERSION, loaded: null, errors: [], log: saved };
const log = (entry) => {
  window.__app.log.push({ t: Date.now(), ...entry });
  sessionStorage.setItem('app-log', JSON.stringify(window.__app.log));
};
log({ event: 'boot', version: VERSION });

if (import.meta.env.VITE_RELOAD_ON_PRELOAD_ERROR === '1') {
  window.addEventListener('vite:preloadError', (event) => {
    log({ event: 'vite:preloadError', message: String(event.payload) });
    if (!sessionStorage.getItem('reloaded-once')) {
      sessionStorage.setItem('reloaded-once', '1');
      window.location.reload();
    }
  });
}

document.getElementById('version').textContent = VERSION;
document.getElementById('open').addEventListener('click', () => {
  import('./feature.js')
    .then((m) => {
      window.__app.loaded = m.FEATURE_VERSION;
      document.getElementById('out').textContent = m.render();
      log({ event: 'chunk-loaded', version: m.FEATURE_VERSION });
    })
    .catch((err) => {
      window.__app.errors.push(`${err.name}: ${err.message}`);
      log({ event: 'chunk-error', message: `${err.name}: ${err.message}` });
    });
});
