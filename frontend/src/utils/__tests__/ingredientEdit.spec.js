import { describe, it, expect } from 'vitest'
import { rowToDisplay, ingredientToRow, rowToPayload } from '../ingredientEdit'

// Regression guard for the edit round-trip. The editor rebuilds every
// ingredient from rows on save, so any serialized field ingredientToRow drops
// (or rowToPayload doesn't send back) is silently WIPED by an unrelated edit.
// `section` shipped exactly that bug: imported "For the drizzle" groupings
// evaporated the first time edit was opened and saved.

const roundTrip = (ing) => rowToPayload(ingredientToRow(ing), 0)

describe('ingredient edit round-trip', () => {
  it('preserves section and qualifier on a structured ingredient', () => {
    const p = roundTrip({
      quantity: 2, unit: { name: 'tbsp' }, food: { name: 'honey' },
      note: 'runny', qualifier: 'wildflower', section: 'For the drizzle',
    })
    expect(p.section).toBe('For the drizzle')
    expect(p.qualifier).toBe('wildflower')
    expect(p.note).toBe('runny')
    expect(p.quantity).toBe(2)
    expect(p.unit).toBe('tbsp')
    expect(p.food).toBe('honey')
  })

  it('preserves section on a linked-recipe component row', () => {
    const p = roundTrip({
      quantity: 1, unit: { name: 'batch' }, section: 'Base',
      refRecipe: { id: 'r1', name: 'Pizza dough' },
    })
    expect(p.section).toBe('Base')
    expect(p.refRecipeId).toBe('r1')
  })

  it('preserves section on a legacy free-text ingredient', () => {
    const p = roundTrip({ display: 'a splash of olive oil', section: 'For the drizzle' })
    expect(p.section).toBe('For the drizzle')
    expect(p.display).toBe('a splash of olive oil')
  })

  it('sends an empty section, not undefined, when there is none', () => {
    // The API clamps str(section or ""), so '' is the honest "no section".
    const p = roundTrip({ quantity: 1, food: { name: 'egg' } })
    expect(p.section).toBe('')
  })

  it('rowToDisplay puts the qualifier in front of the food and the note after', () => {
    expect(rowToDisplay({ quantity: '2', unit: 'tsp', food: 'cinnamon',
                          qualifier: 'Vietnamese', note: 'ground' }))
      .toBe('2 tsp Vietnamese cinnamon, ground')
  })
})
