// Pure transforms between the API's serialized ingredients and the structured
// editor rows in RecipeDetail. They live here, not in the view, because this
// is the round-trip that silently destroys data when a field is missed: the
// editor rebuilds every ingredient from these rows on save, so any serialized
// field that ingredientToRow doesn't carry (and rowToPayload doesn't send
// back) is WIPED the first time someone opens edit and saves — even if they
// never touched that ingredient. It happened to `qualifier` (caught before
// anything wrote it) and it shipped for `section`: imported recipes carried
// "For the drizzle" groupings that evaporated on the first unrelated edit.
// Keeping the pair in one tested module is what stops the third occurrence.

export function rowToDisplay(r) {
  // A free-text row IS its line — there is nothing to compose it from, and
  // composing would keep only the words that happen to have a field ("a good
  // knob of butter, for finishing" has no quantity, unit or food) and drop the
  // rest.
  if (r.freeText) return (r.display || '').trim()
  // The variety belongs in front of the food, the way a person writes it:
  // "2 tsp Vietnamese cinnamon", not "2 tsp cinnamon, Vietnamese".
  const food = [(r.qualifier || '').trim(), (r.food || '').trim()].filter(Boolean).join(' ')
  const parts = [String(r.quantity ?? '').trim(), (r.unit || '').trim(), food].filter(Boolean)
  let d = parts.join(' ')
  const note = (r.note || '').trim()
  if (note) d = d ? `${d}, ${note}` : note
  return d
}

// Turn a stored (serialized) ingredient into an editor row. Structured ones
// (with a food) round-trip exactly; legacy free-text lines drop their whole
// display into the food field so nothing is lost and the row can be
// restructured or AI-tidied.
export function ingredientToRow(i) {
  const carried = { note: i.note || '', qualifier: i.qualifier || '', section: i.section || '' }
  // A component link is still the more structured reading of a row, so it keeps
  // precedence; the two are mutually exclusive in the editor anyway (the
  // free-text toggle isn't offered on a component row).
  if (i.refRecipe) {
    return { quantity: i.quantity || '', unit: i.unit?.name || '', food: i.refRecipe.name,
             ...carried, refRecipeId: i.refRecipe.id, refRecipeName: i.refRecipe.name }
  }
  // Declared prose. Read from the serialized flag, NEVER from "has no food":
  // emptiness already means "an importer couldn't structure this line", and
  // RecipeDetail.startEdit deliberately re-parses those into tidy rows. Treating
  // the two the same would either end that re-parse or feed the author's own
  // prose back through the parser — which is the round-trip loss this lane
  // exists to stop.
  if (i.freeText) {
    return { quantity: '', unit: '', food: '', display: i.display || '',
             freeText: true, ...carried }
  }
  if (i.food) {
    return { quantity: i.quantity || '', unit: i.unit?.name || '', food: i.food.name, ...carried }
  }
  return { quantity: '', unit: '', food: i.display || '', freeText: false, ...carried }
}

// Turn an editor row back into the shape PUT /recipes/:id accepts. Every field
// the serializer emits and the API stores must appear here, or editing wipes it.
export function rowToPayload(r, position) {
  const freeText = !!r.freeText
  return {
    display: rowToDisplay(r), quantity: freeText ? 0 : Number(r.quantity) || 0,
    // Blanked for a free-text row rather than merely ignored server-side: a row
    // toggled INTO the lane still carries whatever food/unit it had before, and
    // the catalog must not be able to grow from a field the author has stopped
    // looking at. The API guards this too — belt and braces on a write that is
    // irreversible once a Food row exists.
    unit: freeText ? '' : r.unit || '', food: freeText ? '' : r.food || '',
    note: r.note || '',
    qualifier: r.qualifier || '', section: r.section || '', position,
    freeText,
    refRecipeId: r.refRecipeId || undefined,
  }
}
