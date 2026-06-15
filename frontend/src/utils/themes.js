export const THEMES = [
  {value: 'midnight_aurora', name: '午夜极光', mode: 'dark', colors: ['#6366f1', '#a855f7', '#06b6d4']},
  {value: 'obsidian_ink', name: '黛山墨韵', mode: 'dark', colors: ['#3b82f6', '#475569', '#0ea5e9']},
  {value: 'velvet_rose', name: '子夜玫瑰', mode: 'dark', colors: ['#ec4899', '#a21caf', '#f43f5e']},
  {value: 'emerald_forest', name: '翡翠深林', mode: 'dark', colors: ['#10b981', '#059669', '#14b8a6']},
  {value: 'amber_noir', name: '琥珀夜阑', mode: 'dark', colors: ['#f59e0b', '#b45309', '#ef4444']},
  {value: 'cloud_mist', name: '晨曦云岫', mode: 'light', colors: ['#6366f1', '#a855f7', '#06b6d4']},
  {value: 'sakura_snow', name: '樱川初雪', mode: 'light', colors: ['#f472b6', '#fb7185', '#fda4af']},
  {value: 'golden_dune', name: '沙金黎明', mode: 'light', colors: ['#d97706', '#ca8a04', '#84cc16']},
  {value: 'celadon_valley', name: '青瓷溪谷', mode: 'light', colors: ['#0d9488', '#10b981', '#22d3ee']},
  {value: 'pearl_mint', name: '月白薄荷', mode: 'light', colors: ['#0ea5e9', '#22d3ee', '#a3e635']},
  {value: 'paper_ink', name: '纸墨书香', mode: 'light', colors: ['#8b5c2a', '#6b4226', '#b87333']},
  {value: 'paper_ink_night', name: '墨夜书房', mode: 'dark', colors: ['#c4860a', '#96600a', '#d4a44c']},
];

export const DEFAULT_THEME = 'midnight_aurora';

export function themeByValue(value) {
  return THEMES.find((theme) => theme.value === value) || THEMES[0];
}

export function savedTheme() {
  try {
    return localStorage.getItem('audioflow_theme') || DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

export function persistTheme(value) {
  try {
    localStorage.setItem('audioflow_theme', value);
  } catch {
    // localStorage may be blocked in private browsing.
  }
}

export function applyTheme(value) {
  const theme = themeByValue(value);
  const root = document.documentElement;
  root.setAttribute('data-theme', theme.value);
  root.setAttribute('data-theme-mode', theme.mode);
  root.style.setProperty('--logo-a', theme.colors[0]);
  root.style.setProperty('--logo-b', theme.colors[1]);
  root.style.setProperty('--logo-c', theme.colors[2]);

  const themeColor = theme.mode === 'light' ? '#f4f3fb' : '#0c1130';
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', themeColor);

  for (const favicon of document.querySelectorAll('link[rel="icon"]')) {
    favicon.setAttribute('href', '/favicon.svg?v=20260615b');
    favicon.setAttribute('type', 'image/svg+xml');
  }
}
