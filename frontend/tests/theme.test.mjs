import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { THEME_KEY, readTheme, applyTheme, saveTheme } from '../src/utils/theme.js'

function environment(saved, dark = false) {
  const values = new Map(saved == null ? [] : [[THEME_KEY, saved]]), listeners = new Map()
  const browser = { document: { documentElement: { dataset: {} } }, matchMedia: () => ({ matches: dark }),
    localStorage: { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) },
    addEventListener: (name, cb) => listeners.set(name, cb) }
  return { browser, values, listeners }
}

test('saved theme wins; first visit and invalid values follow the system', () => {
  for (const [saved, system, expected] of [['light',true,'light'],['dark',false,'dark'],[null,true,'dark'],[null,false,'light'],['unexpected',false,'light']]) {
    assert.equal(readTheme(environment(saved, system).browser), expected)
  }
})

test('blocked storage does not prevent applying or changing the palette', () => {
  const { browser } = environment(null, true)
  Object.defineProperty(browser, 'localStorage', { get() { throw Error('blocked') } })
  assert.equal(readTheme(browser), 'dark')
  applyTheme('light', browser); saveTheme('light', browser)
  assert.equal(browser.document.documentElement.dataset.theme, 'light')
})

test('toggle applies immediately, survives reload and synchronizes other tabs', () => {
  const { browser, values, listeners } = environment('light')
  const context = { window: browser, THEME_KEY, readTheme: () => readTheme(browser), applyTheme: t => applyTheme(t,browser), saveTheme: t => saveTheme(t,browser),
    ref: value => ({ value }), computed: get => ({ get value() { return get() } }) }
  vm.createContext(context)
  const code = fs.readFileSync(new URL('../src/composables/useTheme.js', import.meta.url),'utf8').replace(/^import .*$/gm,'').replaceAll('export function ','function ')
  vm.runInContext(code+'\nthis.useTheme = useTheme', context)
  const a = context.useTheme(), b = context.useTheme()
  a.toggleTheme()
  assert.equal(a.isDark.value, true); assert.equal(b.isDark.value, true)
  assert.equal(browser.document.documentElement.dataset.theme, 'dark')
  assert.equal(readTheme(browser), 'dark')
  values.set(THEME_KEY, 'light'); listeners.get('storage')({ key: 'companyName' })
  assert.equal(a.isDark.value, true)
  listeners.get('storage')({ key: THEME_KEY })
  assert.equal(a.isDark.value, false)
  values.clear(); listeners.get('storage')({ key: null })
  assert.equal(a.isDark.value, false)
})

test('initial paint uses the stored palette before the application mounts', () => {
  const markup = fs.readFileSync(new URL('../index.html',import.meta.url),'utf8')
  const script = markup.match(/<script>([\s\S]*?)<\/script>/)[1]
  const { browser } = environment('dark')
  vm.runInNewContext(script, browser)
  assert.equal(browser.document.documentElement.dataset.theme, 'dark')
})

const luminance = color => {
  const rgb = color.slice(1).match(/../g).map(v => parseInt(v,16)/255).map(v => v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4)
  return rgb[0]*.2126 + rgb[1]*.7152 + rgb[2]*.0722
}
test('both palettes retain readable small text, states and selected navigation', () => {
  const css = fs.readFileSync(new URL('../src/styles/theme.css',import.meta.url),'utf8')
  const tokens = block => Object.fromEntries([...block.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6});/g)].map(m => [m[1],m[2]]))
  const light = tokens(css.match(/:root \{([\s\S]*?)\}/)[1]), dark = {...light,...tokens(css.match(/data-theme="dark"\] \{([\s\S]*?)\}/)[1])}
  for (const [name, palette] of [['light',light],['dark',dark]]) {
    for (const [foreground, backgrounds] of [['text',['page-bg','surface','surface-soft','sidebar-bg']],['text-muted',['surface','surface-soft','sidebar-bg']],['accent',['surface','accent-soft','nav-active-bg']],['on-primary',['primary-bg']],['success',['success-bg']],['warning',['warning-bg']],['danger',['danger-bg']]]) {
      for (const background of backgrounds) {
        const x = luminance(palette[foreground]), y = luminance(palette[background]), ratio = (Math.max(x,y)+.05)/(Math.min(x,y)+.05)
        assert.ok(ratio >= 4.5, `${name} ${foreground}/${background}: ${ratio.toFixed(2)}`)
      }
    }
  }
})
