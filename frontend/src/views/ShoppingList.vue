<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import { useUI } from '../stores/ui'
import EmptyState from '../components/EmptyState.vue'

const ui = useUI()
const lists = ref([])
const activeId = ref(null)
const loading = ref(true)
const newItem = ref('')

async function load() {
  loading.value = true
  try {
    lists.value = (await api.get('/shopping-lists')).items
    if (!activeId.value && lists.value.length) activeId.value = lists.value[0].id
  } finally {
    loading.value = false
  }
}
onMounted(load)

const active = computed(() => lists.value.find((l) => l.id === activeId.value) || null)

// Group items by aisle for tidy shopping.
const grouped = computed(() => {
  const g = {}
  for (const item of active.value?.items || []) {
    const key = item.aisle || 'Other'
    ;(g[key] ||= []).push(item)
  }
  return Object.entries(g).sort((a, b) => a[0].localeCompare(b[0]))
})

async function newList() {
  const sl = await api.post('/shopping-lists', { name: 'Shopping List' })
  activeId.value = sl.id
  await load()
}
async function addItem() {
  if (!newItem.value.trim() || !active.value) return
  await api.post(`/shopping-lists/${active.value.id}/items`, { display: newItem.value })
  newItem.value = ''
  await refreshActive()
}
async function toggle(item) {
  await api.put(`/shopping-lists/items/${item.id}`, { checked: !item.checked })
  await refreshActive()
}
async function del(item) {
  await api.del(`/shopping-lists/items/${item.id}`)
  await refreshActive()
}
async function refreshActive() {
  const fresh = await api.get(`/shopping-lists/${active.value.id}`)
  const idx = lists.value.findIndex((l) => l.id === fresh.id)
  if (idx !== -1) lists.value[idx] = fresh
}
async function delList() {
  if (!confirm('Delete this list?')) return
  await api.del(`/shopping-lists/${active.value.id}`)
  activeId.value = null
  await load()
}
</script>

<template>
  <div class="page-head">
    <h1>Shopping</h1>
    <div class="grow"></div>
    <button @click="newList">＋ New list</button>
  </div>

  <div v-if="loading" class="skeleton" style="height:120px"></div>
  <EmptyState v-else-if="!lists.length" icon="🛒" title="No shopping lists" hint="Create one, or build it from a meal plan.">
    <button @click="newList">＋ New list</button>
  </EmptyState>

  <template v-else>
    <div class="toolbar">
      <select v-model="activeId" style="max-width:280px">
        <option v-for="l in lists" :key="l.id" :value="l.id">{{ l.name }}</option>
      </select>
      <button class="ghost sm danger" @click="delList">Delete list</button>
    </div>

    <div class="card">
      <div class="row" style="margin-bottom:14px">
        <input v-model="newItem" placeholder="Add an item…" class="fill" @keyup.enter="addItem" />
        <button @click="addItem">Add</button>
      </div>

      <EmptyState v-if="!active?.items.length" icon="📝" title="List is empty" />
      <div v-for="[aisle, items] in grouped" :key="aisle" style="margin-bottom:16px">
        <div class="section-label" style="padding-left:0">{{ aisle }}</div>
        <label v-for="item in items" :key="item.id"
               class="row" style="padding:6px 0;border-bottom:1px solid var(--border);cursor:pointer">
          <input type="checkbox" :checked="item.checked" @change="toggle(item)" />
          <span class="fill" :style="item.checked ? 'text-decoration:line-through;color:var(--muted)' : ''">
            <span v-if="item.quantity" class="tnum">{{ item.quantity }} {{ item.unit }} </span>{{ item.display }}
          </span>
          <button class="ghost sm danger" @click.prevent="del(item)">✕</button>
        </label>
      </div>
    </div>
  </template>
</template>
