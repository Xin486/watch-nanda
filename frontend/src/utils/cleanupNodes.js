const nameKey = value => String(value || '').normalize('NFKC').trim().toLocaleLowerCase('zh-CN')

export function cleanupInventory(servers) {
  const counts = new Map()
  for (const server of servers) {
    const key = nameKey(server.hostname)
    if (key) counts.set(key, (counts.get(key) || 0) + 1)
  }
  const rows = servers.map(server => ({ ...server, duplicateCount: counts.get(nameKey(server.hostname)) || 0 }))
  return {
    rows,
    duplicateGroups: [...counts.values()].filter(count => count > 1).length,
    duplicateNodes: rows.filter(server => server.duplicateCount > 1).length,
  }
}

export function collectionLabel(server) {
  return server.collection_mode === 'agent' ? 'Agent' : 'SSH'
}

export function connectionAddress(server) {
  const host = server.ip_address || '未配置地址'
  return `${host.includes(':') ? `[${host}]` : host}:${server.ssh_port || 22}`
}

export function serverIdentity(server) {
  return `${server.hostname}\n节点 ID：${server.id}\n连接地址：${connectionAddress(server)}\n采集方式：${collectionLabel(server)}\n分组：${server.group_name || '未分组'}`
}

export function filterCleanupNodes(rows, query = '', duplicatesOnly = false) {
  const search = nameKey(query)
  return rows.filter(server => (!duplicatesOnly || server.duplicateCount > 1) && (!search || [
    server.hostname, server.id, connectionAddress(server), server.group_name || '未分组', collectionLabel(server),
  ].some(value => nameKey(value).includes(search))))
    .sort((left, right) => Number(right.duplicateCount > 1) - Number(left.duplicateCount > 1)
      || nameKey(left.hostname).localeCompare(nameKey(right.hostname), 'zh-CN') || left.id - right.id)
}
