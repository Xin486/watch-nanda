<template>
  <main class="agent-management">
    <router-link to="/settings">← 返回设置</router-link><h1>常用 SSH 用户名和密码</h1>
    <p>批量重部署按优先级从小到大尝试启用的凭据，再尝试节点已存凭据；同一用户名最多自动尝试 3 次，仍无法认证时等待手动输入。</p>
    <OperatorGate :access="access">
      <section class="management-card">
        <div class="management-toolbar"><h2>常用凭据</h2><button :disabled="loading || saving" @click="load">刷新列表</button></div>
        <p>密码在服务器端加密保存，不在页面回显。修改时密码留空表示保留；修改用户名需重新输入密码。</p>
        <div class="management-table-wrap"><table class="management-table"><thead><tr><th>名称</th><th>用户名</th><th>优先级</th><th>状态</th><th>操作</th></tr></thead><tbody>
          <tr v-for="profile in profiles" :key="profile.id"><td>{{ profile.name }}</td><td>{{ profile.username }}</td><td>{{ profile.priority }}</td><td>{{ profile.enabled ? '启用' : '停用' }} · {{ profile.has_password ? '密码已配置' : '未配置密码' }}</td><td><button :disabled="saving" @click="edit(profile)">编辑</button><button :disabled="saving" @click="remove(profile)">删除</button></td></tr>
          <tr v-if="!loading && !profiles.length"><td colspan="5">尚未添加常用凭据，批量队列将尝试节点已存凭据或提示手动输入。</td></tr>
        </tbody></table></div>
      </section>
      <form class="management-card" @submit.prevent="save">
        <h2>{{ editing ? '编辑常用凭据' : '新增常用凭据' }}</h2>
        <fieldset class="management-fields" :disabled="saving"><label>名称<input v-model="form.name" maxlength="80" required placeholder="例如：机房管理员"></label><label>SSH 用户名<input v-model="form.username" maxlength="50" required autocomplete="off"></label><label>SSH 密码<input v-model="form.password" type="password" :required="!editing" maxlength="1024" autocomplete="new-password" :placeholder="editing ? '留空保留已存密码' : '请输入 SSH 密码'"></label><label>优先级（小的先尝试）<input v-model.number="form.priority" type="number" min="1" max="999" required></label></fieldset>
        <label class="management-check"><input v-model="form.enabled" type="checkbox">在批量队列中启用</label>
        <div class="management-toolbar"><button class="management-primary" :disabled="saving || !loaded">{{ saving ? '正在保存…' : '加密保存凭据' }}</button><button v-if="editing" type="button" :disabled="saving" @click="reset">取消编辑</button></div>
        <p v-if="message" class="management-success" role="status">{{ message }}</p>
      </form>
    </OperatorGate>
  </main>
</template>
<script setup>
import { onBeforeUnmount, reactive, ref, watch } from 'vue'
import { api } from '../api'
import { useOperatorSession } from '../composables/useOperatorSession'
import OperatorGate from '../components/OperatorGate.vue'
import '../styles/agent-management.css'

const access = useOperatorSession()
const profiles = ref([]), loading = ref(false), loaded = ref(false), saving = ref(false), editing = ref(''), message = ref('')
const form = reactive({ name: '', username: '', password: '', priority: 100, enabled: true })
let generation = 0, disposed = false
function reset() { editing.value = ''; Object.assign(form, { name: '', username: '', password: '', priority: 100, enabled: true }) }
function edit(profile) { reset(); editing.value = profile.id; Object.assign(form, { name: profile.name, username: profile.username, priority: profile.priority, enabled: profile.enabled }); message.value = '' }
async function load() {
  const current = ++generation
  loading.value = true; access.error = ''
  try { const { data } = await api.sshProfiles(); if (!disposed && current === generation) { profiles.value = data; loaded.value = true } }
  catch (e) { if (!disposed && current === generation) access.handleError(e) }
  finally { if (current === generation) loading.value = false }
}
async function save() {
  if (saving.value || !loaded.value) return
  const current = generation, id = editing.value, body = { name: form.name, username: form.username, priority: form.priority, enabled: form.enabled }
  if (form.password) body.password = form.password
  saving.value = true; access.error = ''; message.value = ''
  try {
    await (id ? api.updateSshProfile(id, body) : api.createSshProfile(body))
    if (!disposed && current === generation && access.authenticated) { reset(); message.value = '凭据已加密保存'; await load() }
  } catch (e) { if (!disposed && current === generation) access.handleError(e) }
  finally { delete body.password; form.password = ''; if (!disposed) saving.value = false }
}
async function remove(profile) {
  if (!window.confirm(`确定删除常用凭据「${profile.name}」？节点已存凭据不会被删除。`)) return
  saving.value = true; const current = generation
  try { await api.deleteSshProfile(profile.id); if (!disposed && current === generation && access.authenticated) { reset(); await load() } }
  catch (e) { if (!disposed && current === generation) access.handleError(e) }
  finally { if (!disposed) saving.value = false }
}
watch(() => access.authenticated, authenticated => {
  generation++; profiles.value = []; loaded.value = false; reset(); message.value = ''
  if (authenticated) load()
}, { immediate: true })
onBeforeUnmount(() => { disposed = true; generation++; reset() })
</script>
