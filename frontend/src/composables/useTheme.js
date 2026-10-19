import { computed, ref } from 'vue'
import { THEME_KEY, applyTheme, readTheme, saveTheme } from '../utils/theme'

const theme = ref('light')
let initialized = false

export function initTheme() {
  if (initialized) return
  initialized = true
  theme.value = readTheme()
  applyTheme(theme.value)
  window.addEventListener('storage', event => {
    if (event.key !== THEME_KEY && event.key !== null) return
    theme.value = readTheme()
    applyTheme(theme.value)
  })
}

export function useTheme() {
  initTheme()
  function toggleTheme() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    applyTheme(theme.value)
    saveTheme(theme.value)
  }
  return { theme, isDark: computed(() => theme.value === 'dark'), toggleTheme }
}
