<template><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="paths[name] || paths.nodes"/></svg></template>
<script setup>
defineProps({ name: { type: String, required: true } })
const paths = {
  nodes: 'M4 3h16v7H4zM4 14h16v7H4zM8 6.5h.01M8 17.5h.01M12 6.5h4M12 17.5h4',
  folder: 'M3 6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z',
  add: 'M3 4h18v12H3zM8 21h8M12 16v5M8 10h8M12 6v8',
  history: 'M3 4h18v16H3zM6 15l4-5 4 3 4-6',
  terminal: 'M3 4h18v16H3zM7 8l4 4-4 4M14 16h3',
  settings: 'm9 3 1-1h4l1 3 3 1 3 1v4l-2 2-1 3-1 3h-4l-2-2-3-1-3-1v-4l2-2 1-3ZM9 12a3 3 0 1 0 6 0 3 3 0 0 0-6 0',
  trash: 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7',
  plus: 'M12 4v16M4 12h16',
}
</script>
