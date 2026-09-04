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
  if (i.refRecipe) {
    return { quantity: i.quantity || '', unit: i.unit?.name || '', food: i.refRecipe.name,
             ...carried, refRecipeId: i.refRecipe.id, refRecipeName: i.refRecipe.name }
  }
  if (i.food) {
    return { quantity: i.quantity || '', unit: i.unit?.name || '', food: i.food.name, ...carried }
  }
  return { quantity: '', unit: '', food: i.display || '', ...carried }
}

// Turn an editor row back into the shape PUT /recipes/:id accepts. Every field
// the serializer emits and the API stores must appear here, or editing wipes it.
export function rowToPayload(r, position) {
  return {
    display: rowToDisplay(r), quantity: Number(r.quantity) || 0,
    unit: r.unit || '', food: r.food || '', note: r.note || '',
    qualifier: r.qualifier || '', section: r.section || '', position,
    refRecipeId: r.refRecipeId || undefined,
  }
}
