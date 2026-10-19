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

// Canvas/SVG chart libraries require resolved colors rather than CSS variable strings.
export function readThemeColors(browser = window) {
  const styles = browser.getComputedStyle(browser.document.documentElement)
  const tokens = { text:'text', muted:'text-muted', border:'border', grid:'border-soft', surface:'surface', soft:'surface-soft',
    accent:'accent', accentSoft:'accent-soft', track:'track', cpu:'metric-cpu', memory:'metric-memory', gpu:'metric-gpu' }
  return Object.fromEntries(Object.entries(tokens).map(([name, token]) => [name, styles.getPropertyValue('--'+token).trim()]))
}
