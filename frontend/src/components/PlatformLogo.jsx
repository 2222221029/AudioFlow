import {platformKey, platformLogo} from '../utils/platforms.js';

export default function PlatformLogo({value, name, className = 'platform-logo'}) {
  const label = String(name || value || '').trim();
  const src = platformLogo(value);
  if (!src) {
    return <span className={`${className} platform-logo-fallback`}>{label.slice(0, 1) || '?'}</span>;
  }
  return (
    <span className={className} title={label || platformKey(value)}>
      <img src={src} alt={label} loading="lazy" />
    </span>
  );
}

export function PlatformName({value, name}) {
  const label = name || value || '';
  return (
    <span className="platform-name">
      <PlatformLogo value={value} name={label} />
      <span>{label}</span>
    </span>
  );
}

export function PlatformTag({value, name}) {
  const label = name || value || '?';
  return (
    <span className="platform-tag platform-tag-logo">
      <PlatformLogo value={value} name={label} className="platform-logo platform-logo-xs" />
      <span>{label}</span>
    </span>
  );
}
