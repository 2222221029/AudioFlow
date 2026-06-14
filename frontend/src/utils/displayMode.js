export function pickInitialMode() {
  const params = new URLSearchParams(window.location.search);
  const forced = params.get('v') || params.get('view');
  if (forced === 'desktop') return 'desktop';
  if (forced === 'm' || forced === 'mobile') return 'mobile';
  const mobileUA = /Mobi|Android|iPhone|iPad|iPod|Windows Phone|Mobile/i.test(navigator.userAgent || '');
  return mobileUA || window.innerWidth < 900 ? 'mobile' : 'desktop';
}
