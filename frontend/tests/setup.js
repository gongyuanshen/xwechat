import { beforeEach, vi } from 'vitest'

const { createPinia, setActivePinia } = await vi.importActual('pinia')
const { ref } = await vi.importActual('vue')

beforeEach(() => {
  setActivePinia(createPinia())
  vi.stubGlobal('ref', ref)
})

// happy-dom does not implement window.confirm, alert or prompt; tests spy on them and decide the answer themselves.
for (const name of ['confirm', 'alert', 'prompt']) {
  if (typeof window[name] !== 'function') window[name] = () => (name === 'confirm' ? false : name === 'prompt' ? null : undefined)
}
