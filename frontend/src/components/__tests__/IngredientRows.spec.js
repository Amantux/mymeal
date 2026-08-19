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

    await w.get('.src-x').trigger('click')
    expect(w.find('.src-txt').exists()).toBe(false)
  })
})
