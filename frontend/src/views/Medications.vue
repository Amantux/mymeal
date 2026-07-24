<script setup>
// Personal medications / vitamins / supplements — dose + frequency. Scoped to
// the signed-in household member by the backend.
import { ref, reactive, computed } from 'vue'
import { api } from '../api'
import { useUI } from '../stores/ui'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import Modal from '../components/Modal.vue'
import { useLoader } from '../composables/useLoader'

const ui = useUI()
const items = ref([])

async function load() {
  items.value = (await api.get('/medications')).items
}
const { loading, error, reload } = useLoader(load)

const KINDS = ['medication', 'vitamin', 'supplement']
const FREQS = [
  ['daily', 'Every day'],
  ['weekly', 'Weekly'],
  ['as_needed', 'As needed'],
]

const editing = ref(null) // the med being edited, or a blank one when creating
const saving = ref(false)
const blank = () => ({
  name: '', kind: 'medication', doseAmount: '', doseUnit: '',
  frequency: 'daily', timesPerDay: 1, scheduleTimes: '', withFood: false, notes: '', active: true,
})
const form = reactive(blank())

function openNew() {
  editing.value = 'new'
  Object.assign(form, blank())
}
function openEdit(m) {
  editing.value = m.id
  Object.assign(form, {
    name: m.name, kind: m.kind, doseAmount: m.doseAmount || '', doseUnit: m.doseUnit,
    frequency: m.frequency, timesPerDay: m.timesPerDay, scheduleTimes: m.scheduleTimes,
    withFood: m.withFood, notes: m.notes, active: m.active,
  })
}

async function save() {
  if (!form.name.trim()) {
    ui.error('Give it a name.')
    return
  }
  saving.value = true
  const payload = { ...form, doseAmount: Number(form.doseAmount) || 0 }
  try {
    if (editing.value === 'new') await api.post('/medications', payload)
    else await api.put(`/medications/${editing.value}`, payload)
    editing.value = null
    await reload()
    ui.toast('Saved')
  } catch (e) {
    ui.error(e.message)
  } finally {
    saving.value = false
  }
}

async function remove(m) {
  if (!confirm(`Remove "${m.name}"?`)) return
  try {
    await api.del(`/medications/${m.id}`)
    await reload()
  } catch (e) {
    ui.error(e.message)
  }
}

const freqLabel = (m) => {
  if (m.frequency === 'as_needed') return 'As needed'
  if (m.frequency === 'weekly') return 'Weekly'
  return m.timesPerDay > 1 ? `${m.timesPerDay}× daily` : 'Daily'
}
const doseLabel = (m) => [m.doseAmount || '', m.doseUnit].filter(Boolean).join(' ')
const showModal = computed(() => editing.value !== null)
</script>

<template>
  <div class="page-head">
    <h1>Medications &amp; vitamins</h1>
    <div class="grow"></div>
    <button @click="openNew">＋ Add</button>
  </div>

  <div v-if="loading" class="skeleton" style="height:160px"></div>
  <ErrorState v-else-if="error" :message="error" @retry="reload" />
  <EmptyState
    v-else-if="!items.length"
    icon="💊"
    title="Nothing tracked yet"
    hint="Add a medication, vitamin, or supplement with its dose and how often you take it."
  >
    <button @click="openNew">Add one</button>
  </EmptyState>

  <div v-else class="card" style="padding:0">
    <div v-for="m in items" :key="m.id" class="med-row" :class="{ inactive: !m.active }">
      <div class="fill">
        <div class="med-top">
          <strong>{{ m.name }}</strong>
          <span class="badge">{{ m.kind }}</span>
          <span v-if="!m.active" class="badge">paused</span>
        </div>
        <div class="sub">
          <span v-if="doseLabel(m)">{{ doseLabel(m) }}</span>
          <span>· {{ freqLabel(m) }}</span>
          <span v-if="m.scheduleTimes">· {{ m.scheduleTimes }}</span>
          <span v-if="m.withFood">· 🍽 with food</span>
        </div>
        <div v-if="m.notes" class="sub muted">{{ m.notes }}</div>
      </div>
      <button class="secondary sm" @click="openEdit(m)">Edit</button>
      <button class="ghost sm danger" :aria-label="`Remove ${m.name}`" @click="remove(m)">✕</button>
    </div>
  </div>

  <Modal v-if="showModal" :title="editing === 'new' ? 'Add' : 'Edit'" @close="editing = null">
    <label class="field"><span>Name</span>
      <input v-model="form.name" placeholder="e.g. Vitamin D3" /></label>
    <div class="row">
      <label class="field fill"><span>Type</span>
        <select v-model="form.kind"><option v-for="k in KINDS" :key="k" :value="k">{{ k }}</option></select>
      </label>
      <label class="field fill"><span>Frequency</span>
        <select v-model="form.frequency">
          <option v-for="[v, lbl] in FREQS" :key="v" :value="v">{{ lbl }}</option>
        </select>
      </label>
    </div>
    <div class="row">
      <label class="field fill"><span>Dose amount</span>
        <input v-model="form.doseAmount" type="number" min="0" step="any" placeholder="1000" /></label>
      <label class="field fill"><span>Unit</span>
        <input v-model="form.doseUnit" placeholder="mg, IU, tablet…" /></label>
      <label v-if="form.frequency === 'daily'" class="field fill"><span>Times / day</span>
        <input v-model="form.timesPerDay" type="number" min="1" max="24" /></label>
    </div>
    <label class="field"><span>Times (optional)</span>
      <input v-model="form.scheduleTimes" placeholder="08:00, 20:00" /></label>
    <label class="row" style="gap:8px;align-items:center;margin:4px 0">
      <input type="checkbox" v-model="form.withFood" /> <span>Take with food</span>
    </label>
    <label v-if="editing !== 'new'" class="row" style="gap:8px;align-items:center;margin:4px 0">
      <input type="checkbox" v-model="form.active" /> <span>Active</span>
    </label>
    <label class="field"><span>Notes</span>
      <textarea v-model="form.notes" rows="2"></textarea></label>
    <div class="row" style="justify-content:flex-end">
      <button class="secondary" @click="editing = null">Cancel</button>
      <button :disabled="saving || !form.name.trim()" @click="save">{{ saving ? 'Saving…' : 'Save' }}</button>
    </div>
  </Modal>
</template>

<style scoped>
.med-row { display: flex; align-items: center; gap: 10px; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.med-row:last-child { border-bottom: 0; }
.med-row.inactive { opacity: 0.55; }
.med-top { display: flex; align-items: center; gap: 8px; }
.med-row .sub { font-size: 0.82rem; color: var(--muted); margin-top: 2px; }
</style>
