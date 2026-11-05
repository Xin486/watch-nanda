<template>
  <p v-if="!access.secure" class="management-error" role="alert">请通过 HTTPS 打开此页面。</p>
  <div v-else-if="!access.authenticated" class="management-card" role="status">
    <p>{{ access.error ? '连接未就绪，请重试。' : '正在准备页面…' }}</p>
    <button v-if="access.error" class="management-primary" :disabled="access.busy" @click="access.login">重新连接</button>
  </div>
  <slot v-else />
  <p v-if="access.error" class="management-error" role="alert">{{ access.error }}</p>
</template>
<script setup>
defineProps({ access: { type: Object, required: true } })
</script>
