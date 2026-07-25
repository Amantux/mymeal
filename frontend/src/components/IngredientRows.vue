<script setup>
// Mealie-style structured ingredient editor: one row per ingredient with
// separate Quantity / Unit / Food / Note fields, add·remove·reorder, and
// autocomplete (ComboBox) against the group's real units + foods. A "Paste list"
// box parses pasted lines into rows via /recipes/parse. A row can also LINK
// another recipe as a component (refRecipeId) — inserted via the recipe picker,
// rendered as a read-only link. v-model is an array of
// { quantity, unit, food, note, refRecipeId, refRecipeName } rows.
import { ref, watch, onMounted } from 'vue'
import { api } from '../api'
import { useUI } from '../stores/ui'
import ComboBox from './ComboBox.vue'

const props = defineProps({ modelValue: { type: Array, default: () => [] } })
const emit = defineEmits(['update:modelValue'])
const ui = useUI()

const foods = ref([])
const units = ref([])

function blank() {
  return { quantity: '', unit: '', food: '', note: '', refRecipeId: '', refRecipeName: '' }
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
function remove(i) {
  rows.value.splice(i, 1)
  if (!rows.value.length) rows.value.push(blank())
}
function move(i, delta) {
  const j = i + delta
  if (j < 0 || j >= rows.value.length) return
  const [r] = rows.value.splice(i, 1)
  rows.value.splice(j, 0, r)
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
    <div v-for="(r, i) in rows" :key="i" class="ing-row" :class="{ 'is-ref': r.refRecipeId }">
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
        <input v-model="r.quantity" class="qty" inputmode="decimal" placeholder="Qty" aria-label="Quantity" />
        <ComboBox v-model="r.unit" class="unit" :options="units" placeholder="Unit" aria-label="Unit" />
        <ComboBox v-model="r.food" class="food" :options="foods" placeholder="e.g. flour" aria-label="Ingredient" />
      </template>
      <input v-model="r.note" class="note" placeholder="Note (optional)" aria-label="Note" />
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
    </div>

    <div class="row" style="gap:14px;margin-top:4px">
      <button type="button" class="ghost add" @click="add">＋ Add ingredient</button>
      <button type="button" class="ghost add" @click="openPicker">🔗 Add recipe as component</button>
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

/* qty | unit | food (widest) | note | controls */
.col-heads,
.ing-row { display: grid; grid-template-columns: 64px 92px minmax(0, 1.7fr) minmax(0, 1fr) 96px; gap: 8px; align-items: center; }
.col-heads { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); padding: 0 2px; }
.ing-row input { width: 100%; }

/* Batch stepper for a component row — spans the qty+unit columns. */
.batch { grid-column: 1 / 3; display: flex; align-items: center; gap: 6px; }
.bstep {
  width: 28px; height: 28px; padding: 0; flex-shrink: 0; line-height: 1;
  border: 1px solid var(--border); background: var(--surface);
  border-radius: 6px; font-size: 1rem; cursor: pointer;
}
.bstep:hover { border-color: var(--accent); color: var(--accent); }
.bval { min-width: 2.2ch; text-align: center; font-weight: 700; }
.bunit { color: var(--muted); font-size: 0.85rem; }

.food-ref {
  display: flex; align-items: center; gap: 6px; min-width: 0;
  padding: 8px 10px; font-weight: 600;
  background: var(--accent-soft); border: 1px solid var(--accent);
  color: var(--accent); border-radius: var(--radius-sm);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.food-ref .link-ico { flex-shrink: 0; }

.ctl { display: flex; gap: 2px; justify-content: flex-start; }
.icon { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; padding: 0; border: 0; background: transparent; color: var(--muted); border-radius: 6px; cursor: pointer; }
.icon svg { width: 16px; height: 16px; }
.icon:hover:not(:disabled) { background: var(--surface-2, rgba(0, 0, 0, 0.06)); color: var(--text); }
.icon.rm:hover:not(:disabled) { color: var(--danger); }
.icon:disabled { opacity: 0.25; cursor: default; }
.add { align-self: flex-start; margin-top: 4px; }

.picker { margin-top: 8px; background: var(--surface-2, var(--surface)); }
.picker-list { list-style: none; margin: 10px 0 0; padding: 0; max-height: 240px; overflow: auto; }
.picker-list li { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 10px; border-radius: 6px; cursor: pointer; }
.picker-list li:hover { background: var(--surface); }
.pk-name { font-weight: 600; }

@media (max-width: 620px) {
  .col-heads { display: none; }
  .ing-row { grid-template-columns: 1fr 1fr auto; gap: 6px; }
  .ing-row .food, .ing-row .food-ref, .ing-row .note { grid-column: 1 / -1; }
  .ctl { grid-column: 3; grid-row: 1; justify-content: flex-end; }
  .ing-row + .ing-row { border-top: 1px solid var(--border); padding-top: 12px; margin-top: 6px; }
}
</style>
