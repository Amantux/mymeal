import { describe, expect, test, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

// The component decides between "switch the mode here" and "go to the other
// page" purely from the current route, so both have to be controllable.
const push = vi.fn()
let path = '/recipes/new'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push }),
  useRoute: () => ({ get path() { return path } }),
}))

import InputMethods from '../InputMethods.vue'

const at = (p) => { path = p }
const labels = (w) => w.findAll('.m-label').map((b) => b.text())
const byLabel = (w, text) =>
  w.findAll('[role="tab"]').find((b) => b.text().includes(text))

beforeEach(() => {
  push.mockClear()
  at('/recipes/new')
})

describe('InputMethods', () => {
  test('offers every way in, on both pages', () => {
    // The whole point: neither page may show a subset. "New recipe" used to hide
    // the four import methods, and Import had no way back to typing it out.
    const expected = ['Type it out', 'Draft with AI', 'Paste', 'From a link',
                      'From a photo', 'By name']
    at('/recipes/new')
    expect(labels(mount(InputMethods))).toEqual(expected)
    at('/import')
    expect(labels(mount(InputMethods))).toEqual(expected)
  })

  test('a method belonging to this page just changes the mode', async () => {
    at('/recipes/new')
    const w = mount(InputMethods, { props: { modelValue: 'type' } })

    await byLabel(w, 'Draft with AI').trigger('click')

    expect(w.emitted('update:modelValue').at(-1)).toEqual(['draft'])
    expect(push).not.toHaveBeenCalled()
  })

  test('a method on the other page navigates, carrying the choice in the URL', async () => {
    at('/recipes/new')
    const w = mount(InputMethods, { props: { modelValue: 'type' } })

    await byLabel(w, 'Paste').trigger('click')

    expect(push).toHaveBeenCalledWith({ path: '/import', query: { mode: 'text' } })
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })

  test('the same control routes the other way from Import', async () => {
    at('/import')
    const w = mount(InputMethods, { props: { modelValue: 'text' } })

    await byLabel(w, 'Type it out').trigger('click')
    expect(push).toHaveBeenCalledWith({ path: '/recipes/new', query: {} })

    await byLabel(w, 'Draft with AI').trigger('click')
    expect(push).toHaveBeenCalledWith({ path: '/recipes/new', query: { mode: 'draft' } })
  })

  test('re-picking the active method does nothing at all', async () => {
    at('/import')
    const w = mount(InputMethods, { props: { modelValue: 'text' } })

    await byLabel(w, 'Paste').trigger('click')

    expect(push).not.toHaveBeenCalled()
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })

  test('the active method is announced, not just coloured', () => {
    at('/import')
    const w = mount(InputMethods, { props: { modelValue: 'url' } })

    const selected = w.findAll('[role="tab"]').filter((b) => b.attributes('aria-selected') === 'true')
    expect(selected).toHaveLength(1)
    expect(selected[0].text()).toContain('From a link')
    expect(w.get('[role="tablist"]').attributes('aria-label')).toBeTruthy()
  })

  test('the emoji is decorative and hidden from assistive tech', () => {
    // Every option's meaning is in its label; the emoji would otherwise be read
    // out as a name, which is how "✍️" becomes "writing hand" instead of "Type it out".
    const w = mount(InputMethods, { props: { modelValue: 'type' } })

    for (const e of w.findAll('.m-emoji')) {
      expect(e.attributes('aria-hidden')).toBe('true')
    }
  })
})
