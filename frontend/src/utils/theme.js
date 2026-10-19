export const THEME_KEY = 'node-monitor-theme'

export function readTheme(browser = window) {
  try {
    const saved = browser.localStorage.getItem(THEME_KEY)
    if (saved === 'light' || saved === 'dark') return saved
  } catch { /* Storage can be blocked; the in-memory switch still works. */ }
  return browser.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function applyTheme(theme, browser = window) {
  browser.document.documentElement.dataset.theme = theme
}

export function saveTheme(theme, browser = window) {
  try { browser.localStorage.setItem(THEME_KEY, theme) } catch { /* Optional persistence. */ }
}
