import {useId} from 'react';

export function AppLogo({className = '', title = 'AudioFlow'}) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const arcId = `appLogoArc-${id}`;
  const waveId = `appLogoWave-${id}`;
  return (
    <svg className={`app-logo ${className}`.trim()} viewBox="0 0 64 64" role="img" aria-label={title}>
      <defs>
        <linearGradient id={arcId} x1="12" y1="10" x2="54" y2="56" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="var(--aurora-a)" />
          <stop offset=".52" stopColor="var(--aurora-b)" />
          <stop offset="1" stopColor="var(--aurora-c)" />
        </linearGradient>
        <linearGradient id={waveId} x1="18" y1="41" x2="48" y2="41" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="var(--primary)" />
          <stop offset="1" stopColor="var(--accent)" />
        </linearGradient>
      </defs>
      <rect className="app-logo-bg" x="5" y="5" width="54" height="54" rx="16" />
      <path className="app-logo-arc" d="M17 33c0-11 6.8-18.8 15-18.8S47 22 47 33" stroke={`url(#${arcId})`} />
      <path className="app-logo-cup" d="M17 32h6c2.8 0 5 2.2 5 5v8h-5c-6.1 0-11-4.9-11-11v-1.5c0-.3.2-.5.5-.5H17Z" fill={`url(#${arcId})`} />
      <path className="app-logo-cup" d="M47 32h-6c-2.8 0-5 2.2-5 5v8h5c6.1 0 11-4.9 11-11v-1.5c0-.3-.2-.5-.5-.5H47Z" fill={`url(#${arcId})`} />
      <path className="app-logo-wave" d="M23 41c2.5-2.6 5.1-2.6 7.8 0s5.4 2.6 8.2 0" stroke={`url(#${waveId})`} />
      <path className="app-logo-island" d="M25 51h14" />
    </svg>
  );
}
