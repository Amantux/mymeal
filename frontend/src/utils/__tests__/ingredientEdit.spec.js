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

  // --- the free-text lane ---------------------------------------------------
  // A row the author declared to be prose. The flag and the line itself are two
  // more fields that must survive the rebuild; lose either and the row comes
  // back as a structured one and mints its whole sentence into the Food catalog.

  it('reproduces the free-text lane when editing a free-text ingredient', () => {
    const row = ingredientToRow({
      display: 'a good knob of butter, for finishing', freeText: true,
      food: null, unit: null,
    })
    expect(row.freeText).toBe(true)
    expect(row.display).toBe('a good knob of butter, for finishing')
    // The structured fields stay empty — this row has no food to autocomplete.
    expect(row.food).toBe('')
    expect(row.unit).toBe('')
  })

  it('round-trips a free-text ingredient verbatim, flag intact', () => {
    const line = 'a good knob of butter, for finishing'
    const p = roundTrip({ display: line, freeText: true, food: null, unit: null })

    expect(p.freeText).toBe(true)
    expect(p.display).toBe(line)
    // Empty, so the API has nothing to find-or-create even if the guard moved.
    expect(p.food).toBe('')
    expect(p.unit).toBe('')
  })

  it('does not rebuild a free-text line out of its qty/unit/food parts', () => {
    // rowToDisplay composes "2 tsp cinnamon, ground" for structured rows. Run
    // on prose that would drop every word it has no field for.
    expect(rowToDisplay({ freeText: true, display: 'salt and pepper, to taste' }))
      .toBe('salt and pepper, to taste')
  })

  it('keeps a free-text row out of the structured catalog fields', () => {
    // A row that was structured before the toggle may still carry stale
    // food/unit text. The flag is the author's decision and must win.
    const p = rowToPayload({
      freeText: true, display: 'a splash of olive oil',
      food: 'olive oil', unit: 'tbsp', quantity: '2',
    }, 0)

    expect(p.display).toBe('a splash of olive oil')
    expect(p.food).toBe('')
    expect(p.unit).toBe('')
    expect(p.quantity).toBe(0)
  })

  it('leaves a structured row unflagged', () => {
    const p = roundTrip({ quantity: 1, unit: { name: 'cup' }, food: { name: 'flour' } })
    expect(p.freeText).toBe(false)
    expect(p.food).toBe('flour')
  })

  it('does not flag a legacy unparsed line as free text', () => {
    // MUTATION GUARD, mirroring the API's. A food-less legacy row means "an
    // importer could not structure this", and the editor re-parses those on
    // purpose. Inferring the flag from emptiness would silently end that.
    const row = ingredientToRow({ display: 'a splash of olive oil' })
    expect(row.freeText).toBe(false)
  })

  it('rowToDisplay puts the qualifier in front of the food and the note after', () => {
    expect(rowToDisplay({ quantity: '2', unit: 'tsp', food: 'cinnamon',
                          qualifier: 'Vietnamese', note: 'ground' }))
      .toBe('2 tsp Vietnamese cinnamon, ground')
  })
})
