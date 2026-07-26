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
    expect(lastEmit(w)).toEqual([{ text: 'b' }])
  })

  test('move reorders steps', async () => {
    const w = mount(StepRows, { props: { modelValue: [{ text: 'a' }, { text: 'b' }] } })
    // second row's "move up" button
    await w.findAll('.step-row')[1].findAll('.icon')[0].trigger('click')
    expect(lastEmit(w)).toEqual([{ text: 'b' }, { text: 'a' }])
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
