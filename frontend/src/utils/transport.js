// Upgrade old HTTP bookmarks while retaining the requested page and query.
export function httpsPageUrl(href) {
  const url = new URL(href)
  if (url.protocol === 'http:') {
    url.protocol = 'https:'
    if (['3000', '7980'].includes(url.port)) url.port = ''
  }
  return url
}

export function secureOrigin() {
  return httpsPageUrl(window.location.href).origin
}

export function ensureHttps() {
  if (window.location.protocol === 'https:') return true
  window.location.replace(httpsPageUrl(window.location.href).href)
  return false
}
