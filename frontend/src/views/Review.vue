<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
import { useUI } from '../stores/ui'

const ui = useUI()
const categorize = ref([])
const clusters = ref([])
const loading = ref(true)
const failed = ref(false)

async function load() {
  loading.value = true
  failed.value = false
  try {
    categorize.value = (await api.get('/suggestions?kind=categorize')).items || []
    clusters.value = (await api.get('/suggestions?kind=cluster')).items || []
  } catch (e) {
    failed.value = true
    ui.error(e.message || 'Could not load suggestions.')
  } finally { loading.value = false }
}
const acting = ref([])
const isActing = (id) => acting.value.includes(id)
function drop(s, list) { list.value = list.value.filter(x => x.id !== s.id) }
async function accept(s, list) {
  if (isActing(s.id)) return
  acting.value = [...acting.value, s.id]
  try { const r = await api.post(`/suggestions/${s.id}/accept`); drop(s, list); ui.toast(`Applied “${r.label}”.`) }
  catch (e) { ui.error(e.message || 'Could not apply.') }
  finally { acting.value = acting.value.filter(x => x !== s.id) }
}
async function reject(s, list) {
  if (isActing(s.id)) return
  acting.value = [...acting.value, s.id]
  try { await api.post(`/suggestions/${s.id}/reject`); drop(s, list) }
  catch (e) { ui.error(e.message || 'Could not reject.') }
  finally { acting.value = acting.value.filter(x => x !== s.id) }
}
onMounted(load)
</script>

<template>
  <div class="page">
    <h1>Review suggestions</h1>

    <div v-if="loading" class="card"><p class="muted">Loading…</p></div>
    <div v-else-if="failed" class="card">
      <p class="muted">Couldn’t load suggestions. <a href="#" @click.prevent="load">Try again</a>.</p>
    </div>
    <template v-else>
      <div class="card">
        <h2>Recipe tags ({{ categorize.length }})</h2>
        <p v-if="!categorize.length" class="muted">Nothing to review. Run
          <router-link to="/settings">Auto-tag recipes</router-link> in Settings; confident
          tags are applied automatically, less-sure ones show up here.</p>
        <div v-for="s in categorize" :key="s.id" class="review-row">
          <div>
            <strong>{{ s.recipe?.name || '—' }}</strong>
            <span style="opacity:.6"> → </span><span class="pill">{{ s.label }}</span>
            <span class="muted" style="font-size:.8rem"> · {{ Math.round((s.confidence || 0) * 100) }}%<span v-if="s.rationale"> · {{ s.rationale }}</span></span>
          </div>
          <div class="btns">
            <button type="button" class="secondary" :disabled="isActing(s.id)" @click="accept(s, categorize)">Accept</button>
            <button type="button" class="secondary" :disabled="isActing(s.id)" @click="reject(s, categorize)">Reject</button>
          </div>
        </div>
      </div>

      <div class="card">
        <h2>Collections ({{ clusters.length }})</h2>
        <p v-if="!clusters.length" class="muted">Nothing to review. Run
          <router-link to="/settings">Propose collections</router-link> in Settings.</p>
        <div v-for="s in clusters" :key="s.id" class="review-row" style="flex-direction:column;align-items:stretch;gap:4px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <strong>{{ s.label }} <span class="muted" style="font-weight:400;font-size:.85rem">· {{ (s.members || []).length }} recipes</span></strong>
            <div class="btns">
              <button type="button" class="secondary" :disabled="isActing(s.id)" @click="accept(s, clusters)">Accept &amp; tag</button>
              <button type="button" class="secondary" :disabled="isActing(s.id)" @click="reject(s, clusters)">Reject</button>
            </div>
          </div>
          <div class="muted" style="font-size:.85rem">{{ (s.members || []).map(m => m.name).join(', ') }}</div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.review-row {
  display:flex; justify-content:space-between; align-items:center; gap:12px;
  padding:8px 0; border-bottom:1px solid var(--border, #e5e7eb);
}
.review-row:last-child { border-bottom:0; }
.btns { display:flex; gap:6px; flex-shrink:0; }
.pill {
  font-size:.8rem; padding:2px 8px; border-radius:999px;
  background:var(--surface-2, #f1f3f5); border:1px solid var(--border, #e5e7eb);
}
</style>
