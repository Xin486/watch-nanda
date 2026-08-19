/**
 * 统一的后端 API 请求封装
 * --------------------------------
 * 前后端部署在同一台机器上：
 * - 前端 Vite   : http://<主机>:3000
 * - 后端 FastAPI : http://<主机>:7980
 * 因此只需取当前页面主机名 + 固定端口即可定位后端。
 */
import axios from 'axios'

const http = axios.create({
  baseURL: `http://${window.location.hostname}:7980`,
  timeout: 15000,
})

export const api = {
  /** 获取服务器列表（支持 { group } 分组过滤） */
  getServers: (params) => http.get('/api/servers', { params }),

  /** 新增服务器节点 */
  addServer: (data) => http.post('/api/servers/add', data),

  /** 更新节点基本信息 */
  updateServer: (id, data) => http.post(`/api/server/${id}/update`, data),

  /** 彻底删除节点及其全部历史数据 */
  deleteServer: (id) => http.delete(`/api/server/${id}`),

  /** 获取历史负载（days：查询天数，>7 自动降采样） */
  getHistory: (id, days) => http.get(`/api/server/${id}/history`, { params: { days } }),

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
