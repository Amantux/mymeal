<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useUI } from '../stores/ui'
import EmptyState from '../components/EmptyState.vue'

const router = useRouter()
const ui = useUI()
const items = ref([])
const loading = ref(true)
const form = ref({ label: '', quantity: '', unit: '', location: '' })
const suggestions = ref(null)

async function load() {
  loading.value = true
  try {
    items.value = (await api.get('/pantry')).items
  } finally {
    loading.value = false
  }
}
onMounted(load)

async function add() {
  if (!form.value.label.trim()) return
  try {
    await api.post('/pantry', form.value)
    form.value = { label: '', quantity: '', unit: '', location: '' }
    await load()
  } catch (e) {
    ui.error(e.message)
  }
}
async function del(id) {
  await api.del(`/pantry/${id}`)
  await load()
}
async function whatCanICook() {
  try {
    suggestions.value = (await api.post('/ai/suggest', { limit: 10 })).suggestions
  } catch (e) {
    ui.error(e.message)
  }
}
</script>

<template>
  <div class="page-head">
    <h1>Pantry</h1>
    <div class="grow"></div>
    <button @click="whatCanICook">🍳 What can I cook?</button>
  </div>

  <div class="card">
    <h2>Add item</h2>
    <div class="row wrap">
      <input v-model="form.label" placeholder="Item (e.g. Rice)" style="flex:2;min-width:160px" @keyup.enter="add" />
      <input v-model="form.quantity" placeholder="Qty" type="number" style="width:90px" />
      <input v-model="form.unit" placeholder="Unit" style="width:110px" />
      <input v-model="form.location" placeholder="Location" style="width:140px" />
      <button @click="add">Add</button>
    </div>
  </div>

  <div v-if="suggestions" class="card">
    <h2>Cook with what you have</h2>
    <EmptyState v-if="!suggestions.length" icon="🥘" title="No recipes to match yet" />
    <div v-for="s in suggestions" :key="s.recipeId" class="row" style="padding:8px 0;border-bottom:1px solid var(--border)">
      <a class="fill" href="#" @click.prevent="router.push(`/recipes/${s.recipeId}`)">{{ s.name }}</a>
      <span class="badge" :class="s.coverage === 1 ? 'ok' : ''">
        {{ s.haveCount }}/{{ s.totalCount }} on hand
      </span>
    </div>
  </div>

  <div class="card">
    <h2>In stock</h2>
    <div v-if="loading" class="skeleton" style="height:80px"></div>
    <EmptyState v-else-if="!items.length" icon="🧺" title="Pantry is empty" hint="Add what you have on hand." />
    <div v-else>
      <div v-for="p in items" :key="p.id" class="row" style="padding:8px 0;border-bottom:1px solid var(--border)">
        <span class="fill">{{ p.label }}</span>
        <span class="muted tnum" v-if="p.quantity">{{ p.quantity }} {{ p.unit }}</span>
        <span class="muted" v-if="p.location">· {{ p.location }}</span>
        <button class="ghost sm danger" @click="del(p.id)">✕</button>
      </div>
    </div>
  </div>
</template>
