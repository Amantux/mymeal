import { describe, expect, test } from 'vitest'
import { mount } from '@vue/test-utils'
import ComboBox from '../ComboBox.vue'

const OPTS = ['cup', 'tbsp', 'tsp', 'gram']

describe('ComboBox', () => {
  test('filters options by the current value and offers a free-text row', async () => {
    const w = mount(ComboBox, { props: { modelValue: 't', options: OPTS } })
    await w.get('input').trigger('focus')
    const texts = w.findAll('.menu li').map((li) => li.text())
    expect(texts.filter((t) => !t.startsWith('Use'))).toEqual(['tbsp', 'tsp'])
    expect(texts.some((t) => t.includes('Use') && t.includes('t'))).toBe(true)
  })

  test('no free-text row when the value exactly matches an option', async () => {
    const w = mount(ComboBox, { props: { modelValue: 'tbsp', options: OPTS } })
    await w.get('input').trigger('focus')
    const texts = w.findAll('.menu li').map((li) => li.text())
    expect(texts.some((t) => t.startsWith('Use'))).toBe(false)
  })

  test('clicking an option emits it', async () => {
    const w = mount(ComboBox, { props: { modelValue: 't', options: OPTS } })
    await w.get('input').trigger('focus')
    await w.findAll('.menu li')[0].trigger('mousedown') // tbsp
    expect(w.emitted('update:modelValue').at(-1)).toEqual(['tbsp'])
  })

  test('typing emits the raw value (free-text is preserved)', async () => {
    const w = mount(ComboBox, { props: { modelValue: '', options: OPTS } })
    await w.get('input').setValue('pinch')
    expect(w.emitted('update:modelValue').at(-1)).toEqual(['pinch'])
  })
})
