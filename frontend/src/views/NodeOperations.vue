<template>
  <NodeAccessLayout :access="access" title="节点运维命令行" description="测试 SSH 连通性，使用已配置凭据，或验证并同步新的用户名和密码。" login-purpose="测试 SSH 或进入命令行">
    <RemoteTerminal v-if="access.node" :key="access.node.id" :node="access.node" :access="access" :auto-connect="route.query.action === 'terminal'" @busy="activity = $event"/>
  </NodeAccessLayout>
</template>
<script setup>
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { useNodeAccess } from '../composables/useNodeAccess'
import NodeAccessLayout from '../components/NodeAccessLayout.vue'
import RemoteTerminal from '../components/RemoteTerminal.vue'
const route = useRoute(), activity = ref(false)
const access = useNodeAccess({ activity })
</script>
