import {useId} from 'react';

export function AppLogo({className = '', title = 'AudioFlow'}) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const markId = `audioflowMark-${id}`;
  const pageId = `audioflowPage-${id}`;
  const glowId = `audioflowGlow-${id}`;

  return (
    <svg className={`app-logo ${className}`.trim()} viewBox="0 0 64 64" role="img" aria-label={title}>
      <defs>
        <linearGradient id={markId} x1="14" y1="16" x2="52" y2="50" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="var(--logo-c)" />
          <stop offset=".48" stopColor="var(--logo-a)" />
          <stop offset="1" stopColor="var(--logo-b)" />
        </linearGradient>
        <linearGradient id={pageId} x1="13" y1="20" x2="51" y2="50" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="var(--panel-2)" />
          <stop offset="1" stopColor="var(--panel-hi)" />
        </linearGradient>
        <radialGradient id={glowId} cx="50%" cy="38%" r="58%">
          <stop offset="0" stopColor="var(--logo-c)" stopOpacity=".28" />
          <stop offset=".58" stopColor="var(--logo-a)" stopOpacity=".12" />
          <stop offset="1" stopColor="var(--logo-b)" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect className="app-logo-bg" x="4.5" y="4.5" width="55" height="55" rx="17" />
      <circle className="app-logo-glow" cx="32" cy="31" r="25" fill={`url(#${glowId})`} />
      <path
        className="app-logo-page app-logo-page-left"
        d="M14.5 23.5c0-2.5 2-4.5 4.5-4.5h10.8c2.1 0 3.8 1.7 3.8 3.8v24.6c-2.3-2.1-5.4-3.2-9.2-3.2H19c-2.5 0-4.5-2-4.5-4.5V23.5Z"
        fill={`url(#${pageId})`}
      />
      <path
        className="app-logo-page app-logo-page-right"
        d="M49.5 23.5c0-2.5-2-4.5-4.5-4.5H34.2c-2.1 0-3.8 1.7-3.8 3.8v24.6c2.3-2.1 5.4-3.2 9.2-3.2H45c2.5 0 4.5-2 4.5-4.5V23.5Z"
        fill={`url(#${pageId})`}
      />
      <path className="app-logo-spine" d="M32 22v25" />
      <path className="app-logo-flow" d="M18 36c4.2-6.3 8.6-6.3 13.1 0s8.8 6.3 13.1 0" stroke={`url(#${markId})`} />
      <path className="app-logo-flow app-logo-flow-secondary" d="M20 42c3.5-3.2 7-3.2 10.6 0s7.2 3.2 10.9 0" stroke={`url(#${markId})`} />
      <path className="app-logo-play" d="M31 24.4c0-1.2 1.3-1.9 2.3-1.3l8.2 5.2c.9.6.9 2 0 2.6l-8.2 5.2c-1 .6-2.3-.1-2.3-1.3V24.4Z" fill={`url(#${markId})`} />
    </svg>
  );
}
