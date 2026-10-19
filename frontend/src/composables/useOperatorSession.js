import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '../api'

export function useOperatorSession() {
  const secure = location.protocol === 'https:'
  const authenticated = ref(false), password = ref(''), busy = ref(false), error = ref('')
  let generation = 0, disposed = false
  function handleError(e) {
    error.value = typeof e.response?.data?.detail === 'string' ? e.response.data.detail : '操作失败，请检查连接后重试。'
    if (e.response?.status === 401) { authenticated.value = false; password.value = ''; generation++ }
  }
  async function login() {
    if (!secure || busy.value) return
    const current = ++generation
    busy.value = true; error.value = ''
    try {
      await api.opsLogin(password.value)
      if (!disposed && current === generation) authenticated.value = true
    } catch (e) { if (!disposed && current === generation) handleError(e) }
    finally { password.value = ''; if (!disposed) busy.value = false }
  }
  async function logout() {
    generation++; authenticated.value = false; password.value = ''; error.value = ''
    try { await api.opsLogout() } catch (e) { if (!disposed) handleError(e) }
  }
  onMounted(async () => {
    if (!secure) return
    const current = generation
    try { await api.opsSession(); if (!disposed && current === generation) authenticated.value = true }
    catch (e) { if (!disposed && e.response?.status !== 401) handleError(e) }
  })
  onBeforeUnmount(() => { disposed = true; generation++; password.value = '' })
  return reactive({ secure, authenticated, password, busy, error, login, logout, handleError })
}
