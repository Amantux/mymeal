import { describe, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// Mock the API + UI store the component pulls in on mount.
vi.mock('../../api', () => ({
  api: {
    get: vi.fn((path) => {
      if (path === '/foods') return Promise.resolve([{ name: 'flour' }, { name: 'sugar' }])
      if (path === '/units') return Promise.resolve([{ name: 'cup' }, { name: 'tsp' }])
      if (path.startsWith('/search')) return Promise.resolve({ results: [{ id: 'r1', name: 'Garlic Confit' }] })
      return Promise.resolve([])
    }),
    post: vi.fn((path) => {
      if (path === '/recipes/parse') {
        return Promise.resolve({ ingredients: [
          { quantity: 2, unit: 'cup', food: 'flour' },
          { quantity: 1, unit: 'tsp', food: 'salt' },
        ] })
      }
      return Promise.resolve({})
    }),
  },
}))
vi.mock('../../stores/ui', () => ({ useUI: () => ({ toast: vi.fn(), error: vi.fn() }) }))

import IngredientRows from '../IngredientRows.vue'

const lastEmit = (w) => w.emitted('update:modelValue').at(-1)[0]
const mountIt = (rows) => mount(IngredientRows, { props: { modelValue: rows } })

describe('IngredientRows', () => {
  test('batch stepper for a component row steps in 0.5 increments (min 0.5)', async () => {
    const w = mountIt([{ quantity: '1', unit: 'batch', food: 'Garlic Confit',
                         note: '', refRecipeId: 'r1', refRecipeName: 'Garlic Confit' }])
    await flushPromises()
    const plus = w.get('[aria-label="More batches"]')
    const minus = w.get('[aria-label="Fewer batches"]')
    await plus.trigger('click')
    expect(w.get('.bval').text()).toBe('1.5')
    await plus.trigger('click')
    expect(w.get('.bval').text()).toBe('2')
    await minus.trigger('click')
    await minus.trigger('click')
    await minus.trigger('click')
    await minus.trigger('click') // would go below 0.5 → clamps
    expect(w.get('.bval').text()).toBe('0.5')
  })

  test('a component row shows the linked recipe (read-only), not a food input', async () => {
    const w = mountIt([{ quantity: '1', refRecipeId: 'r1', refRecipeName: 'Garlic Confit' }])
    await flushPromises()
    expect(w.find('.food-ref').exists()).toBe(true)
    expect(w.get('.food-ref').text()).toContain('Garlic Confit')
  })

  test('Paste list parses lines into rows via /recipes/parse', async () => {
    const w = mountIt([{ quantity: '', unit: '', food: '', note: '' }])
    await flushPromises()
    await w.get('.head .ghost').trigger('click') // toggle Paste list
    await w.get('.paste textarea').setValue('2 cups flour\n1 tsp salt')
    await w.get('.paste button:not(.ghost)').trigger('click') // "Add to rows"
    await flushPromises()
    const rows = lastEmit(w)
    expect(rows.map((r) => r.food)).toEqual(['flour', 'salt'])
    expect(rows[0].quantity).toBe(2)
  })

  test('add ingredient appends a blank row', async () => {
    const w = mountIt([{ food: 'flour', quantity: '2' }])
    await flushPromises()
    await w.findAll('.add')[0].trigger('click') // "＋ Add ingredient"
    expect(lastEmit(w).length).toBe(2)
  })

  // Regression for the proxy-identity infinite-loop: echoing our emitted array
  // back must not re-seed/thrash.
  test('stable when the parent echoes the same content back', async () => {
    const w = mountIt([{ food: 'flour', quantity: '2' }])
    await flushPromises()
    await w.findAll('.add')[0].trigger('click')
    const echoed = lastEmit(w)
    const before = w.emitted('update:modelValue').length
    await w.setProps({ modelValue: echoed })
    await flushPromises()
    expect(w.findAll('.ing-row').length).toBe(2)
    expect(w.emitted('update:modelValue').length).toBeLessThanOrEqual(before + 1)
  })
})

describe('qualifier round-trip', () => {
  // The editor rebuilds `display` from these fields on every save, so a field
  // missing from the blank row is DESTROYED the first time someone edits an
  // unrelated part of the recipe. Proven in a browser: dropping `qualifier`
  // from the row turned "2 tsp Vietnamese cinnamon" into "2 tsp cinnamon"
  // after changing only the serving count.
  test('a qualifier handed in survives being emitted back', async () => {
    const wrapper = mount(IngredientRows, {
      props: {
        modelValue: [{ quantity: 2, unit: 'tsp', food: 'cinnamon',
                       note: '', qualifier: 'Vietnamese' }],
      },
    })
    await flushPromises()

    const emitted = wrapper.emitted('update:modelValue')
    const rows = emitted ? emitted[emitted.length - 1][0] : wrapper.props('modelValue')
    expect(rows[0].qualifier).toBe('Vietnamese')
  })

  test('the blank row declares qualifier so it is never dropped', async () => {
    // Rows are built as { ...blank(), ...r }: a key absent from blank() is
    // absent from every row, whatever the parent passed.
    const wrapper = mount(IngredientRows, { props: { modelValue: [] } })
    await flushPromises()
    wrapper.vm.rows.forEach((r) => expect(r).toHaveProperty('qualifier'))
  })
})

describe('IngredientRows keyboard + undo', () => {
  test('Enter on a field inserts a row directly beneath, not at the end', async () => {
    const w = mount(IngredientRows, {
      props: { modelValue: [{ food: 'flour' }, { food: 'salt' }] },
    })
    await w.findAll('input[aria-label="Quantity"]')[0].trigger('keydown', { key: 'Enter' })

    const foods = w.emitted('update:modelValue').at(-1)[0].map((r) => r.food)
    expect(foods).toEqual(['flour', '', 'salt'])
  })

  test('a removed row can be put back where it was', async () => {
    const w = mount(IngredientRows, {
      props: { modelValue: [{ food: 'flour' }, { food: 'salt' }, { food: 'sugar' }] },
    })
    await w.findAll('button[aria-label="Remove"]')[1].trigger('click')
    expect(w.emitted('update:modelValue').at(-1)[0].map((r) => r.food)).toEqual(['flour', 'sugar'])

    await w.get('p.undo button').trigger('click')
    expect(w.emitted('update:modelValue').at(-1)[0].map((r) => r.food))
      .toEqual(['flour', 'salt', 'sugar'])
  })

  test('the pasted source line is shown and is dismissible', async () => {
    const w = mount(IngredientRows, {
      props: { modelValue: [{ food: 'flour', sourceText: '- 2 cups flour' }] },
    })
    expect(w.get('.src-txt').text()).toBe('- 2 cups flour')

    await w.get('.src-hide').trigger('click')
    expect(w.find('.src-txt').exists()).toBe(false)
  })
})

describe('IngredientRows undo + reorder safety', () => {
  test('a pending undo is dropped when the parent replaces the whole list', async () => {
    // "Tidy up with AI" and loading a version snapshot both replace the list. A
    // stale Undo there spliced an ingredient from the OLD list into what the
    // user then saved — a silent write of data they never entered.
    const w = mount(IngredientRows, {
      props: { modelValue: [{ food: 'flour' }, { food: 'salt' }] },
    })
    await w.findAll('button[aria-label="Remove"]')[1].trigger('click')
    expect(w.find('p.undo').exists()).toBe(true)

    await w.setProps({ modelValue: [{ food: 'butter' }, { food: 'eggs' }] })

    expect(w.find('p.undo').exists()).toBe(false)
  })

  test('Alt+Down walks a row more than one place', async () => {
    const w = mountIt([{ food: 'a' }, { food: 'b' }, { food: 'c' }])
    await flushPromises()
    const order = () => lastEmit(w).map((r) => r.food)
    const rows = () => w.findAll('.ing-row')

    await rows()[0].trigger('keydown', { key: 'ArrowDown', altKey: true })
    expect(order()).toEqual(['b', 'a', 'c'])
    // Second press must keep moving the SAME row, not undo the first.
    await rows()[1].trigger('keydown', { key: 'ArrowDown', altKey: true })
    expect(order()).toEqual(['b', 'c', 'a'])
  })

  // --- the free-text lane ----------------------------------------------------

  // One constant accessible name; aria-pressed carries the state.
  const TOGGLE = '[aria-label="Write this line as free text"]'
  // Same button; the pressed state is what distinguishes the two directions.
  const UNTOGGLE_STATE = TOGGLE

  test('the free-text toggle collapses the row to a single input', async () => {
    const w = mountIt([{ food: '' }])
    await flushPromises()
    expect(w.find('.ftxt').exists()).toBe(false)

    await w.get(TOGGLE).trigger('click')

    expect(w.findAll('.ftxt')).toHaveLength(1)
    // The qty/unit/food/note grid is gone for this row — that is the point.
    expect(w.find('[aria-label="Quantity"]').exists()).toBe(false)
    expect(w.find('[aria-label="Unit"]').exists()).toBe(false)
    expect(w.find('[aria-label="Note"]').exists()).toBe(false)
  })

  test('an emitted free-text row carries the flag and the line', async () => {
    const w = mountIt([{ food: '' }])
    await flushPromises()
    await w.get(TOGGLE).trigger('click')
    await w.get('.ftxt').setValue('a good knob of butter, for finishing')

    const row = lastEmit(w)[0]
    expect(row.freeText).toBe(true)
    expect(row.display).toBe('a good knob of butter, for finishing')
  })

  test('a row handed in as free text renders in the lane', async () => {
    // The round trip: RecipeDetail maps a stored free-text ingredient to this
    // shape, and the editor must reproduce the lane rather than showing an
    // empty structured row beside a line the author can no longer see.
    const w = mountIt([{ freeText: true, display: 'salt and pepper, to taste' }])
    await flushPromises()

    expect(w.get('.ftxt').element.value).toBe('salt and pepper, to taste')
    expect(w.get(TOGGLE).attributes('aria-pressed')).toBe('true')
  })

  test('toggling into the lane carries the typed text across', async () => {
    // The toggle sits one tab-stop from Remove. Emptying the row the user just
    // typed would be data loss with no undo.
    const w = mountIt([{ quantity: '2', unit: 'tsp', food: 'cinnamon' }])
    await flushPromises()
    await w.get(TOGGLE).trigger('click')

    expect(w.get('.ftxt').element.value).toBe('2 tsp cinnamon')
  })

  test('toggling back out keeps the line in the food field', async () => {
    const w = mountIt([{ freeText: true, display: 'a splash of olive oil' }])
    await flushPromises()
    await w.get(TOGGLE).trigger('click')

    expect(w.get('[aria-label="Ingredient"]').element.value).toBe('a splash of olive oil')
    expect(lastEmit(w)[0].freeText).toBe(false)
  })

  test('an edit made between two toggles is not discarded', async () => {
    // Out of the lane, rewrite the food, back into the lane. Keeping the old
    // `display` "just in case" meant the stale line won and the rewrite
    // vanished — silent data loss with no undo, from two clicks.
    const w = mountIt([{ freeText: true, display: 'a splash of olive oil' }])
    await flushPromises()

    await w.get(UNTOGGLE_STATE).trigger('click')
    await w.get('[aria-label="Ingredient"]').setValue('extra virgin olive oil')
    await w.get(TOGGLE).trigger('click')

    expect(w.get('.ftxt').element.value).toBe('extra virgin olive oil')
  })

  test('entering the lane folds the note into the line and clears it', async () => {
    // The row is one sentence now, and the Note box is gone — an invisible note
    // would keep rendering on the recipe page with no way to edit it.
    const w = mountIt([{ quantity: '2', unit: 'tsp', food: 'cinnamon', note: 'ground' }])
    await flushPromises()
    await w.get(TOGGLE).trigger('click')

    expect(w.get('.ftxt').element.value).toBe('2 tsp cinnamon, ground')
    expect(lastEmit(w)[0].note).toBe('')
  })

  test('structured rows are unaffected by the lane', async () => {
    const w = mountIt([{ quantity: '2', unit: 'cup', food: 'flour', note: 'sifted' }])
    await flushPromises()

    expect(w.find('.ftxt').exists()).toBe(false)
    expect(w.get('[aria-label="Quantity"]').element.value).toBe('2')
    expect(w.get('[aria-label="Note"]').element.value).toBe('sifted')
    // The toggle is off, and merely mounting a structured row emits nothing.
    expect(w.get(TOGGLE).attributes('aria-pressed')).toBe('false')
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })

  test('a component row is not offered the free-text toggle', async () => {
    // It references a recipe, so there is no prose for it to become.
    const w = mountIt([{ quantity: '1', food: 'Garlic Confit',
                         refRecipeId: 'r1', refRecipeName: 'Garlic Confit' }])
    await flushPromises()

    expect(w.find(TOGGLE).exists()).toBe(false)
  })

  test('Ctrl+Alt+Arrow does not reorder', async () => {
    // A desktop workspace shortcut (and used by some screen readers) must not
    // rearrange the user's ingredients as a side effect.
    const w = mountIt([{ food: 'a' }, { food: 'b' }])
    await flushPromises()
    await w.findAll('.ing-row')[1].trigger('keydown', { key: 'ArrowUp', altKey: true, ctrlKey: true })

    expect(w.emitted('update:modelValue')).toBeUndefined()
  })
})
