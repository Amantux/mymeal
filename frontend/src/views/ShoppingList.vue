<script setup>
import { ref, computed } from 'vue'
import { api } from '../api'
import { useUI } from '../stores/ui'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import { useLoader } from '../composables/useLoader'

const ui = useUI()
const lists = ref([])
const activeId = ref(null)
const newItem = ref('')
const bulkInput = ref('')

const loading = ref(false)
const error = ref(null)

async function load() {
  lists.value = (await api.get('/shopping-lists')).items
  if (!activeId.value && lists.value.length) activeId.value = lists.value[0].id
}

async function reload() {
  loading.value = true
  error.value = null
  try {
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

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

const checkedCount = computed(() => {
  return active.value?.items.filter(i => i.checked).length || 0
})

async function newList() {
  try {
    const sl = await api.post('/shopping-lists', { name: 'Shopping List' })
    activeId.value = sl.id
    await reload()
  } catch (e) {
    ui.error(e.message)
  }
}

async function addItem() {
  if (!newItem.value.trim() || !active.value) return
  await api.post(`/shopping-lists/${active.value.id}/items`, { display: newItem.value })
  newItem.value = ''
  await refreshActive()
}

async function bulkAddItems() {
  if (!bulkInput.value.trim() || !active.value) return
  const lines = bulkInput.value.split('\n').filter(line => line.trim())
  if (lines.length > 0) {
    await api.post(`/shopping-lists/${active.value.id}/bulk-items`, { lines })
    bulkInput.value = ''
    await refreshActive()
  }
}

async function toggle(item) {
  await api.put(`/shopping-lists/items/${item.id}`, { checked: !item.checked })
  await refreshActive()
}

async function del(item) {
  await api.del(`/shopping-lists/items/${item.id}`)
  await refreshActive()
}

async function clearChecked() {
  if (!active.value || checkedCount.value === 0) return
  if (!confirm(`Remove ${checkedCount.value} checked item(s)?`)) return
  await api.post(`/shopping-lists/${active.value.id}/clear-checked`)
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
    <button class="secondary" @click="newList">＋ New list</button>
  </div>

  <div v-if="loading" class="skeleton" style="height:120px"></div>
  <ErrorState v-else-if="error" :message="error" @retry="reload" />
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
      <!-- Bulk quick-add section -->
      <div style="margin-bottom:16px">
        <label class="section-label" style="padding-left:0">Quick add (one item per line)</label>
        <div class="row" style="margin-bottom:8px">
          <textarea
            v-model="bulkInput"
            placeholder="Paste or type multiple items, one per line...
2 cups flour
1 gal milk
salt"
            style="width:100%;min-height:80px;padding:8px"
            @keyup.enter.exact="bulkAddItems"
          ></textarea>
        </div>
        <button @click="bulkAddItems" class="secondary">Add all items</button>
      </div>

      <!-- Single item add section -->
      <div class="row" style="margin-bottom:14px">
        <input v-model="newItem" placeholder="Add an item…" class="fill" @keyup.enter="addItem" />
        <button @click="addItem">Add</button>
      </div>

      <!-- Clear checked button -->
      <div v-if="checkedCount > 0" class="row" style="margin-bottom:14px">
        <button class="ghost sm danger" @click="clearChecked">
          Clear {{ checkedCount }} checked item{{ checkedCount > 1 ? 's' : '' }}
        </button>
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
          <button class="ghost sm rm" :aria-label="`Remove ${item.display}`" @click.prevent="del(item)">✕</button>
        </label>
      </div>
    </div>
  </template>
</template>

<style scoped>
/* Row remove: a quiet muted × that reddens on hover, not a solid-red column. */
.rm { color: var(--muted); }
.rm:hover { color: var(--danger); background: transparent; }
</style>
