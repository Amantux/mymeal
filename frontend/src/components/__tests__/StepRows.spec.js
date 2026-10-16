import { describe, expect, test } from 'vitest'
import { mount } from '@vue/test-utils'
import StepRows from '../StepRows.vue'

const lastEmit = (w) => w.emitted('update:modelValue').at(-1)[0]

describe('StepRows', () => {
  test('renders one row per step', () => {
    const w = mount(StepRows, { props: { modelValue: [{ text: 'a' }, { text: 'b' }] } })
    expect(w.findAll('.step-row').length).toBe(2)
  })

  test('add appends a blank step', async () => {
    const w = mount(StepRows, { props: { modelValue: [{ text: 'a' }] } })
    await w.get('.add').trigger('click')
    expect(lastEmit(w).length).toBe(2)
  })

  test('remove drops a step (but never below one row)', async () => {
    const w = mount(StepRows, { props: { modelValue: [{ text: 'a' }, { text: 'b' }] } })
    await w.findAll('.icon.rm')[0].trigger('click')
    expect(lastEmit(w)).toEqual([{ title: '', text: 'b' }])
  })

  test('move reorders steps', async () => {
    const w = mount(StepRows, { props: { modelValue: [{ text: 'a' }, { text: 'b' }] } })
    // second row's "move up" button
    await w.findAll('.step-row')[1].findAll('.icon')[0].trigger('click')
    expect(lastEmit(w)).toEqual([{ title: '', text: 'b' }, { title: '', text: 'a' }])
  })

  // ---- Author phase headings (RecipeStep.title) ----

  test('heading inputs are hidden until asked for', async () => {
    const w = mount(StepRows, { props: { modelValue: [{ text: 'a' }, { text: 'b' }] } })
    expect(w.findAll('.title-in').length).toBe(0)

    await w.get('.titles-on').trigger('click')

    expect(w.findAll('.title-in').length).toBe(2)
  })

  // An author who already has headings (typed here, or carried in from the
  // schema.org / AI import paths) must not find the fields hidden and conclude
  // they were lost.
  test('heading inputs are forced open when a step already has one', () => {
    const w = mount(StepRows, { props: { modelValue: [{ title: 'Dough', text: 'a' }, { text: 'b' }] } })
    expect(w.findAll('.title-in').length).toBe(2)
    expect(w.find('.titles-on').exists()).toBe(false)
  })

  test('an edited heading is emitted on its own step', async () => {
    const w = mount(StepRows, { props: { modelValue: [{ text: 'a' }, { text: 'b' }] } })
    await w.get('.titles-on').trigger('click')

    await w.findAll('.title-in')[1].setValue('Bake day')

    expect(lastEmit(w)).toEqual([{ title: '', text: 'a' }, { title: 'Bake day', text: 'b' }])
  })

  test('a heading travels with its step when reordered', async () => {
    const w = mount(StepRows, {
      props: { modelValue: [{ title: 'Dough', text: 'a' }, { title: 'Bake day', text: 'b' }] },
    })
    await w.findAll('.step-row')[1].findAll('.icon')[0].trigger('click')

    expect(lastEmit(w)).toEqual([{ title: 'Bake day', text: 'b' }, { title: 'Dough', text: 'a' }])
  })

  // Regression: the parent echoing our own emitted array back (v-model) must not
  // re-seed/thrash — Vue proxies the prop, so an identity guard would loop.
  test('stable when the parent echoes the same content back', async () => {
    const w = mount(StepRows, { props: { modelValue: [{ text: 'a' }] } })
    await w.get('.add').trigger('click')
    const echoed = lastEmit(w)
    const emitsBefore = w.emitted('update:modelValue').length
    await w.setProps({ modelValue: echoed })
    await w.vm.$nextTick()
    expect(w.findAll('.step-row').length).toBe(2)
    // No extra emit storm from the echo.
    expect(w.emitted('update:modelValue').length).toBeLessThanOrEqual(emitsBefore + 1)
  })
})
