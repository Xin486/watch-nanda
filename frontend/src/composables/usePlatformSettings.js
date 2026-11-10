import { ref } from 'vue'
import { api } from '../api'

const platform = ref({ branding: {}, hermes: { enabled: false, model: '' } })
let pending
export function usePlatformSettings() {
  async function refreshPlatform() {
    if (pending) return pending
    pending = api.platform().then(({ data }) => { platform.value = data }).finally(() => { pending = undefined })
    return pending
  }
  return { platform, refreshPlatform }
}
