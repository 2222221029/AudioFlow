import { Suspense, lazy, useMemo } from 'react';
import { pickInitialMode } from './utils/displayMode.js';

const DesktopPage = lazy(() => import('./pages/DesktopPage.jsx'));
const MobilePage = lazy(() => import('./pages/MobilePage.jsx'));

export default function App() {
  const mode = useMemo(() => pickInitialMode(), []);
  const Page = mode === 'mobile' ? MobilePage : DesktopPage;
  return (
    <Suspense fallback={null}>
      <Page />
    </Suspense>
  );
}
