import { describe, expect, test, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useUI } from '../../stores/ui'

// The subscription card is the unit under test; the rest of the page just needs
// to render, so the plan/recipe loads resolve empty.
const get = vi.fn()
const post = vi.fn()
const del = vi.fn()
vi.mock('../../api', () => ({
  api: {
    get: (...a) => get(...a),
    post: (...a) => post(...a),
    del: (...a) => del(...a),
  },
  apiUrl: (p) => `/api/v1${p}`,
}))

import MealPlan from '../MealPlan.vue'

const TOKEN = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const TOKEN2 = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'

function routeApi({ token = null, fail = false, entries = [] } = {}) {
  get.mockImplementation((url) => {
    if (url.startsWith('/calendar/subscription')) {
      return fail
        ? Promise.reject(new Error('Service unavailable'))
        : Promise.resolve({ token })
    }
    if (url.startsWith('/mealplans')) return Promise.resolve({ items: entries })
    return Promise.resolve({ items: [] })
  })
}

// A planned week. "Build shopping list" only holds the accent when there IS a
// plan to build from — with nothing planned it drops to secondary and the
// accent moves to the empty state's CTA, so the two cases assert different
// things and both have to be checked.
const PLANNED = [{
  id: 'e1', date: '2026-09-14', slot: 'dinner',
  recipe: { id: 'r1', name: 'Chili', slug: 'chili' },
}]

// The card is inline at the foot of the page and loads on mount, so there is
// nothing to open — mounting IS the arrange step.
async function mountPage(opts) {
  routeApi(opts)
  const pinia = createPinia()
  // Active so a test can read the ui store (toasts) that the component writes.
  setActivePinia(pinia)
  const w = mount(MealPlan, { global: { plugins: [pinia] } })
  await flushPromises()
  return w
}

const findButton = (w, text) =>
  w.findAll('button').find((b) => b.text().includes(text))
const feedField = (w) => w.find('textarea[readonly]')

// The link is masked until asked for, so most assertions need it revealed.
async function reveal(w) {
  const btn = findButton(w, 'Show link')
  if (btn) {
    await btn.trigger('click')
    await flushPromises()
  }
  return w
}

beforeEach(() => {
  vi.clearAllMocks()
  window.history.replaceState({}, '', '/')
  // jsdom has no clipboard by default; most tests want the happy path.
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
    writable: true,
  })
})

describe('meal plan calendar subscription', () => {
  test('the page head keeps exactly one primary action', async () => {
    const w = await mountPage({ token: TOKEN, entries: PLANNED })

    const head = w.find('.page-head')

    // Secondary buttons carry a class; the primary is the unclassed one. A
    // fourth head button was what pushed the primary onto a third row at 390px,
    // so the subscription UI deliberately lives in a card instead.
    const primaries = head.findAll('button').filter(
      (b) => !b.classes().some((c) => ['secondary', 'ghost', 'sm'].includes(c)),
    )
    expect(primaries).toHaveLength(1)
    expect(primaries[0].text()).toContain('Build shopping list')
  })

  test('an empty plan has exactly one primary, and it is the empty-state CTA',
    async () => {
      // The subscription card must not become the accent by default: with
      // nothing planned the head's action is deliberately demoted, so a fourth
      // unclassed button anywhere on the page would silently take over the
      // view's single primary.
      const w = await mountPage({ token: TOKEN, entries: [] })

      // Scoped to the two surfaces that compete for the accent: the head and
      // the empty state. (The rest of the page renders plain day-cell buttons
      // that are styled as cells, not actions — counting those would make this
      // assert something it does not mean.)
      const unstyled = (el) => el.findAll('button').filter(
        (b) => !b.classes().some((c) => ['secondary', 'ghost', 'sm'].includes(c)),
      )

      expect(unstyled(w.find('.page-head'))).toHaveLength(0)
      const cta = unstyled(w.find('.empty-state'))
      expect(cta).toHaveLength(1)
      expect(cta[0].text()).toMatch(/Plan your first meal|Add a meal/)
    })

  test('an unpublished household is offered the publish action', async () => {
    const w = await mountPage({ token: null })

    expect(w.text()).toContain('Publish calendar link')
    // First-run copy must teach the concept before asking for the action.
    expect(w.text()).toMatch(/keeps it in\s+sync/)
  })

  test('publishing shows the feed url', async () => {
    post.mockResolvedValue({ token: TOKEN })
    const w = await mountPage({ token: null })

    await findButton(w, 'Publish calendar link').trigger('click')
    await flushPromises()

    expect(post).toHaveBeenCalledWith('/calendar/subscription')
    // Publishing reveals automatically — this is the moment they must read it.
    expect(feedField(w).element.value).toContain(`/api/v1/calendar/${TOKEN}.ics`)
  })

  test('a published household sees the url straight away', async () => {
    const w = await reveal(await mountPage({ token: TOKEN }))

    expect(feedField(w).element.value).toContain(`${TOKEN}.ics`)
    expect(findButton(w, 'Copy')).toBeTruthy()
  })

  test('the url is absolute so it can be pasted into a calendar app', async () => {
    const w = await reveal(await mountPage({ token: TOKEN }))

    // A relative path is useless outside the browser session.
    expect(feedField(w).element.value).toMatch(/^https?:\/\//)
  })

  test('the whole token is visible rather than truncated', async () => {
    const w = await reveal(await mountPage({ token: TOKEN }))

    // A single-line input hides the token at every width, which also makes a
    // replaced link look identical to the old one. The field must wrap.
    const field = feedField(w)
    expect(field.element.tagName).toBe('TEXTAREA')
    expect(field.element.value).toContain(TOKEN)
  })

  test('the link is masked until asked for', async () => {
    const w = await mountPage({ token: TOKEN })

    // It is a bearer credential on a page opened daily, and meal-plan
    // screenshots are a routine support artifact.
    const value = feedField(w).element.value
    expect(value).not.toContain(TOKEN)
    expect(value).toContain('•')
    // The rest of the URL stays readable so it is still recognisable.
    expect(value).toContain('/api/v1/calendar/')
  })

  test('copy works while the link is masked', async () => {
    const w = await mountPage({ token: TOKEN })

    await findButton(w, 'Copy').trigger('click')
    await flushPromises()

    // The real URL is copied, not the mask.
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      expect.stringContaining(TOKEN))
  })

  test('copy reports failure instead of doing nothing on an insecure origin', async () => {
    // navigator.clipboard is undefined over plain http — which is exactly where
    // this feature sends people (http://<ha-host>:7850). Optional chaining made
    // the whole call short-circuit: no copy, no toast, no error at all.
    Object.defineProperty(navigator, 'clipboard', {
      value: undefined, configurable: true, writable: true,
    })
    const w = await mountPage({ token: TOKEN })

    await findButton(w, 'Copy').trigger('click')
    await flushPromises()

    // It tells the user (via a toast, which App.vue renders, so assert on the
    // store) and reveals the link so they can select it by hand.
    const ui = useUI()
    expect(ui.toasts.some((t) => /secure \(https\) connection/.test(t.message)))
      .toBe(true)
    expect(feedField(w).element.value).toContain(TOKEN)
  })

  test('under HA ingress the session-scoped url is NOT offered', async () => {
    window.history.replaceState({}, '', '/api/hassio_ingress/abc123/')

    const w = await reveal(await mountPage({ token: TOKEN }))

    const url = feedField(w).element.value
    // That path only resolves for a signed-in HA session; a calendar app
    // fetching it gets a login page, so handing it over would be a broken feed.
    expect(url).not.toContain('hassio_ingress')
    expect(url).toContain(':7850')
  })

  test('the ingress copy says how to fix the placeholder host', async () => {
    window.history.replaceState({}, '', '/api/hassio_ingress/abc123/')

    const w = await mountPage({ token: TOKEN })

    // The URL contains a placeholder, so Copy hands over something that needs
    // editing — the instructions must say so, and name the port prerequisite.
    expect(w.text()).toContain("machine's address")
    expect(w.text()).toContain('<home-assistant-host>')
    expect(w.text()).toContain('Network tab')
  })

  test('replacing the link asks first and names the consequence', async () => {
    const w = await mountPage({ token: TOKEN })

    await findButton(w, 'Replace link').trigger('click')
    await flushPromises()

    expect(post).not.toHaveBeenCalledWith('/calendar/subscription/rotate')
    expect(w.text()).toContain('stops working immediately')
    expect(w.text()).toContain('stops updating')
  })

  test('confirming the replacement rotates the token and shows the new url', async () => {
    const w = await mountPage({ token: TOKEN })
    post.mockResolvedValue({ token: TOKEN2 })

    await findButton(w, 'Replace link').trigger('click')
    await flushPromises()
    await w.findAll('button').filter((b) => b.text() === 'Replace link')
      .at(-1).trigger('click')
    await flushPromises()

    expect(post).toHaveBeenCalledWith('/calendar/subscription/rotate')
    // Rotating reveals automatically — the new link has to be handed out.
    expect(feedField(w).element.value).toContain(TOKEN2)
  })

  test('cancelling the replacement leaves the link alone', async () => {
    const w = await mountPage({ token: TOKEN })

    await findButton(w, 'Replace link').trigger('click')
    await flushPromises()
    await findButton(w, 'Cancel').trigger('click')
    await flushPromises()

    expect(post).not.toHaveBeenCalledWith('/calendar/subscription/rotate')
    expect(feedField(await reveal(w)).element.value).toContain(TOKEN)
  })

  test('stopping sharing asks first — it is the less reversible action', async () => {
    const w = await mountPage({ token: TOKEN })

    await findButton(w, 'Stop sharing').trigger('click')
    await flushPromises()

    // Guarding "replace" but not "stop" would put the seatbelt on the safer of
    // the two: a replaced link still leaves a working feed, a stopped one does
    // not and cannot be restored.
    expect(del).not.toHaveBeenCalled()
    expect(w.text()).toContain('cannot be')
  })

  test('confirming stop clears the url and returns to first run', async () => {
    del.mockResolvedValue({})
    const w = await mountPage({ token: TOKEN })

    await findButton(w, 'Stop sharing').trigger('click')
    await flushPromises()
    await w.findAll('button').filter((b) => b.text() === 'Stop sharing')
      .at(-1).trigger('click')
    await flushPromises()

    expect(del).toHaveBeenCalledWith('/calendar/subscription')
    expect(feedField(w).exists()).toBe(false)
    expect(w.text()).toContain('Publish calendar link')
  })

  test('a failed publish surfaces the reason and keeps the first-run state', async () => {
    post.mockRejectedValue(new Error('Database is locked'))
    const w = await mountPage({ token: null })

    await findButton(w, 'Publish calendar link').trigger('click')
    await flushPromises()

    expect(useUI().toasts.some((t) => t.message === 'Database is locked')).toBe(true)
    // No phantom link: the card must not pretend a feed exists.
    expect(feedField(w).exists()).toBe(false)
    expect(findButton(w, 'Publish calendar link')).toBeTruthy()
  })

  test('a failed replace keeps the existing link working', async () => {
    const w = await mountPage({ token: TOKEN })
    post.mockRejectedValue(new Error('Upstream error'))

    await findButton(w, 'Replace link').trigger('click')
    await flushPromises()
    await w.findAll('button').filter((b) => b.text() === 'Replace link')
      .at(-1).trigger('click')
    await flushPromises()

    expect(useUI().toasts.some((t) => t.message === 'Upstream error')).toBe(true)
    expect(feedField(await reveal(w)).element.value).toContain(TOKEN)
  })

  test('a failed stop leaves the feed published', async () => {
    del.mockRejectedValue(new Error('Nope'))
    const w = await mountPage({ token: TOKEN })

    await findButton(w, 'Stop sharing').trigger('click')
    await flushPromises()
    await w.findAll('button').filter((b) => b.text() === 'Stop sharing')
      .at(-1).trigger('click')
    await flushPromises()

    expect(useUI().toasts.some((t) => t.message === 'Nope')).toBe(true)
    expect(feedField(await reveal(w)).element.value).toContain(TOKEN)
  })

  test('the publish button reports pending state in its label', async () => {
    let resolve
    post.mockReturnValue(new Promise((r) => { resolve = r }))
    const w = await mountPage({ token: null })

    await findButton(w, 'Publish calendar link').trigger('click')
    await flushPromises()

    // The label changes, it does not merely disable.
    expect(w.text()).toContain('Publishing…')
    resolve({ token: TOKEN })
  })

  test('a failed load shows an error with a retry, not a stuck skeleton', async () => {
    const w = await mountPage({ fail: true })

    // The server's reason is surfaced, not swallowed into a generic message,
    // and there is a way out — a card that can't show its error leaves the user
    // on a permanent skeleton.
    expect(w.text()).toContain('Service unavailable')
    expect(findButton(w, 'Retry')).toBeTruthy()
    expect(w.find('.skeleton').exists()).toBe(false)
  })
})
