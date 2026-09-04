import { describe, expect, test, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'

// The route-leave guard is the thing under test, so the router mock has to
// hand us the registered callback instead of swallowing it.
const push = vi.fn()
const replace = vi.fn()
let leaveGuard = null
vi.mock('vue-router', () => ({
  useRouter: () => ({ push, replace }),
  useRoute: () => ({ path: '/import', query: { mode: 'text' } }),
  onBeforeRouteLeave: (fn) => { leaveGuard = fn },
}))

const put = vi.fn().mockResolvedValue({})
// Default import result: two proposals — enough to exercise the plural copy.
let doneEvent
vi.mock('../../api', () => ({
  api: { put: (...a) => put(...a), uploadPost: vi.fn() },
  streamPost: (url, body, onEvent) => { onEvent(doneEvent); return Promise.resolve() },
}))

import RecipeImport from '../RecipeImport.vue'

const proposal = (display) => ({
  display, quantity: 25, unit: 'g', food: 'butter', note: '', confidence: 0.7,
})

// Runs an import through to the review screen and returns the wrapper.
async function mountAtReview(overrides = {}) {
  doneEvent = {
    type: 'done',
    id: 7,
    name: 'Lemon Drizzle Cake',
    ingredients: [],
    warnings: [],
    ingredientProposals: [proposal('a good knob of butter'), proposal('a splash of milk')],
    ...overrides,
  }
  const w = mount(RecipeImport, { global: { plugins: [createPinia()] } })
  await w.find('textarea').setValue('Lemon Drizzle Cake\na good knob of butter')
  await w.findAll('button').find((b) => b.text() === 'Import').trigger('click')
  await flushPromises()
  return w
}

const confirmSpy = vi.fn()

beforeEach(() => {
  push.mockClear()
  put.mockClear()
  confirmSpy.mockReset()
  leaveGuard = null
  vi.stubGlobal('confirm', confirmSpy)
})

describe('RecipeImport review abandonment guard', () => {
  test('leaving with pending proposals asks, naming the recipe and the cost', async () => {
    confirmSpy.mockReturnValue(false)
    await mountAtReview()
    expect(leaveGuard()).toBe(false)   // cancel = stay on the review
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    const msg = confirmSpy.mock.calls[0][0]
    expect(msg).toContain('Lemon Drizzle Cake')
    expect(msg).toContain('2 suggested ingredient fixes will be discarded')
  })

  test('singular copy for a single proposal', async () => {
    confirmSpy.mockReturnValue(false)
    await mountAtReview({ ingredientProposals: [proposal('a good knob of butter')] })
    leaveGuard()
    expect(confirmSpy.mock.calls[0][0]).toContain('1 suggested ingredient fix will be discarded')
  })

  test('confirming leave lets navigation through and does not ask twice', async () => {
    confirmSpy.mockReturnValue(true)
    await mountAtReview()
    expect(leaveGuard()).toBe(true)
    // A redirect chain can re-run the guard; the first confirm settled it.
    expect(leaveGuard()).toBe(true)
    expect(confirmSpy).toHaveBeenCalledTimes(1)
  })

  test('leaving after "Leave them as written" does not ask', async () => {
    const w = await mountAtReview()
    await w.findAll('button').find((b) => b.text() === 'Leave them as written').trigger('click')
    expect(push).toHaveBeenCalledWith('/recipes/7')
    expect(leaveGuard()).toBe(true)
    expect(confirmSpy).not.toHaveBeenCalled()
  })

  test('leaving after "Use these" does not ask', async () => {
    const w = await mountAtReview()
    await w.findAll('button').find((b) => b.text() === 'Use these').trigger('click')
    await flushPromises()
    expect(put).toHaveBeenCalled()
    expect(push).toHaveBeenCalledWith('/recipes/7')
    expect(leaveGuard()).toBe(true)
    expect(confirmSpy).not.toHaveBeenCalled()
  })

  test('a warnings-only review (nothing to discard) never asks', async () => {
    await mountAtReview({ ingredientProposals: [], warnings: ['No timings found'] })
    expect(leaveGuard()).toBe(true)
    expect(confirmSpy).not.toHaveBeenCalled()
  })

  test('leaving before any import never asks', () => {
    mount(RecipeImport, { global: { plugins: [createPinia()] } })
    expect(leaveGuard()).toBe(true)
    expect(confirmSpy).not.toHaveBeenCalled()
  })
})
