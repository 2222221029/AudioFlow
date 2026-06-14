import { Suspense, lazy, useMemo } from 'react';
import { pickInitialMode } from './utils/displayMode.js';

const DesktopPage = lazy(() => import('./pages/DesktopPage.jsx'));
const MobilePage = lazy(() => import('./pages/MobilePage.jsx'));

const loadingStyle = {
  minHeight: '100vh',
  display: 'grid',
  placeItems: 'center',
  background: '#111827',
  color: '#f9fafb',
  fontSize: 14,
};

export default function App() {
  const mode = useMemo(() => pickInitialMode(), []);
  const Page = mode === 'mobile' ? MobilePage : DesktopPage;
  return (
    <Suspense fallback={<div style={loadingStyle}>加载中...</div>}>
      <Page />
    </Suspense>
  );
}
