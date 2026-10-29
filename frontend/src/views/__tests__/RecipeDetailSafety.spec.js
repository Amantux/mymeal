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
const put = vi.fn()
vi.mock('../../api', () => ({
  api: {
    get: (...a) => get(...a),
    post: vi.fn().mockResolvedValue({}),
    put: (...a) => put(...a),
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

beforeEach(() => {
  get.mockReset()
  // `save()` assigns the response straight onto `recipe`, so the PUT has to
  // hand back a whole recipe or the template blows up on the next render.
  put.mockReset().mockResolvedValue(RECIPE)
})

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
    // The LIMITATION leads, and the allergen name follows it. The first draft
    // read "No known ingredient identified (not a safety claim): Milk …",
    // which skims as a free-from list — the caveat has to be in the headline,
    // not in a parenthesis after the reassurance.
    const quiet = w.find('.sf-quiet').text()
    expect(quiet).toContain('not the same as free from them')
    expect(quiet.indexOf('not the same as free from them'))
      .toBeLessThan(quiet.indexOf('Milk'))
    const diets = w.findAll('.sf-quiet')[1].text()
    expect(diets).toContain('not a certification')
    expect(diets.indexOf('not a certification')).toBeLessThan(diets.indexOf('Vegan'))

    // ...and it is not shrunk below the line it qualifies. The old
    // `.sf-quiet-note` rule set it to 0.8rem inside a 0.84rem line.
    expect(w.find('.sf-quiet-note').exists()).toBe(false)

    // No word a reader could bank on. `certified: true` must not become a tick.
    expect(text).not.toMatch(/\ballergen-free\b|\bsafe\b|\bcertified\b(?! from)/i)
    expect(text).not.toContain('✓')
    // "free from" may appear ONLY as something being denied. Every occurrence
    // must have a negation close in front of it.
    for (const m of text.matchAll(/free from/gi)) {
      const before = text.slice(Math.max(0, m.index - 30), m.index)
      expect(before, `unqualified "free from" after "…${before}"`)
        .toMatch(/\bnot\b/i)
    }
  })

  test('a wholly-quiet allergen section gets the limitation as a full sentence',
    async () => {
      // The highest-risk screen: nothing else is on the card to compare
      // against, so the prefix alone is not enough.
      const w = await mountWith({
        ...SCREENED,
        allergens: [allergen('milk', 'Milk', 'no_known_ingredient_identified')],
      })
      const lead = w.find('.sf-quiet-lead')
      expect(lead.exists()).toBe(true)
      expect(lead.text()).toContain('nothing found')
      expect(lead.text()).toContain('not')
      expect(lead.text()).toContain('free from')
    })

  test('the lead sentence is dropped once there is a real finding to compare to',
    async () => {
      const w = await mountWith({
        ...SCREENED,
        allergens: [
          allergen('wheat', 'Wheat', 'contains'),
          allergen('milk', 'Milk', 'no_known_ingredient_identified'),
        ],
      })
      expect(w.find('.sf-quiet-lead').exists()).toBe(false)
      expect(w.find('.sf-quiet').exists()).toBe(true)
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

describe('the panel is never stale', () => {
  // The verdicts describe an ingredient list. The moment that list changes,
  // a panel that does not re-screen is showing last week's answer under this
  // week's ingredients — and it is silent about it. Found by review: the call
  // sat in `onMounted`, so neither saving an edit nor restoring a version
  // refreshed it.
  function serveCounting(first, second) {
    let n = 0
    get.mockImplementation((raw) => {
      const path = String(raw || '')
      if (path.endsWith('/safety')) return Promise.resolve(n++ ? second : first)
      if (path.includes('/versions')) return Promise.resolve({ items: [] })
      if (path === '/categories') return Promise.resolve([])
      return Promise.resolve(RECIPE)
    })
    return () => n
  }

  const CLEAN = {
    ...SCREENED,
    allergens: [allergen('peanuts', 'Peanuts', 'no_known_ingredient_identified')],
  }
  const DIRTY = {
    ...SCREENED,
    allergens: [allergen('peanuts', 'Peanuts', 'contains',
      { evidence: [ev({ matchedTerm: 'peanut butter' })] })],
  }

  test('saving an edit re-screens', async () => {
    serveCounting(CLEAN, DIRTY)
    const w = mount(RecipeDetail, { global: { plugins: [createPinia()] } })
    await flushPromises(); await flushPromises()
    expect(cardText(w)).toContain('No rule matched these')

    await w.vm.save()
    await flushPromises(); await flushPromises()

    const text = cardText(w)
    expect(text).toContain('Contains')
    expect(text).toContain('peanut butter')
    expect(text).not.toContain('No rule matched these')
  })

  test('re-reading the recipe re-screens, so restoring a version does too',
    async () => {
      // `promoteVersion` swaps the whole ingredient list and calls `load()`.
      serveCounting(CLEAN, DIRTY)
      const w = mount(RecipeDetail, { global: { plugins: [createPinia()] } })
      await flushPromises(); await flushPromises()
      expect(cardText(w)).toContain('No rule matched these')

      await w.vm.load()
      await flushPromises(); await flushPromises()

      expect(cardText(w)).toContain('Contains')
    })
})

describe('an unfamiliar state is never rendered as fine', () => {
  test('a state this view has not heard of gets the attention tone and sorts first',
    async () => {
      // Fail open, not quiet: a future foodcore state must not land muted at
      // the bottom of the card looking like an absence of findings.
      const w = await mountWith({
        ...SCREENED,
        allergens: [
          allergen('milk', 'Milk', 'contains'),
          allergen('egg', 'Egg', 'probably_contains_somehow'),
        ],
      })
      const rows = w.findAll('.sf-dt')
      expect(rows[0].classes()).toContain('sf-tone-unscreened')
      expect(rows[0].text()).toBe('probably_contains_somehow')
      // And it is not swept into the quiet line.
      expect(w.find('.sf-quiet').exists()).toBe(false)
    })
})

describe('a hedged verdict shows WHY it is hedged', () => {
  test("the engine's note is surfaced on may_contain", async () => {
    // Without it, a `contains` downgraded because the line claims to be vegan
    // renders as "May contain Milk — butter": indistinguishable from an
    // ordinary precautionary match, and the actionable half is gone.
    const w = await mountWith({
      ...SCREENED,
      allergens: [allergen('milk', 'Milk', 'may_contain', {
        evidence: [ev({
          contributes: 'may_contain', matchedTerm: 'butter',
          matchKind: 'declared_free_from',
          note: 'declared vegan; claim not verified',
        })],
      })],
    })
    const text = cardText(w)
    expect(text).toContain('butter')
    expect(text).toContain('declared vegan; claim not verified')
  })

  test('a plain contains stays terse — no note is invented', async () => {
    const w = await mountWith({
      ...SCREENED,
      allergens: [allergen('milk', 'Milk', 'contains', {
        evidence: [ev({ matchedTerm: 'butter', note: 'some internal note' })],
      })],
    })
    expect(cardText(w)).not.toContain('some internal note')
  })
})

describe('rows merge on the full reason, not the displayed one', () => {
  test('verdicts differing past the 4-term cutoff stay separate', async () => {
    const terms = (...t) => ({
      evidence: t.map((x) => ev({ matchedTerm: x })),
    })
    const w = await mountWith({
      ...SCREENED,
      allergens: [
        allergen('milk', 'Milk', 'contains', terms('a', 'b', 'c', 'd', 'e')),
        allergen('egg', 'Egg', 'contains', terms('a', 'b', 'c', 'd', 'f')),
      ],
    })
    // Same first four terms, different fifth: two findings, two rows.
    expect(w.findAll('.sf-dt')).toHaveLength(2)
    expect(cardText(w)).toContain('+1 more')
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

  // --- collapsed by default ------------------------------------------------
  //
  // Asked for. The risk is obvious and these tests exist to bound it: a panel
  // that hides "Contains milk" from someone who cannot eat dairy would be a
  // regression dressed as a preference.

  const chip = (w) => w.findAll('.sf-chip').at(0)

  test('the panel starts collapsed', async () => {
    const w = await mountWith({ ...SCREENED,
      allergens: [allergen('milk', 'Milk', 'contains')] })

    // v-show, so the body exists but is hidden — asserting the style rather
    // than absence, because v-if and v-show would both "pass" an exists check
    // and only one of them keeps the body in the a11y tree for aria-controls.
    expect(w.get('#contains-body').attributes('style')).toContain('display: none')
    expect(w.get('.sf-toggle').attributes('aria-expanded')).toBe('false')
  })

  test('a CONTAINS verdict is visible while collapsed', async () => {
    const w = await mountWith({ ...SCREENED,
      allergens: [allergen('milk', 'Milk', 'contains')] })

    // The whole safety argument for collapsing. The label is NAMED, not
    // counted: "1 flagged" would force a reader to open the panel to find out
    // whether it is the thing they react to.
    expect(chip(w).text()).toContain('Contains')
    expect(chip(w).text()).toContain('Milk')
    expect(chip(w).classes()).toContain('sf-tone-bad')
  })

  test('a MAY CONTAIN verdict is visible while collapsed, in its own tone', async () => {
    const w = await mountWith({ ...SCREENED,
      allergens: [allergen('nuts', 'Tree nuts', 'may_contain')] })

    expect(chip(w).text()).toContain('May contain')
    expect(chip(w).classes()).toContain('sf-tone-warn')
  })

  test('the worst tone leads when several states are present', async () => {
    const w = await mountWith({ ...SCREENED, allergens: [
      allergen('nuts', 'Tree nuts', 'may_contain'),
      allergen('milk', 'Milk', 'contains'),
    ] })

    // "Contains Milk", not "May contain Tree nuts" — the summary must lead
    // with the strongest claim, not with whichever row sorted first.
    expect(chip(w).text()).toContain('Contains')
    expect(chip(w).text()).toContain('Milk')
    expect(chip(w).classes()).toContain('sf-tone-bad')
  })

  test('the summary never claims a clearance when nothing is flagged', async () => {
    const w = await mountWith(SCREENED)

    const text = chip(w).text().toLowerCase()
    // States the ACT, not a verdict. The panel exists because an empty result
    // is not a clearance, and a collapsed summary is the easiest place to
    // undo that by accident.
    expect(text).toContain('screened')
    for (const banned of ['free from', 'free of', 'safe', 'no allergens',
                          'allergen free', 'none']) {
      expect(text).not.toContain(banned)
    }
  })

  test('unreadable lines are surfaced in the collapsed summary', async () => {
    const w = await mountWith({
      ...SCREENED, fullyScreened: false, screenedLines: 2,
      coverage: [{ ingredient: 'a splash of something', recognised: [],
                   unscreened: ['no rule matched'] }],
    })

    // With nothing flagged, "screened, nothing flagged" would be a half-truth
    // while a line went unread — so the unread count takes the lead instead.
    expect(chip(w).text()).toContain('unreadable')
    expect(chip(w).classes()).toContain('sf-tone-unscreened')
  })

  test('an unscreened-only verdict summarises without crashing', async () => {
    // REGRESSION. The first version hardcoded `bad ? 'bad' : 'warn'`, so a
    // recipe whose only loud rows were `unknown` matched neither tone, the
    // lead group came back empty and `lead[0].state` threw inside a computed —
    // surfacing as an unhandled rejection rather than a failed assertion.
    const w = await mountWith({ ...SCREENED,
      allergens: [allergen('sesame', 'Sesame', 'unknown')] })

    expect(chip(w).exists()).toBe(true)
    expect(chip(w).text().length).toBeGreaterThan(0)
  })

  test('expanding reveals the detail and hides the summary chip', async () => {
    const w = await mountWith({ ...SCREENED,
      allergens: [allergen('milk', 'Milk', 'contains')] })

    await w.get('.sf-toggle').trigger('click')

    expect(w.get('#contains-body').attributes('style') || '')
      .not.toContain('display: none')
    expect(w.get('.sf-toggle').attributes('aria-expanded')).toBe('true')
    // The chip goes: the verdict it summarised is now on screen in full, and
    // two copies of one fact invite the reader to wonder which is current.
    expect(w.find('.sf-chip').exists()).toBe(false)
  })

  test('the screening is still fetched even though the panel is shut', async () => {
    await mountWith(SCREENED)

    // Not fetching would be the cheaper reading of "hidden", and it would make
    // the summary impossible — so the request stays and the body is what hides.
    expect(get.mock.calls.some(([p]) => String(p).endsWith('/safety'))).toBe(true)
  })
})
