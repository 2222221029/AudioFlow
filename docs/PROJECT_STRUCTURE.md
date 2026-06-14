# Project Structure

```text
web_server.py          Server entrypoint
src/server/            Flask Web/PWA API and SPA fallback
src/features/          Shared feature code used by the server
core/                  Search, download, cookies, subscriptions, platform adapters
core/vendor/           Portable reference scripts used by platform adapters
frontend/              React + Vite Web/PWA frontend
frontend/src/          Componentized UI source
frontend/public/       PWA manifest, service worker, runtime env, platform logos
assets/branding/       Web icons, favicons, logos, and branding assets
scripts/               Version and deployment helper scripts
data/                  Runtime data mount
config/                Runtime config mount
downloads/             Download mount
logs/                  Log mount
```

Legacy business HTML pages are intentionally removed. The only frontend HTML kept is `frontend/index.html`, which is the Vite mount entry.

Runtime data such as real cookies, subscription databases, task records, logs, and downloads should live in mounted deployment directories, not in source control.

Key frontend modules:

```text
frontend/src/hooks/useAudioFlowApp.js      Shared app state and API actions
frontend/src/components/Shared.jsx      Search/detail/download/subscription/settings components
frontend/src/pages/DesktopPage.jsx      Desktop shell
frontend/src/pages/MobilePage.jsx       Mobile shell
frontend/src/services/api.js            Unified /api request wrapper
frontend/src/utils/themes.js            Theme and favicon switching
```

Key backend endpoints:

```text
GET  /api/diagnostics          Runtime, path, frontend and ffmpeg diagnostics
POST /api/downloads/cleanup    Batch clean completed/failed task records
POST /api/auth/login           Password login with failure rate limiting
```
