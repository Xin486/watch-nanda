import test from 'node:test'
import assert from 'node:assert/strict'
import { httpsPageUrl, secureOrigin, ensureHttps } from '../src/utils/transport.js'

test('old HTTP bookmarks upgrade to HTTPS without losing route or query', () => {
  for (const port of ['', ':3000', ':7980']) {
    assert.equal(httpsPageUrl(`http://192.168.1.24${port}/agent-deploy?id=73#terminal`).href,
      'https://192.168.1.24/agent-deploy?id=73#terminal')
  }
})

test('HTTPS origins retain custom ports and IPv6 addresses', () => {
  assert.equal(httpsPageUrl('https://[::1]:3443/path').origin, 'https://[::1]:3443')
  assert.equal(httpsPageUrl('https://example.com:3000/path').origin, 'https://example.com:3000')
})

test('HTTP never supplies an API origin or mounts the application', () => {
  let redirected
  globalThis.window = { location: { protocol: 'http:', href: 'http://192.168.1.24:3000/?group=a', replace: url => { redirected = url } } }
  try {
    assert.equal(secureOrigin(), 'https://192.168.1.24')
    assert.equal(ensureHttps(), false)
    assert.equal(redirected, 'https://192.168.1.24/?group=a')
    window.location = { protocol: 'https:', href: 'https://192.168.1.24/' }
    assert.equal(ensureHttps(), true)
  } finally {
    delete globalThis.window
  }
})
