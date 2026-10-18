/**
 * The "What this contains" panel.
 *
 * These assert the PRESENTATION rules that make `unknown` honest, because the
 * backend can be perfectly correct and the panel still lie by rendering an
 * unscreened allergen the same way as one that matched nothing. Each test is
 * written so that softening the UI breaks it:
 *
 *   - `unknown` never appears in the quiet "No known ingredient identified"
 *     line, and never borrows its wording.
 *   - the card never uses a word a reader could take as a clearance.
 *   - an engine failure is stated, not rendered as an empty panel.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, test, vi } from 'vitest'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useRoute: () => ({ path: '/recipes/r1', params: { id: 'r1' }, query: {} }),
  onBeforeRouteLeave: () => {},
}))

const get = vi.fn()
vi.mock('../../api', () => ({
  api: {
    get: (...a) => get(...a),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    del: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    upload: vi.fn(),
    uploadPost: vi.fn(),
  },
  apiUrl: (p) => p,
  mediaUrl: (p) => p,
  streamPost: vi.fn(),
}))

import RecipeDetail from '../RecipeDetail.vue'

const RECIPE = {
  id: 'r1', slug: 'r1', name: 'Test Recipe', servings: 2, ingredients: [],
  steps: [], tags: [], categories: [], videos: [], nutrition: null,
}

const ev = (over = {}) => ({
  ingredient: '100 ml milk', matchedTerm: 'milk', ruleId: 'milk.canonical.milk',
  ruleVersion: 'allergens/2026.10.1', matchKind: 'canonical',
  contributes: 'contains', note: '',
  provenance: {
    source: 'internal', sourceId: 'milk.canonical.milk', retrieved: '2026-10-16',
    method: 'inferred', version: 'allergens/2026.10.1', confidence: 0.95,
    license: 'CC0-1.0',
  },
  ...over,
})

const allergen = (key, label, state, over = {}) => ({
  key, label, state, stateText: state, needsReview: false,
  evidence: [ev({ contributes: state })], ...over,
})

const SCREENED = {
  status: 'screened', screenedLines: 3, fullyScreened: true,
  rulesetVersions: { allergens: 'allergens/2026.10.1', dietary: 'dietary/2026.10.1' },
  allergens: [], diets: [],
  coverage: [{ ingredient: '100 ml milk', recognised: ['milk'], unscreened: [] }],
}

function serve(safety) {
  get.mockImplementation((raw) => {
    const path = String(raw || "")
    if (path.endsWith('/safety')) return Promise.resolve(safety)
    if (path.includes('/versions')) return Promise.resolve({ items: [] })
    if (path === '/categories') return Promise.resolve([])
    return Promise.resolve(RECIPE)
  })
}

async function mountWith(safety) {
  serve(safety)
  const w = mount(RecipeDetail, { global: { plugins: [createPinia()] } })
  await flushPromises()
  await flushPromises()
  return w
}

// The card's own text, isolated from the rest of the page.
function cardText(w) {
  const card = w.findAll('.card').find((c) => c.text().includes('What this contains'))
  expect(card, 'the safety card should render').toBeTruthy()
  return card.text()
}

beforeEach(() => get.mockReset())

describe('an unscreened allergen is never presented as a clear one', () => {
  test('`unknown` is called out, not folded into the quiet line', async () => {
    const w = await mountWith({
      ...SCREENED,
      fullyScreened: false,
      allergens: [
        allergen('peanuts', 'Peanuts', 'unknown'),
        allergen('milk', 'Milk', 'no_known_ingredient_identified'),
      ],
      coverage: [{
        ingredient: '1 jar secret sauce', recognised: ['sauce'],
        unscreened: ["'sauce' is a composite whose ingredients are not declared"],
      }],
    })
    const text = cardText(w)
    expect(text).toContain('Not screened')
    expect(text).toContain('Peanuts')
    // The quiet line exists for Milk only. Peanuts must not be in it.
    const quiet = w.find('.sf-quiet').text()
    expect(quiet).toContain('Milk')
    expect(quiet).not.toContain('Peanuts')
  })

  test('`unknown` gets the dashed rail, distinct from may-contain', async () => {
    const w = await mountWith({
      ...SCREENED,
      fullyScreened: false,
      allergens: [
        allergen('peanuts', 'Peanuts', 'unknown'),
        allergen('sesame', 'Sesame', 'may_contain'),
      ],
    })
    const rails = w.findAll('.sf-dt').map((d) => d.classes())
    expect(rails.some((c) => c.includes('sf-tone-unscreened'))).toBe(true)
    expect(rails.some((c) => c.includes('sf-tone-warn'))).toBe(true)
    // And it is never styled as the quiet/absent tone.
    const unscreened = w.findAll('.sf-dt').find((d) => d.text() === 'Not screened')
    expect(unscreened.classes()).not.toContain('sf-tone-quiet')
  })

  test('an unreadable ingredient is named, with the consequence spelled out', async () => {
    const w = await mountWith({
      ...SCREENED,
      fullyScreened: false,
      allergens: [allergen('peanuts', 'Peanuts', 'unknown')],
      coverage: [
        { ingredient: '1 jar secret sauce', recognised: [], unscreened: ['unrecognised: secret'] },
        { ingredient: '100 ml milk', recognised: ['milk'], unscreened: [] },
      ],
    })
    const notice = w.find('.sf-notice').text()
    expect(notice).toContain('1 jar secret sauce')
    expect(notice).toContain('unrecognised: secret')
    expect(notice).toContain('Nothing below rules out an allergen')
    // Only the unreadable line is listed, not the one that screened fine.
    expect(notice).not.toContain('100 ml milk')
  })

  test('a fully-screened recipe shows no unreadable notice', async () => {
    const w = await mountWith({
      ...SCREENED, allergens: [allergen('milk', 'Milk', 'contains')],
    })
    expect(w.find('.sf-notice').exists()).toBe(false)
  })
})

describe('the quiet state never reads as a clearance', () => {
  test('it carries the engine\'s caveat and no reassuring word', async () => {
    const w = await mountWith({
      ...SCREENED,
      allergens: [allergen('milk', 'Milk', 'no_known_ingredient_identified')],
      diets: [{
        key: 'vegan', label: 'Vegan', state: 'no_conflict_identified',
        stateText: 'no conflict identified', certified: true, advisory: null,
        needsReview: false, evidence: [ev({ contributes: 'no_conflict_identified' })],
      }],
    })
    const text = cardText(w)
    expect(text).toContain('No known ingredient identified')
    expect(text).toContain('(not a safety claim)')
    expect(text).toContain('No conflict identified')
    expect(text).toContain('(not a certification)')
    // No word a reader could bank on. `certified: true` must not become a tick.
    expect(text).not.toMatch(/\bfree from\b|\ballergen-free\b|\bsafe\b|\bcertified\b(?! from)/i)
    expect(text).not.toContain('✓')
  })

  test('the card always states what it is bounded by', async () => {
    const w = await mountWith({
      ...SCREENED, allergens: [allergen('milk', 'Milk', 'contains')],
    })
    const text = cardText(w)
    expect(text).toContain('Screened 3 ingredient lines')
    expect(text).toContain('allergens/2026.10.1')
    expect(text).toContain('it is not a safety check')
  })
})

describe('rows that agree are merged, rows that differ are not', () => {
  test('one row for nine allergens sharing a state and a reason', async () => {
    const keys = ['milk', 'egg', 'fish', 'peanuts', 'sesame']
    const w = await mountWith({
      ...SCREENED,
      fullyScreened: false,
      allergens: keys.map((k) => allergen(k, k, 'unknown',
        { evidence: [ev({ contributes: 'unknown', matchedTerm: '' })] })),
    })
    const rows = w.findAll('.sf-dt')
    expect(rows).toHaveLength(1)
    expect(rows[0].text()).toBe('Not screened')
    expect(w.find('.sf-labels').text()).toContain('milk')
    expect(w.find('.sf-labels').text()).toContain('sesame')
  })

  test('different reasons stay on their own rows', async () => {
    const w = await mountWith({
      ...SCREENED,
      allergens: [
        allergen('milk', 'Milk', 'contains', { evidence: [ev({ matchedTerm: 'butter' })] }),
        allergen('wheat', 'Wheat', 'contains', { evidence: [ev({ matchedTerm: 'flour' })] }),
      ],
    })
    expect(w.findAll('.sf-dt')).toHaveLength(2)
  })

  test('severity leads: contains sorts above unknown sorts above the quiet line',
    async () => {
      const w = await mountWith({
        ...SCREENED,
        fullyScreened: false,
        allergens: [
          allergen('peanuts', 'Peanuts', 'unknown',
            { evidence: [ev({ contributes: 'unknown', matchedTerm: '' })] }),
          allergen('milk', 'Milk', 'contains'),
          allergen('fish', 'Fish', 'no_known_ingredient_identified'),
        ],
      })
      const states = w.findAll('.sf-dt').map((d) => d.text())
      expect(states).toEqual(['Contains', 'Not screened'])
      expect(w.find('.sf-quiet').text()).toContain('Fish')
    })
})

describe('the panel says when it cannot answer', () => {
  test('a failed request is stated, not rendered as an empty panel', async () => {
    get.mockImplementation((raw) => {
    const path = String(raw || "")
      if (path.endsWith('/safety')) {
        return Promise.reject(new Error('Screening is unavailable on this install.'))
      }
      if (path.includes('/versions')) return Promise.resolve({ items: [] })
      if (path === '/categories') return Promise.resolve([])
      return Promise.resolve(RECIPE)
    })
    const w = mount(RecipeDetail, { global: { plugins: [createPinia()] } })
    await flushPromises()
    await flushPromises()
    const text = cardText(w)
    expect(text).toContain('This recipe has not been screened')
    expect(text).toContain('Screening is unavailable on this install.')
    // Not silence, and not a verdict list.
    expect(w.findAll('.sf-dt')).toHaveLength(0)
    expect(w.find('.sf-quiet').exists()).toBe(false)
  })

  test('a recipe with no ingredients says so instead of reporting nothing',
    async () => {
      const w = await mountWith({ status: 'no_ingredients', screenedLines: 0 })
      const text = cardText(w)
      expect(text).toContain('No ingredients yet')
      expect(w.findAll('.sf-dt')).toHaveLength(0)
      expect(w.find('.sf-quiet').exists()).toBe(false)
    })
})

describe('diet advisories', () => {
  test('the never-certified headline is visible without expanding', async () => {
    const w = await mountWith({
      ...SCREENED,
      diets: [{
        key: 'keto', label: 'Keto', state: 'no_conflict_identified',
        stateText: 'no conflict identified', certified: false,
        advisory: 'Informational: keto is a daily carbohydrate budget.',
        needsReview: false, evidence: [ev({ contributes: 'no_conflict_identified' })],
      }],
    })
    const summary = w.find('.sf-advisory summary').text()
    expect(summary).toContain('Keto')
    expect(summary).toContain('never certified from ingredient text')
    // The engine's own sentence survives, verbatim, inside.
    expect(w.find('.sf-advisory').text())
      .toContain('Informational: keto is a daily carbohydrate budget.')
  })

  test('no advisory block when no diet carries one', async () => {
    const w = await mountWith({
      ...SCREENED,
      diets: [{
        key: 'vegan', label: 'Vegan', state: 'no_conflict_identified',
        stateText: 'no conflict identified', certified: true, advisory: null,
        needsReview: false, evidence: [ev({ contributes: 'no_conflict_identified' })],
      }],
    })
    expect(w.find('.sf-advisory').exists()).toBe(false)
  })
})
