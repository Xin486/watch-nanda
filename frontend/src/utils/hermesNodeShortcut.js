export const hermesNodeShortcutEvent = 'hermes-node-shortcut'

export function openHermesForNode(hostname) {
  if (typeof hostname !== 'string' || !hostname.trim()) return
  window.dispatchEvent(new CustomEvent(hermesNodeShortcutEvent, { detail: hostname.trim() }))
}

// Keep the user's unfinished instructions; never silently truncate a draft.
export function appendHermesNode(draft, hostname) {
  const text = draft + (draft && !/\s$/.test(draft) ? ' ' : '') + hostname + ' '
  return text.length <= 8000 ? text : null
}

export async function copyHermesNode(hostname) {
  try {
    await navigator.clipboard.writeText(hostname)
    return true
  } catch { return false }
}
