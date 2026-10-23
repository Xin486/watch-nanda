import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useServers } from './useServers'

// Both pages share authentication and SSH checks, but keep their actions separate.
export function useNodeAccess({ selectable = true, activity = ref(false) } = {}) {
  const route = useRoute(), router = useRouter(), { servers, subscribe, patchServer } = useServers()
  const secure = location.protocol === 'https:'
  const authenticated = ref(false), operatorPassword = ref(''), loggingIn = ref(false), error = ref('')
  const selectedId = ref(String(route.params.id || '')), useSaved = ref(false)
  const credentials = ref({ username: '', password: '' })
  const check = ref(null), trustConfirmed = ref(false), testing = ref(false)
  const trustDialogOpen = ref(false)
  const node = computed(() => servers.value.find(s => String(s.id) === selectedId.value))
  const busy = computed(() => testing.value || activity.value)
  let generation = 0, authGeneration = 0, disposed = false, autoCheckDone = false
  let retryAfterTrust = null

  function cancelHostTrust() {
    trustDialogOpen.value = false
    trustConfirmed.value = false
    retryAfterTrust = null
  }

  function handleError(e) {
    error.value = typeof e.response?.data?.detail === 'string' ? e.response.data.detail : '操作失败，请检查网络和输入后重试。'
    if (e.response?.status === 401) {
      authenticated.value = false
      authGeneration++
      credentials.value.password = ''
      generation++
      testing.value = false
      cancelHostTrust()
    }
  }
  function validInput() {
    if (!secure || !authenticated.value || !node.value) return false
    if (!useSaved.value && (!credentials.value.username.trim() || !credentials.value.password)) {
      error.value = '请填写 SSH 用户名和密码。'
      return false
    }
    return true
  }
  function payload() {
    return useSaved.value ? { use_saved: true } : {
      use_saved: false, username: credentials.value.username.trim(), password: credentials.value.password,
      save_credentials: true,
    }
  }
  function connectionResult(data, retry = null) {
    if (data.credentials_saved) {
      patchServer(data.server)
      useSaved.value = true
      credentials.value.password = ''
    }
    check.value = data
    retryAfterTrust = retry
    trustDialogOpen.value = data.code === 'host_key_changed' && !!data.fingerprint && !!data.previous_fingerprint
    window.dispatchEvent(new CustomEvent('servers-refresh'))
  }
  async function testConnection() {
    if (testing.value || !validInput()) return false
    testing.value = true
    error.value = ''
    cancelHostTrust()
    const current = generation, id = node.value.id
    try {
      const { data } = await api.sshTest(id, payload())
      if (disposed || current !== generation) return false
      connectionResult(data)
      return data.ok
    } catch (e) {
      if (!disposed && current === generation) handleError(e)
      return false
    } finally {
      if (current === generation) testing.value = false
    }
  }
  async function trustHost() {
    if (busy.value || !secure || !authenticated.value || !trustConfirmed.value || !check.value?.fingerprint || !node.value) return
    const current = generation
    const result = check.value, retry = retryAfterTrust, confirmation = {}
    if (result.code === 'host_key_changed') {
      if (!result.previous_fingerprint) return
      confirmation.previous_fingerprint = result.previous_fingerprint
    }
    if (result.host) confirmation.expected_host = result.host
    if (result.port) confirmation.expected_port = result.port
    testing.value = true
    error.value = ''
    try {
      await api.trustHost(node.value.id, result.fingerprint, confirmation)
      if (disposed || current !== generation) return
      testing.value = false
      cancelHostTrust()
      if (retry) await retry()
      else await testConnection()
    } catch (e) {
      if (!disposed && current === generation) handleError(e)
    } finally {
      if (current === generation) testing.value = false
    }
  }
  async function confirmHostChange() {
    if (busy.value || !trustDialogOpen.value || check.value?.code !== 'host_key_changed') return
    trustConfirmed.value = true
    await trustHost()
  }
  async function maybeAutoCheck() {
    if (!autoCheckDone && route.query.action === 'check' && authenticated.value && node.value?.has_ssh_password) {
      autoCheckDone = true
      await testConnection()
    }
  }
  async function login() {
    if (loggingIn.value || !secure) return
    loggingIn.value = true
    error.value = ''
    const current = authGeneration
    try {
      await api.opsLogin(operatorPassword.value)
      if (disposed || current !== authGeneration) return
      authenticated.value = true
      await maybeAutoCheck()
    } catch (e) { if (!disposed) handleError(e) }
    finally { operatorPassword.value = ''; loggingIn.value = false }
  }
  async function logout() {
    authenticated.value = false
    authGeneration++
    operatorPassword.value = ''
    credentials.value.password = ''
    generation++
    testing.value = false
    cancelHostTrust()
    check.value = null
    try { await api.opsLogout() } catch (e) { if (!disposed) handleError(e) }
  }
  watch(selectedId, value => {
    if (selectable && value !== String(route.params.id || '')) router.replace(value ? `/nodes/${value}/operations` : '/operations')
  })
  watch(() => route.params.id, id => { selectedId.value = String(id || '') })
  watch(() => JSON.stringify([node.value?.id, node.value?.ip_address || node.value?.hostname, node.value?.ssh_port || 22]), async () => {
    generation++
    testing.value = false
    check.value = null
    cancelHostTrust()
    error.value = ''
    credentials.value = { username: node.value?.ssh_user || 'root', password: '' }
    useSaved.value = !!node.value?.has_ssh_password
    autoCheckDone = false
    await maybeAutoCheck()
  }, { immediate: true })
  watch([useSaved, () => credentials.value.username, () => credentials.value.password], () => {
    check.value = null
    cancelHostTrust()
  }, { flush: 'sync' })
  onMounted(async () => {
    subscribe()
    if (!secure) return
    const current = authGeneration
    try {
      await api.opsSession()
      if (disposed || current !== authGeneration) return
      authenticated.value = true
      await maybeAutoCheck()
    } catch (e) { if (!disposed && e.response?.status !== 401) handleError(e) }
  })
  onBeforeUnmount(() => {
    disposed = true
    generation++
    authGeneration++
    operatorPassword.value = ''
    credentials.value.password = ''
    cancelHostTrust()
  })
  return reactive({
    secure, authenticated, operatorPassword, loggingIn, error, selectedId, servers, node, useSaved,
    credentials, check, trustConfirmed, testing, busy, login, logout, testConnection, trustHost,
    handleError, validInput, payload, connectionResult, trustDialogOpen, cancelHostTrust, confirmHostChange,
  })
}
