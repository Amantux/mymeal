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
        <button type="button" class="icon" :disabled="i === 0" aria-label="Move up" @click="move(i, -1)">↑</button>
        <button type="button" class="icon" :disabled="i === rows.length - 1" aria-label="Move down" @click="move(i, 1)">↓</button>
        <button type="button" class="icon danger" aria-label="Remove step" @click="remove(i)">✕</button>
      </div>
    </div>
    <button type="button" class="ghost add" @click="add">＋ Add step</button>
  </div>
</template>

<style scoped>
.step-rows { display: flex; flex-direction: column; gap: 8px; }
.head .lbl { font-weight: 600; font-size: 0.88rem; }
.step-row { display: grid; grid-template-columns: 28px 1fr auto; gap: 8px; align-items: start; }
.num { text-align: center; padding-top: 8px; font-weight: 700; color: var(--muted); }
.text { width: 100%; resize: vertical; }
.ctl { display: flex; flex-direction: column; gap: 4px; }
.icon { padding: 6px 9px; line-height: 1; font-size: 0.9rem; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm); }
.icon.danger { color: var(--danger); }
.icon:disabled { opacity: 0.35; }
.add { align-self: flex-start; margin-top: 2px; }
@media (max-width: 620px) {
  .step-row { grid-template-columns: 22px 1fr auto; }
}
</style>
