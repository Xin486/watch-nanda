<template>
  <button type="button" class="theme-toggle" :aria-label="label" :title="label" :aria-pressed="isDark" @click="toggleTheme">
    <svg v-if="isDark" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19M5 19l1.4-1.4M17.6 6.4 19 5"/></svg>
    <svg v-else viewBox="0 0 24 24" aria-hidden="true"><path d="M20.8 13.5A9 9 0 0 1 10.5 3.2a9 9 0 1 0 10.3 10.3Z"/></svg>
  </button>
</template>
<script setup>
import { computed } from 'vue'
import { useTheme } from '../composables/useTheme'
const { isDark, toggleTheme } = useTheme()
const label = computed(() => isDark.value ? '切换到白天样式' : '切换到夜间样式')
</script>
<style scoped>
.theme-toggle{display:grid;place-items:center;flex-shrink:0;width:40px;height:40px;padding:0;border:1px solid var(--border);border-radius:9px;background:var(--surface);color:var(--accent);cursor:pointer;box-shadow:var(--shadow)}.theme-toggle:hover{border-color:var(--accent-border);background:var(--accent-soft)}.theme-toggle:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.theme-toggle svg{width:19px;height:19px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
</style>
