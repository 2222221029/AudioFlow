import {useId} from 'react';

export function AppLogo({className = '', title = 'AudioFlow'}) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const ribbonId = `audioflowRibbon-${id}`;
  const accentId = `audioflowAccent-${id}`;
  const glowId = `audioflowGlow-${id}`;
  const glassId = `audioflowGlass-${id}`;

  return (
    <svg className={`app-logo ${className}`.trim()} viewBox="0 0 64 64" role="img" aria-label={title}>
      <defs>
        <linearGradient id={ribbonId} x1="13" y1="46" x2="52" y2="14" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="var(--logo-c)" />
          <stop offset=".46" stopColor="var(--logo-a)" />
          <stop offset="1" stopColor="var(--logo-b)" />
        </linearGradient>
        <linearGradient id={accentId} x1="16" y1="20" x2="49" y2="47" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="var(--logo-b)" />
          <stop offset=".55" stopColor="var(--logo-c)" />
          <stop offset="1" stopColor="var(--logo-a)" />
        </linearGradient>
        <linearGradient id={glassId} x1="12" y1="8" x2="52" y2="57" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="var(--logo-glass-top)" />
          <stop offset=".58" stopColor="var(--logo-glass-mid)" />
          <stop offset="1" stopColor="var(--logo-glass-bottom)" />
        </linearGradient>
        <radialGradient id={glowId} cx="50%" cy="38%" r="58%">
          <stop offset="0" stopColor="var(--logo-c)" stopOpacity=".34" />
          <stop offset=".54" stopColor="var(--logo-a)" stopOpacity=".16" />
          <stop offset="1" stopColor="var(--logo-b)" stopOpacity="0" />
        </radialGradient>
        <filter id={`audioflowShadow-${id}`} x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="6" stdDeviation="5" floodColor="var(--logo-shadow)" floodOpacity=".24" />
        </filter>
      </defs>
      <rect className="app-logo-bg" x="5" y="5" width="54" height="54" rx="17" fill={`url(#${glassId})`} />
      <circle className="app-logo-glow" cx="33" cy="31" r="24" fill={`url(#${glowId})`} />
      <g filter={`url(#audioflowShadow-${id})`}>
        <path className="app-logo-page" d="M17 42.5c5.8-2.9 11-3 15.1.1 4.3-3 9.3-3 14.9-.1" />
        <path className="app-logo-page app-logo-page-top" d="M18.5 37.5c5-2.3 9.6-2.3 13.5.5 3.9-2.8 8.5-2.8 13.5-.5" />
        <path className="app-logo-ribbon" d="M17.5 43.5C24 24.8 30 16.8 35.6 19.7c3.2 1.7 3.4 6.4.4 14.4" stroke={`url(#${ribbonId})`} />
        <path className="app-logo-ribbon app-logo-ribbon-right" d="M35.9 34.1c-1.7 5.3.2 9.7 5.7 9.7 3.5 0 5.5-1.9 7.1-5.1" stroke={`url(#${ribbonId})`} />
        <path className="app-logo-wave" d="M18.6 33.7c3.5-4.8 7-4.8 10.6 0s7 4.8 10.5 0 6.8-4.8 10 0" stroke={`url(#${accentId})`} />
        <path className="app-logo-spark" d="M46.5 18.2l1.3 3.2 3.4 1.2-3.4 1.2-1.3 3.2-1.3-3.2-3.4-1.2 3.4-1.2 1.3-3.2Z" fill={`url(#${accentId})`} />
      </g>
    </svg>
  );
}
