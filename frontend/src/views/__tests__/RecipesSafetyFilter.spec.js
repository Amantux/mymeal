/**
 * Filtering the recipe list by what a recipe contains.
 *
 * The backend reports four states and refuses to return a boolean, so the
 * POLICY lives here — and a policy about allergens is the kind that is easy to
 * make quietly wrong. These tests pin the two halves that matter:
 *
 *   contains / mayContain  -> removed
 *   unknown                -> KEPT, and marked
 *
 * Dropping `unknown` silently is a false clearance in reverse: the cook never
 * learns the one recipe that might have had it was hidden. Keeping it unmarked
 * presents an unverified recipe as cleared. Both are regressions this file
 * should catch.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, test, vi } from 'vitest'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

const get = vi.fn()
vi.mock('../../api', () => ({
  api: { get: (...a) => get(...a) },
  mediaUrl: (p) => p,
}))

import Recipes from '../Recipes.vue'

const LIST = {
  items: [
    { id: 'milky', name: 'Milky Thing', tags: [], categories: [] },
    { id: 'maybe', name: 'Maybe Nuts', tags: [], categories: [] },
    { id: 'dunno', name: 'Unreadable One', tags: [], categories: [] },
    { id: 'clean', name: 'Plain Rice', tags: [], categories: [] },
  ],
}

const TAGS = {
  truncated: false,
  failed: [],
  allergens: ['Milk', 'Tree nuts'],
  diets: ['Vegan'],
  recipes: {
    milky: { status: 'screened', contains: ['Milk'], mayContain: [], unknown: [],
             incompatibleDiets: ['Vegan'], compatibleDiets: [], uncertainDiets: [],
             unreadable: 0, fullyScreened: true },
    maybe: { status: 'screened', contains: [], mayContain: ['Tree nuts'], unknown: [],
             incompatibleDiets: [], compatibleDiets: ['Vegan'], uncertainDiets: [],
             unreadable: 0, fullyScreened: true },
    dunno: { status: 'screened', contains: [], mayContain: [], unknown: ['Milk'],
             incompatibleDiets: [], compatibleDiets: [], uncertainDiets: ['Vegan'],
             unreadable: 2, fullyScreened: false },
    clean: { status: 'screened', contains: [], mayContain: [], unknown: [],
             incompatibleDiets: [], compatibleDiets: ['Vegan'], uncertainDiets: [],
             unreadable: 0, fullyScreened: true },
  },
}

beforeEach(() => {
  get.mockReset()
  get.mockImplementation((path) => {
    const p = String(path || '')
    if (p.startsWith('/recipes/safety-tags')) return Promise.resolve(TAGS)
    if (p.startsWith('/recipes')) return Promise.resolve(LIST)
    if (p === '/categories') return Promise.resolve([])
    return Promise.resolve([])
  })
})

async function mounted() {
  // useLoader reaches a Pinia store, same as the detail view's spec.
  const w = mount(Recipes, { global: { plugins: [createPinia()] } })
  await flushPromises()
  return w
}

const titles = (w) => w.findAll('.title').map((t) => t.text())

describe('Recipes — allergens & diets filter', () => {
  test('the screening is not fetched until the filter is opened', async () => {
    await mounted()

    // ~15 ms a recipe server-side. Every visitor would pay it for a control
    // most never open.
    expect(get.mock.calls.some(([p]) => String(p).includes('safety-tags'))).toBe(false)
  })

  test('opening the filter fetches it once', async () => {
    const w = await mounted()

    await w.get('.sf-filter').trigger('toggle')
    await flushPromises()
    await w.get('.sf-filter').trigger('toggle')
    await flushPromises()

    const calls = get.mock.calls.filter(([p]) => String(p).includes('safety-tags'))
    expect(calls).toHaveLength(1)
  })

  test('the options offered come from the screening, not a hardcoded list', async () => {
    const w = await mounted()
    await w.get('.sf-filter').trigger('toggle')
    await flushPromises()

    const labels = w.findAll('.sf-opt').map((o) => o.text())
    expect(labels).toEqual(['Milk', 'Tree nuts'])
  })

  async function withAvoid(allergen) {
    const w = await mounted()
    await w.get('.sf-filter').trigger('toggle')
    await flushPromises()
    const box = w.findAll('.sf-opt input')
      .find((_, i) => w.findAll('.sf-opt')[i].text() === allergen)
    await box.setValue(true)
    await flushPromises()
    return w
  }

  test('a CONTAINS match is removed', async () => {
    const w = await withAvoid('Milk')

    expect(titles(w)).not.toContain('Milky Thing')
  })

  test('a MAY CONTAIN match is removed too', async () => {
    // "May contain" is the state a cook with an allergy acts on exactly like
    // "contains". Treating it as a soft signal would be the dangerous reading.
    const w = await withAvoid('Tree nuts')

    expect(titles(w)).not.toContain('Maybe Nuts')
  })

  test('an UNKNOWN is kept and marked, never silently dropped', async () => {
    const w = await withAvoid('Milk')

    // Kept: hiding it would mean the cook never learns the one recipe that
    // might have contained milk was removed on a guess.
    expect(titles(w)).toContain('Unreadable One')
    // Marked: and the marker NAMES the allergen, so the reader does not have
    // to open the recipe to find out what was unverified.
    const warn = w.findAll('.sf-unsure').map((n) => n.text()).join(' ')
    expect(warn).toContain('could not tell')
    expect(warn).toContain('Milk')
  })

  test('a recipe with nothing flagged is kept and not marked', async () => {
    const w = await withAvoid('Milk')

    expect(titles(w)).toContain('Plain Rice')
    expect(w.findAll('.sf-unsure').map((n) => n.text()).join(' '))
      .not.toContain('Plain Rice')
  })

  test('requiring a diet removes a declared conflict only', async () => {
    const w = await mounted()
    await w.get('.sf-filter').trigger('toggle')
    await flushPromises()

    await w.get('.sf-diet select').setValue('Vegan')
    await flushPromises()

    expect(titles(w)).not.toContain('Milky Thing')   // incompatible
    expect(titles(w)).toContain('Plain Rice')        // compatible
    // Uncertain is surfaced, not filtered — same reasoning as `unknown`.
    expect(titles(w)).toContain('Unreadable One')
  })

  test('the filter states its limits where the filtering happens', async () => {
    const w = await mounted()
    await w.get('.sf-filter').trigger('toggle')
    await flushPromises()

    const caveat = w.findAll('.sf-caveat').map((p) => p.text()).join(' ').toLowerCase()
    // A cook using this as an allergy filter needs the limits BEFORE they
    // trust the result; "always read the packaging" on the recipe page
    // afterwards is too late.
    expect(caveat).toContain('not a safety check')
    expect(caveat).toContain('ingredient text')
    // And it must not claim what it cannot.
    for (const banned of ['free from', 'guarantee', 'certified', 'safe for']) {
      expect(caveat).not.toContain(banned)
    }
  })

  test('filtering everything out explains why, and offers a way back', async () => {
    // Its own fixture: in the shared one, avoiding both allergens still leaves
    // the `unknown` recipe and the clean one — which is the policy working, not
    // an empty list. To reach the empty state every recipe must actually be
    // excluded.
    get.mockImplementation((path) => {
      const p = String(path || '')
      if (p.startsWith('/recipes/safety-tags')) {
        return Promise.resolve({
          truncated: false, failed: [], allergens: ['Milk'], diets: [],
          recipes: Object.fromEntries(LIST.items.map((r) => [r.id, {
            status: 'screened', contains: ['Milk'], mayContain: [], unknown: [],
            incompatibleDiets: [], compatibleDiets: [], uncertainDiets: [],
            unreadable: 0, fullyScreened: true,
          }])),
        })
      }
      if (p.startsWith('/recipes')) return Promise.resolve(LIST)
      if (p === '/categories') return Promise.resolve([])
      return Promise.resolve([])
    })

    const w = await mounted()
    await w.get('.sf-filter').trigger('toggle')
    await flushPromises()
    await w.get('.sf-opt input').setValue(true)
    await flushPromises()

    const text = w.text()
    expect(text).toContain('No matches')
    // Says WHY it is empty — "No matches" alone reads as "you have no
    // recipes", which would be alarming and wrong.
    expect(text.toLowerCase()).toContain('asked to avoid')
    // And a way back out, on the empty state itself.
    expect(text.toLowerCase()).toContain('clear allergen filters')
  })
})
