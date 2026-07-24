<script setup>
// Structured step editor (Mealie-style): one numbered, editable block per step
// with add / remove / reorder, instead of a blank-line-delimited textarea.
// v-model is an array of { text } rows.
import { ref, watch } from 'vue'

const props = defineProps({ modelValue: { type: Array, default: () => [] } })
const emit = defineEmits(['update:modelValue'])

function blank() { return { text: '' } }
const rows = ref(props.modelValue.length ? props.modelValue.map((r) => ({ ...blank(), ...r })) : [blank()])

// Same content-compare sync as IngredientRows: emit on edit, re-seed only when
// the parent pushes different content (Vue proxies the prop, so an identity
// check would loop forever).
function emitUp() { emit('update:modelValue', rows.value.map((r) => ({ ...r }))) }
watch(rows, emitUp, { deep: true })
watch(() => props.modelValue, (v) => {
  const incoming = JSON.stringify(v || [])
  if (incoming !== JSON.stringify(rows.value)) {
    rows.value = v && v.length ? v.map((r) => ({ ...blank(), ...r })) : [blank()]
  }
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
</script>

<template>
  <div class="step-rows">
    <div class="head"><span class="lbl">Steps</span></div>
    <div v-for="(r, i) in rows" :key="i" class="step-row">
      <div class="num tnum">{{ i + 1 }}</div>
      <textarea v-model="r.text" rows="2" class="text"
        :placeholder="`Step ${i + 1} — what to do`" :aria-label="`Step ${i + 1}`"></textarea>
      <div class="ctl">
        <button type="button" class="icon" :disabled="i === 0" title="Move up" aria-label="Move up" @click="move(i, -1)">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10l4-4 4 4" /></svg>
        </button>
        <button type="button" class="icon" :disabled="i === rows.length - 1" title="Move down" aria-label="Move down" @click="move(i, 1)">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6l4 4 4-4" /></svg>
        </button>
        <button type="button" class="icon rm" title="Remove step" aria-label="Remove step" @click="remove(i)">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8" /></svg>
        </button>
      </div>
    </div>
    <button type="button" class="ghost add" @click="add">＋ Add step</button>
  </div>
</template>

<style scoped>
.step-rows { display: flex; flex-direction: column; gap: 8px; }
.head .lbl { font-weight: 600; font-size: 0.88rem; }
.step-row { display: grid; grid-template-columns: 26px 1fr auto; gap: 10px; align-items: center; }
.num { text-align: center; font-weight: 700; color: var(--muted); font-size: 0.9rem; }
.text { width: 100%; resize: vertical; }
.ctl { display: flex; gap: 2px; }
.icon { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; padding: 0; border: 0; background: transparent; color: var(--muted); border-radius: 6px; cursor: pointer; }
.icon svg { width: 16px; height: 16px; }
.icon:hover:not(:disabled) { background: var(--surface-2, rgba(0, 0, 0, 0.06)); color: var(--text); }
.icon.rm:hover:not(:disabled) { color: var(--danger); }
.icon:disabled { opacity: 0.25; cursor: default; }
.add { align-self: flex-start; margin-top: 2px; }
@media (max-width: 620px) {
  .step-row { grid-template-columns: 22px 1fr auto; gap: 6px; }
}
</style>
