import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'

import ChatAssistant from '../ChatAssistant.vue'
import { useUI } from '../../stores/ui'

const get = vi.fn()
const post = vi.fn()

vi.mock('../../api', () => ({
  api: {
    get: (...a) => get(...a),
    post: (...a) => post(...a),
    del: vi.fn(),
  },
  streamPost: vi.fn(),
}))

function statusResponses({ enabled = true, provider = 'claude' } = {}) {
  get.mockImplementation(async (path) => {
    if (path === '/ai/chat-settings') return { stream: false }
    if (path === '/ai/status') return { enabled, provider }
    return {}
  })
}

function mountChat() {
  return mount(ChatAssistant, {
    global: {
      plugins: [createPinia()],
      stubs: { 'router-link': { template: '<a><slot/></a>' } },
    },
  })
}

async function flush() {
  for (let i = 0; i < 6; i++) await nextTick()
}

beforeEach(() => {
  setActivePinia(createPinia())
  get.mockReset()
  post.mockReset()
})

describe('ChatAssistant provider status', () => {
  it('asks the status endpoint on mount', async () => {
    statusResponses()
    mountChat()
    await flush()

    expect(get).toHaveBeenCalledWith('/ai/status')
  })

  it('asks again each time the panel is opened', async () => {
    statusResponses()
    const wrapper = mountChat()
    await flush()
    const afterMount = get.mock.calls.filter((c) => c[0] === '/ai/status').length

    useUI().toggleAssistant()
    await flush()

    const afterOpen = get.mock.calls.filter((c) => c[0] === '/ai/status').length
    expect(afterOpen).toBe(afterMount + 1)
    wrapper.unmount()
  })

  it('stays optimistic when the status call fails', async () => {
    get.mockImplementation(async (path) => {
      if (path === '/ai/status') throw new Error('offline')
      return { stream: false }
    })
    const wrapper = mountChat()
    await flush()

    // A slow or failing status probe must not lock a working chat.
    expect(wrapper.html()).not.toContain('No AI provider is set up yet')
  })
})

describe('ChatAssistant mid-chat provider loss', () => {
  async function sendAndFail(status) {
    statusResponses({ enabled: true })
    const err = new Error('no provider configured')
    err.status = status
    post.mockRejectedValue(err)

    const wrapper = mountChat()
    await flush()
    useUI().toggleAssistant()
    await flush()

    await wrapper.vm.send('what can I cook?')
    await flush()
    return wrapper
  }

  it('flips back to the setup state on a 503', async () => {
    const wrapper = await sendAndFail(503)

    expect(wrapper.html()).toContain('No AI provider is set up yet')
  })

  it('pops the orphaned user message on a 503', async () => {
    const wrapper = await sendAndFail(503)

    expect(wrapper.html()).not.toContain('what can I cook?')
  })

  it('keeps the message and shows an error bubble for other failures', async () => {
    const wrapper = await sendAndFail(500)

    expect(wrapper.html()).toContain('what can I cook?')
    expect(wrapper.html()).toContain('no provider configured')
  })
})
