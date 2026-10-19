export function eligibleAgents(servers) { return servers.filter(node => node.collection_mode === 'agent') }
export function selectedAgents(servers, ids) {
  const chosen = new Set(ids.map(Number))
  return eligibleAgents(servers).filter(node => chosen.has(node.id))
}
export function parseBatchIds(value) {
  return [...new Set(String(value || '').split(',').map(Number).filter(id => Number.isSafeInteger(id) && id > 0))].slice(0, 500)
}
