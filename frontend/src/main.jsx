import { createRoot } from 'react-dom/client';
import App from './App.jsx';
import {applyTheme, savedTheme} from './utils/themes.js';
import {registerServiceWorker} from './utils/pwa.js';

applyTheme(savedTheme());
registerServiceWorker();
createRoot(document.getElementById('root')).render(<App />);
