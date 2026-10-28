/**
 * API 和 SSE 统一通过同源 HTTPS 入口，不再直连后端端口。
 */
import axios from 'axios'
import { secureOrigin } from './utils/transport'

const http = axios.create({
  baseURL: secureOrigin(),
  timeout: 15000,
})

export const api = {
  /** 获取服务器列表（支持 { group } 分组过滤） */
  getServers: (params) => http.get('/api/servers', { params }),

  /** 新增服务器节点 */
  addServer: (data) => http.post('/api/servers/add', data),

  /** 更新节点基本信息 */
  updateServer: (id, data) => http.post(`/api/server/${id}/update`, data),
  sshProfiles: () => http.get('/api/ops/ssh-profiles'),
  createSshProfile: data => http.post('/api/ops/ssh-profiles', data),
  updateSshProfile: (id, data) => http.put('/api/ops/ssh-profiles/'+id, data),
  deleteSshProfile: id => http.delete('/api/ops/ssh-profiles/'+id),
  agentBatches: () => http.get('/api/ops/agent-batches'),
  agentBatch: (id, options = {}) => http.get('/api/ops/agent-batches/'+id, options),
  agentBatchNode: (batch, node) => http.get('/api/ops/agent-batches/'+batch+'/nodes/'+node),
  agentBatchSnapshot: (id, node, options = {}) => http.get('/api/ops/agent-batches/'+id+'/snapshot', { ...options, params: node ? { log_node: node } : {} }),
  createAgentBatch: serverIds => http.post('/api/ops/agent-batches', { server_ids: serverIds }),
  retryAgentBatchNode: (batch, node, data = {}) => http.post('/api/ops/agent-batches/'+batch+'/nodes/'+node+'/retry', data),
  retryAgentBatchWaiting: id => http.post('/api/ops/agent-batches/'+id+'/retry-waiting'),
  stopAgentBatch: id => http.post('/api/ops/agent-batches/'+id+'/stop'),
  agentHealth: (id, options = {}) => http.get(`/api/server/${id}/agent-health`, options),
  setNodeNotifications: (id, enabled) => http.post(`/api/server/${id}/notifications`, { enabled }),
  opsSession: () => http.get('/api/ops/session'),
  opsLogin: (password) => http.post('/api/ops/login', { password }),
  opsLogout: () => http.post('/api/ops/logout'),
  sshTest: (id, data) => http.post(`/api/ops/nodes/${id}/ssh-test`, data, { timeout: 45000 }),
  trustHost: (id, fingerprint, confirmation = {}) => http.post(`/api/ops/nodes/${id}/trust-host`, { fingerprint, ...confirmation }, { timeout: 30000 }),
  deployAgent: (id, data) => http.post(`/api/ops/nodes/${id}/deploy`, data, { timeout: 45000 }),
  redeployAgent: (id, data) => http.post(`/api/ops/nodes/${id}/redeploy`, data, { timeout: 45000 }),
  deploymentJob: (id, options = {}) => http.get(`/api/ops/jobs/${id}`, options),
  deploymentJobs: (serverId) => http.get('/api/ops/jobs', { params: { server_id: serverId } }),

  /** 彻底删除节点及其全部历史数据 */
  deleteServer: (id) => http.delete(`/api/server/${id}`),

  /** 获取历史负载（days：查询天数，>7 自动降采样） */
  getHistory: (id, days, options = {}) => http.get(`/api/server/${id}/history`, { ...options, params: { days } }),

  /** 清空单台机器的历史数据 */
  clearHistory: (id) => http.delete(`/api/server/${id}/history`),

  /** 获取告警规则配置 */
  getAlertSettings: () => http.get('/api/settings/alert'),

  /** 保存告警规则配置 */
  saveAlertSettings: (data) => http.post('/api/settings/alert', data),

  /** 获取 SMTP 发件配置 */
  getEmailSettings: () => http.get('/api/settings/email'),

  /** 保存 SMTP 发件配置 */
  saveEmailSettings: (data) => http.post('/api/settings/email', data),

  /** 发送测试邮件 */
  testEmail: () => http.post('/api/settings/email/test'),

  /** 获取股票/板块 K 线（后端代理东方财富，参数如 { symbol: '90.BK1036', klt: 101, lmt: 120 }） */
  getMarketKline: (params) => http.get('/api/market/kline', { params }),
}

export default http
