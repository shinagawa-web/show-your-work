// VITE_APP_VERSION differs between v1 and v2, so every hashed file name changes.
const VERSION = import.meta.env.VITE_APP_VERSION;

// State read by the test runner. It survives a reload through sessionStorage.
const saved = JSON.parse(sessionStorage.getItem('app-log') || '[]');
window.__app = { version: VERSION, loaded: null, errors: [], log: saved };
const log = (entry) => {
  window.__app.log.push({ t: Date.now(), ...entry });
  sessionStorage.setItem('app-log', JSON.stringify(window.__app.log));
};
log({ event: 'boot', version: VERSION });

if (import.meta.env.VITE_RELOAD_ON_PRELOAD_ERROR === '1') {
  // Countermeasure: reload once when a chunk fails to load (Vite's documented hook).
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
      // What an error collector would receive.
      window.__app.errors.push(`${err.name}: ${err.message}`);
      log({ event: 'chunk-error', message: `${err.name}: ${err.message}` });
    });
});
