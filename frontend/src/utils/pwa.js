let deferredInstallPrompt = null;
let updated = false;

export function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js', {scope: '/'}).then((registration) => {
      // 新版本 SW 就绪并接管后自动刷新页面，让用户拿到最新版界面
      if (registration.waiting && !updated) {
        updated = true;
        registration.waiting.postMessage({type: 'SKIP_WAITING'});
      }
      registration.addEventListener('updatefound', () => {
        const newWorker = registration.installing;
        if (!newWorker) return;
        newWorker.addEventListener('statechange', () => {
          if (newWorker.state === 'installed' && navigator.serviceWorker.controller && !updated) {
            updated = true;
            newWorker.postMessage({type: 'SKIP_WAITING'});
          }
        });
      });
    }).catch(() => {});
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (updated) window.location.reload();
    });
  });
}

export function setupInstallPrompt(callback) {
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    deferredInstallPrompt = event;
    callback?.(true);
  });
  window.addEventListener('appinstalled', () => {
    deferredInstallPrompt = null;
    callback?.(false);
  });
}

export async function promptInstall() {
  if (!deferredInstallPrompt) return false;
  deferredInstallPrompt.prompt();
  await deferredInstallPrompt.userChoice.catch(() => null);
  deferredInstallPrompt = null;
  return true;
}

export function isStandalonePwa() {
  return window.matchMedia?.('(display-mode: standalone)').matches || window.navigator.standalone === true;
}
