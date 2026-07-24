<script setup>
// Mealie-style structured ingredient editor: one row per ingredient with
// separate Quantity / Unit / Food / Note fields, add·remove·reorder, and food +
// unit autocomplete. A "Paste list" box parses pasted lines into rows via the
// deterministic /recipes/parse endpoint. v-model is an array of
// { quantity, unit, food, note } rows; display is derived by the parent on save.
import { ref, watch, onMounted } from 'vue'
import { api } from '../api'
import { useUI } from '../stores/ui'

const props = defineProps({ modelValue: { type: Array, default: () => [] } })
const emit = defineEmits(['update:modelValue'])
const ui = useUI()

const COMMON_UNITS = ['g', 'kg', 'ml', 'l', 'tsp', 'tbsp', 'cup', 'oz', 'lb',
  'pinch', 'clove', 'slice', 'can', 'pkg']
const foods = ref([])

function blank() { return { quantity: '', unit: '', food: '', note: '' } }
const rows = ref(props.modelValue.length ? props.modelValue.map((r) => ({ ...blank(), ...r })) : [blank()])

// Two-way sync. Emit our rows up on any edit; re-seed from the parent ONLY when
// it pushes genuinely different content (e.g. an AI draft). Guard by CONTENT,
// not reference — Vue proxies the prop, so an identity check would never match
// what we emitted and would loop forever.
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
})

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

// Paste-a-list flow.
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
      quantity: r.quantity || '', unit: r.unit || '', food: r.food || '', note: '',
    }))
    // Replace a single empty starter row; otherwise append.
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

defineExpose({ rows })
</script>

<template>
  <div class="ing-rows">
    <datalist id="ing-units"><option v-for="u in COMMON_UNITS" :key="u" :value="u" /></datalist>
    <datalist id="ing-foods"><option v-for="f in foods" :key="f" :value="f" /></datalist>

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
    <div v-for="(r, i) in rows" :key="i" class="ing-row">
      <input v-model="r.quantity" class="qty" inputmode="decimal" placeholder="Qty" aria-label="Quantity" />
      <input v-model="r.unit" class="unit" list="ing-units" placeholder="Unit" aria-label="Unit" />
      <input v-model="r.food" class="food" list="ing-foods" placeholder="e.g. flour" aria-label="Ingredient" />
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

    <button type="button" class="ghost add" @click="add">＋ Add ingredient</button>
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

.ctl { display: flex; gap: 2px; justify-content: flex-start; }
.icon { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; padding: 0; border: 0; background: transparent; color: var(--muted); border-radius: 6px; cursor: pointer; }
.icon svg { width: 16px; height: 16px; }
.icon:hover:not(:disabled) { background: var(--surface-2, rgba(0, 0, 0, 0.06)); color: var(--text); }
.icon.rm:hover:not(:disabled) { color: var(--danger); }
.icon:disabled { opacity: 0.25; cursor: default; }
.add { align-self: flex-start; margin-top: 4px; }

/* Stack fields on narrow screens so nothing overflows. */
@media (max-width: 620px) {
  .col-heads { display: none; }
  .ing-row { grid-template-columns: 1fr 1fr auto; gap: 6px; }
  .ing-row .food, .ing-row .note { grid-column: 1 / -1; }
  .ctl { grid-column: 3; grid-row: 1; justify-content: flex-end; }
  /* Separate each ingredient GROUP so the stacked fields don't run together. */
  .ing-row + .ing-row { border-top: 1px solid var(--border); padding-top: 12px; margin-top: 6px; }
}
</style>
