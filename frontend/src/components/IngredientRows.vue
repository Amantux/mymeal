<script setup>
// Mealie-style structured ingredient editor: one row per ingredient with
// separate Quantity / Unit / Food / Note fields, add·remove·reorder, and
// autocomplete (ComboBox) against the group's real units + foods. A "Paste list"
// box parses pasted lines into rows via /recipes/parse. A row can also LINK
// another recipe as a component (refRecipeId) — inserted via the recipe picker,
// rendered as a read-only link. v-model is an array of
// { quantity, unit, food, note, refRecipeId, refRecipeName } rows.
import { ref, watch, onMounted, nextTick } from 'vue'
import { api } from '../api'
import { useUI } from '../stores/ui'
import ComboBox from './ComboBox.vue'

const props = defineProps({ modelValue: { type: Array, default: () => [] } })
const emit = defineEmits(['update:modelValue'])
const ui = useUI()

const foods = ref([])
const units = ref([])

function blank() {
  // `qualifier` (the variety: "Vietnamese" in "Vietnamese cinnamon") must be in
  // the blank row: rows are built as { ...blank(), ...r }, so a key missing here
  // is dropped from every row the parent hands in.
  // `sourceText` is the line the paste parser was given. It's UI-only (the
  // backend ignores unknown row keys) and exists so a wrong parse is visible
  // against its ground truth instead of silently landing in the wrong column.
  // `section` ("For the drizzle") likewise: it isn't editable here, but a key
  // missing from blank() is what let the save path rebuild rows without it and
  // wipe every grouping the first time an imported recipe was edited.
  return { quantity: '', unit: '', food: '', note: '', qualifier: '', section: '',
           sourceText: '', refRecipeId: '', refRecipeName: '' }
}
const rows = ref(props.modelValue.length ? props.modelValue.map((r) => ({ ...blank(), ...r })) : [blank()])

// Emit on edit; re-seed only when the parent pushes different CONTENT (Vue
// proxies the prop, so an identity check would loop forever).
function emitUp() { emit('update:modelValue', rows.value.map((r) => ({ ...r }))) }
watch(rows, emitUp, { deep: true })
watch(() => props.modelValue, (v) => {
  const incoming = JSON.stringify(v || [])
  if (incoming !== JSON.stringify(rows.value)) {
    rows.value = v && v.length ? v.map((r) => ({ ...blank(), ...r })) : [blank()]
    // The pending undo belongs to a list that no longer exists. "Tidy up with AI"
    // and loading a version snapshot both replace the whole list, and a stale
    // Undo there spliced a foreign ingredient into what the user went on to save.
    lastRemoved.value = null
  }
})

onMounted(async () => {
  try { foods.value = (await api.get('/foods')).map((f) => f.name) } catch { /* optional */ }
  try { units.value = (await api.get('/units')).map((u) => u.name) } catch { /* optional */ }
})

// Component "batch" count steps in 0.5 increments (min 0.5).
function stepBatch(r, delta) {
  const v = (Number(r.quantity) || 1) + delta
  r.quantity = String(Math.max(0.5, Math.round(v * 2) / 2))
}
function fmtBatch(q) {
  const n = Number(q) || 1
  return Number.isInteger(n) ? String(n) : n.toFixed(1)
}

function add() { rows.value.push(blank()) }

// --- Keyboard flow -----------------------------------------------------------
// Adding a row used to cost a mouse trip to the button below the list, which at
// 30 rows is a long way from where you're typing. Enter on any field in a row
// opens a fresh one directly beneath and puts the caret in its Quantity box, so
// a whole list can be typed without leaving the keyboard.
const qtyRefs = ref([])
function setQtyRef(el, i) {
  if (el) qtyRefs.value[i] = el
  else delete qtyRefs.value[i]
}
async function addAfter(i) {
  rows.value.splice(i + 1, 0, blank())
  await nextTick()
  qtyRefs.value[i + 1]?.focus()
}

// Removal keeps the row so it can be put back: this is a destructive click with
// no confirm, one tab-stop away from the fields you're editing.
const lastRemoved = ref(null)
function remove(i) {
  const [row] = rows.value.splice(i, 1)
  lastRemoved.value = { row, index: i }
  if (!rows.value.length) rows.value.push(blank())
}
function undoRemove() {
  if (!lastRemoved.value) return
  const { row, index } = lastRemoved.value
  // A blank row auto-added by remove() would otherwise be left stranded above
  // the restored one.
  const onlyBlank = rows.value.length === 1 && !rows.value[0].food
    && !rows.value[0].quantity && !rows.value[0].refRecipeId
  if (onlyBlank) rows.value = []
  rows.value.splice(Math.min(index, rows.value.length), 0, row)
  lastRemoved.value = null
}
function removedLabel(row) {
  return row.refRecipeName || row.food || row.sourceText || 'ingredient'
}
function move(i, delta) {
  const j = i + delta
  if (j < 0 || j >= rows.value.length) return
  const [r] = rows.value.splice(i, 1)
  rows.value.splice(j, 0, r)
}

// Rows are keyed by index, so the focused DOM node stays put while its CONTENTS
// move away — a second Alt+Up therefore moved the neighbour back and undid the
// first, capping the shortcut at a one-place nudge. Follow the row: re-focus the
// same field in its new position.
const rowRefs = ref([])
function setRowRef(el, i) {
  if (el) rowRefs.value[i] = el
  else delete rowRefs.value[i]
}
async function moveFocused(i, delta) {
  const j = i + delta
  if (j < 0 || j >= rows.value.length) return
  const label = document.activeElement?.getAttribute?.('aria-label')
  move(i, delta)
  await nextTick()
  const rowEl = rowRefs.value[j]
  const same = label && rowEl ? rowEl.querySelector(`[aria-label="${label}"]`) : null
  ;(same || qtyRefs.value[j])?.focus()
}
function onRowKey(e, i) {
  // Vue's `.alt` modifier does NOT require the other modifiers to be absent, so
  // Ctrl+Alt+Up — a desktop workspace shortcut, and used by some screen readers
  // — was silently reordering the user's ingredients as a side effect.
  if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return
  if (e.key === 'ArrowUp') { e.preventDefault(); moveFocused(i, -1) }
  else if (e.key === 'ArrowDown') { e.preventDefault(); moveFocused(i, 1) }
}

// --- Paste-a-list ---
const showPaste = ref(false)
const pasteText = ref('')
const parsing = ref(false)
async function parsePaste() {
  const lines = pasteText.value.split('\n').map((l) => l.trim()).filter(Boolean)
  if (!lines.length || parsing.value) return
  parsing.value = true
  try {
    const res = await api.post('/recipes/parse', { lines })
    const parsed = res.ingredients.map((r) => ({
      ...blank(), quantity: r.quantity || '', unit: r.unit || '', food: r.food || '',
      // Carry the note (the parser puts a dropped range high end there) and the
      // original line, both of which used to be discarded on arrival.
      note: r.note || '', sourceText: r.display || '',
    }))
    const onlyBlank = rows.value.length === 1 && !rows.value[0].food && !rows.value[0].quantity
    rows.value = onlyBlank ? parsed : rows.value.concat(parsed)
    pasteText.value = ''
    showPaste.value = false
    ui.toast(`Added ${parsed.length} ingredient${parsed.length === 1 ? '' : 's'}`)
  } catch (e) {
    ui.error(e.message || 'Could not parse the list')
  } finally {
    parsing.value = false
  }
}

// --- Recipe component picker ---
const showPicker = ref(false)
const pickerQuery = ref('')
const pickerResults = ref([])
let searchTimer
function onPickerInput() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(searchRecipes, 200)
}
async function searchRecipes() {
  try {
    const q = encodeURIComponent(pickerQuery.value.trim())
    const res = await api.get(`/search?types=recipe&limit=12&q=${q}`)
    pickerResults.value = res.results || []
  } catch { pickerResults.value = [] }
}
function openPicker() {
  showPicker.value = true
  pickerQuery.value = ''
  pickerResults.value = []
  searchRecipes()
}
function addComponent(r) {
  rows.value.push({
    ...blank(), quantity: '1', unit: 'batch', food: r.name,
    refRecipeId: r.id, refRecipeName: r.name,
  })
  showPicker.value = false
}
</script>

<template>
  <div class="ing-rows">
    <div class="head">
      <span class="lbl">Ingredients</span>
      <div class="grow"></div>
      <button type="button" class="ghost sm" @click="showPaste = !showPaste">
        {{ showPaste ? 'Close' : '📋 Paste list' }}
      </button>
    </div>

    <div v-if="showPaste" class="paste card">
      <textarea v-model="pasteText" rows="5"
        placeholder="Paste ingredients, one per line:&#10;2 cups flour&#10;1 tsp salt&#10;3 eggs"></textarea>
      <div class="row" style="justify-content:flex-end;gap:8px;margin-top:8px">
        <button type="button" class="ghost sm" @click="showPaste = false">Cancel</button>
        <button type="button" class="sm" :disabled="parsing || !pasteText.trim()" @click="parsePaste">
          {{ parsing ? 'Parsing…' : 'Add to rows' }}
        </button>
      </div>
    </div>

    <div class="col-heads">
      <span>Qty</span><span>Unit</span><span>Ingredient</span><span>Note</span><span></span>
    </div>
    <template v-for="(r, i) in rows" :key="i">
      <!-- The row's section ("For the drizzle"), shown where it changes so the
           editor mirrors the read view's grouping. Display-only; editing
           sections is deliberately not built — this label exists so a preserved
           grouping is VISIBLE, not invisible-but-intact. A SIBLING of the row,
           not a child: inside the row grid it collided with the phone layout's
           explicit `.ctl { grid-row: 1 }` placement and orphaned the controls. -->
      <p v-if="r.section && r.section !== rows[i - 1]?.section" class="sec-lbl">{{ r.section }}</p>
      <div :ref="(el) => setRowRef(el, i)"
         class="ing-row" :class="{ 'is-ref': r.refRecipeId }"
         role="group" :aria-label="`Ingredient ${i + 1} of ${rows.length}`"
         @keydown="onRowKey($event, i)">
      <template v-if="r.refRecipeId">
        <div class="batch">
          <button type="button" class="bstep" aria-label="Fewer batches" @click="stepBatch(r, -0.5)">−</button>
          <span class="bval tnum">{{ fmtBatch(r.quantity) }}</span>
          <button type="button" class="bstep" aria-label="More batches" @click="stepBatch(r, 0.5)">+</button>
          <span class="bunit">{{ Number(r.quantity) === 1 ? 'batch' : 'batches' }}</span>
        </div>
        <div class="food-ref" :title="`Component: ${r.refRecipeName}`">
          <span class="link-ico">🔗</span>{{ r.refRecipeName }}
        </div>
      </template>
      <template v-else>
        <input :ref="(el) => setQtyRef(el, i)" v-model="r.quantity" class="qty"
               inputmode="decimal" placeholder="Qty" aria-label="Quantity"
               @keydown.enter.prevent="addAfter(i)" />
        <ComboBox v-model="r.unit" class="unit" :options="units" placeholder="Unit"
                  aria-label="Unit" @enter="addAfter(i)" />
        <ComboBox v-model="r.food" class="food" :options="foods" placeholder="e.g. flour"
                  aria-label="Ingredient" @enter="addAfter(i)" />
      </template>
      <input v-model="r.note" class="note" placeholder="Note (optional)" aria-label="Note"
             @keydown.enter.prevent="addAfter(i)" />
      <div class="ctl">
        <button type="button" class="icon" :disabled="i === 0" title="Move up" aria-label="Move up" @click="move(i, -1)">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10l4-4 4 4" /></svg>
        </button>
        <button type="button" class="icon" :disabled="i === rows.length - 1" title="Move down" aria-label="Move down" @click="move(i, 1)">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6l4 4 4-4" /></svg>
        </button>
        <button type="button" class="icon rm" title="Remove" aria-label="Remove" @click="remove(i)">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8" /></svg>
        </button>
      </div>
      <!-- The line the parser was handed, so a wrong split is visible against its
           ground truth. Must come AFTER the controls: it spans every column, so
           in DOM order before them it pushes them onto a grid row of their own. -->
      <p v-if="r.sourceText" class="src">
        <span class="src-txt">{{ r.sourceText }}</span>
        <button type="button" class="src-hide" @click="r.sourceText = ''">Hide original</button>
      </p>
      </div>
    </template>

    <p v-if="lastRemoved" class="undo">
      Removed <strong>{{ removedLabel(lastRemoved.row) }}</strong>.
      <button type="button" class="ghost sm" @click="undoRemove">Undo</button>
    </p>

    <div class="actions">
      <button type="button" class="ghost add" @click="add">＋ Add ingredient</button>
      <button type="button" class="ghost add" @click="openPicker">🔗 Add recipe as component</button>
      <span class="kbd-hint">Enter adds a row · Alt+↑/↓ moves one</span>
    </div>

    <!-- Recipe picker -->
    <div v-if="showPicker" class="picker card">
      <div class="row" style="gap:8px">
        <input v-model="pickerQuery" class="fill" placeholder="Search your recipes…" @input="onPickerInput" />
        <button type="button" class="ghost sm" @click="showPicker = false">Close</button>
      </div>
      <ul class="picker-list">
        <li v-for="r in pickerResults" :key="r.id" @click="addComponent(r)">
          <span class="pk-name">{{ r.name }}</span>
          <span v-if="r.totalMinutes" class="muted tnum" style="font-size:0.8rem">{{ r.totalMinutes }} min</span>
        </li>
        <li v-if="!pickerResults.length" class="muted" style="cursor:default">No recipes found.</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.ing-rows { display: flex; flex-direction: column; gap: 6px; }
.head { display: flex; align-items: center; }
.head .lbl { font-weight: 600; font-size: 0.88rem; }
.paste { background: var(--surface-2, var(--surface)); }
.paste textarea { width: 100%; }

/* qty | unit | food (widest) | note | controls
   The qty track was a hardcoded 64px — the only inflexible column in the row —
   leaving ~38px of usable input for "1 1/2", a first-class supported format that
   therefore scrolled inside its own box. It now flexes and grows with large text. */
.col-heads,
.ing-row { display: grid; grid-template-columns: minmax(80px, 0.6fr) 92px minmax(0, 1.7fr) minmax(0, 1fr) 96px; gap: 8px; align-items: center; }
.col-heads { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); padding: 0 2px; }
.ing-row input { width: 100%; }

/* The line the paste parser was handed, under the row it produced. Margin is
   asymmetric on purpose: it belongs to the row ABOVE it, so it must sit closer to
   that row than to the next one. Sized at 0.85rem because the whole job is
   comparing it against 16px inputs — 10-12px text can't do that. There's
   deliberately no "PASTED" eyebrow: it repeated one constant word on every row
   and encoded nothing per row. */
.src {
  grid-column: 1 / -1; display: flex; align-items: baseline; gap: 8px;
  margin: 0 0 12px; font-size: 0.85rem; color: var(--muted);
}
.src-txt { min-width: 0; overflow-wrap: anywhere; }
/* A WORD, not a ✕. The row's destructive Remove is also a ✕ a few hundred px
   away on the same band — reusing the glyph for "hide a hint" put a benign and a
   destructive action behind identical affordances. */
.src-hide {
  flex-shrink: 0; border: 0; background: transparent; padding: 0;
  font: inherit; color: var(--muted); cursor: pointer; text-decoration: underline;
}
.src-hide:hover { color: var(--text); }

/* Section label: a quiet line between rows, styled like the read view's
   sub-heading. */
.sec-lbl {
  margin: 6px 0 0;
  font-size: 0.72rem; font-weight: 650; text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--muted);
}

.undo {
  display: flex; align-items: center; gap: 8px; margin: 4px 0 0;
  font-size: 0.85rem; color: var(--muted);
}
.kbd-hint { align-self: center; font-size: 0.76rem; color: var(--muted); }
@media (max-width: 620px) { .kbd-hint { display: none; } }

/* Batch stepper for a component row — spans the qty+unit columns. */
.batch { grid-column: 1 / 3; display: flex; align-items: center; gap: 6px; }
.bstep {
  width: 28px; height: 28px; padding: 0; flex-shrink: 0; line-height: 1;
  border: 1px solid var(--border); background: var(--surface);
  border-radius: 6px; font-size: 1rem; cursor: pointer;
}
.bstep:hover { border-color: var(--accent); color: var(--accent-text); }
.bval { min-width: 2.2ch; text-align: center; font-weight: 700; }
.bunit { color: var(--muted); font-size: 0.85rem; }

.food-ref {
  display: flex; align-items: center; gap: 6px; min-width: 0;
  padding: 8px 10px; font-weight: 600;
  background: var(--accent-soft); border: 1px solid var(--accent);
  color: var(--accent-text); border-radius: var(--radius-sm);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.food-ref .link-ico { flex-shrink: 0; }

.ctl { display: flex; gap: 2px; justify-content: flex-start; }
.icon { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; padding: 0; border: 0; background: transparent; color: var(--muted); border-radius: 6px; cursor: pointer; }
.icon svg { width: 16px; height: 16px; }
.icon:hover:not(:disabled) { background: var(--surface-2); color: var(--text); }
.icon.rm:hover:not(:disabled) { color: var(--danger); }
.icon:disabled { opacity: 0.25; cursor: default; }
/* Wraps: at 390px the three labels ran past the card edge and were clipped. */
.actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 4px; }
.add { align-self: flex-start; }

.picker { margin-top: 8px; background: var(--surface-2, var(--surface)); }
.picker-list { list-style: none; margin: 10px 0 0; padding: 0; max-height: 240px; overflow: auto; }
.picker-list li { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 10px; border-radius: 6px; cursor: pointer; }
.picker-list li:hover { background: var(--surface); }
.pk-name { font-weight: 600; }

@media (max-width: 620px) {
  .col-heads { display: none; }
  .ing-row { grid-template-columns: 1fr 1fr auto; gap: 8px; }
  .ing-row .food, .ing-row .food-ref, .ing-row .note { grid-column: 1 / -1; }
  .ctl { grid-column: 3; grid-row: 1; justify-content: flex-end; }
  .ing-row + .ing-row { border-top: 1px solid var(--border); padding-top: 12px; margin-top: 8px; }
  /* Reorder/remove are 28px targets 2px apart on a phone. The read surface's
     controls were raised for exactly this reason; the editor's were missed. */
  .icon { width: 40px; height: 40px; }
  .ctl { gap: 4px; }
}
</style>
