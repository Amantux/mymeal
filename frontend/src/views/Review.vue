<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
import { useUI } from '../stores/ui'

const ui = useUI()
const categorize = ref([])
const clusters = ref([])
const duplicates = ref([])
const loading = ref(true)
const failed = ref(false)

async function load() {
  loading.value = true
  failed.value = false
  try {
    categorize.value = (await api.get('/suggestions?kind=categorize')).items || []
    clusters.value = (await api.get('/suggestions?kind=cluster')).items || []
    duplicates.value = await api.get('/foods/duplicates') || []
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
const pct = (c) => Math.round((c || 0) * 100)

// Ingredient tidy-up. Merging is destructive and not undoable from here, so it
// is always a click, never automatic, and the button says what is lost.
const merging = ref([])
const usage = (f) => f.usageCount === 1 ? '1 recipe' : `${f.usageCount || 0} recipes`
async function mergeInto(group, dupe) {
  if (merging.value.includes(dupe.id)) return
  merging.value = [...merging.value, dupe.id]
  try {
    await api.post(`/foods/${group.keep.id}/merge`, { fromId: dupe.id, confirm: true })
    group.merge = group.merge.filter(f => f.id !== dupe.id)
    if (!group.merge.length) duplicates.value = duplicates.value.filter(g => g !== group)
    ui.toast(`Merged “${dupe.name}” into “${group.keep.name}”.`)
  } catch (e) {
    ui.error(e.message || `Could not merge “${dupe.name}”.`)
  } finally {
    merging.value = merging.value.filter(x => x !== dupe.id)
  }
}
function keepBoth(group, dupe) {
  group.merge = group.merge.filter(f => f.id !== dupe.id)
  if (!group.merge.length) duplicates.value = duplicates.value.filter(g => g !== group)
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
    <div v-else-if="!categorize.length && !clusters.length && !duplicates.length" class="card empty">
      <div class="empty-ico">🗂️</div>
      <p>Nothing to review right now. Run <router-link to="/settings">Auto-tag recipes</router-link> or
        <router-link to="/settings">Propose collections</router-link> in Settings — confident tags apply
        automatically, and anything less certain shows up here.</p>
    </div>
    <template v-else>
      <div v-if="categorize.length" class="card">
        <h2>Recipe tags ({{ categorize.length }})</h2>
        <div v-for="s in categorize" :key="s.id" class="review-row">
          <div class="rr-main">
            <strong>{{ s.recipe?.name || '—' }}</strong>
            <span style="opacity:.6"> → </span><span class="pill">{{ s.label }}</span>
            <span class="muted rr-meta"> · {{ pct(s.confidence) }}%<span v-if="s.rationale"> · {{ s.rationale }}</span></span>
          </div>
          <div class="rr-btns">
            <button type="button" class="secondary" :disabled="isActing(s.id)" @click="accept(s, categorize)">Accept</button>
            <button type="button" class="ghost" :disabled="isActing(s.id)" @click="reject(s, categorize)">Reject</button>
          </div>
        </div>
      </div>

      <div v-if="duplicates.length" class="card">
        <h2>Tidy up ingredients ({{ duplicates.length }})</h2>
        <p class="muted rr-lead">
          These look like the same ingredient written more than one way. Merging moves every
          recipe and shopping line onto the first one and deletes the other — it can’t be undone
          from here, so nothing happens until you choose.
        </p>
        <div v-for="g in duplicates" :key="g.canonical" class="review-row cluster">
          <div class="rr-head">
            <strong class="rr-main">
              Keep “{{ g.keep.name }}”
              <span class="muted" style="font-weight:400;font-size:.85rem">· used in {{ usage(g.keep) }}</span>
            </strong>
          </div>
          <div v-for="d in g.merge" :key="d.id" class="dupe-row">
            <span class="rr-main">
              <span class="muted">merge</span> {{ d.name }}
              <span class="muted" style="font-size:.85rem">· {{ usage(d) }}</span>
            </span>
            <span class="rr-btns">
              <button type="button" class="secondary" :disabled="merging.includes(d.id)"
                      @click="mergeInto(g, d)">
                {{ merging.includes(d.id) ? 'Merging…' : `Merge into “${g.keep.name}”` }}
              </button>
              <button type="button" class="ghost" :disabled="merging.includes(d.id)"
                      @click="keepBoth(g, d)">Keep both</button>
            </span>
          </div>
        </div>
      </div>

      <div v-if="clusters.length" class="card">
        <h2>Collections ({{ clusters.length }})</h2>
        <div v-for="s in clusters" :key="s.id" class="review-row cluster">
          <div class="rr-head">
            <strong class="rr-main">{{ s.label }} <span class="muted" style="font-weight:400;font-size:.85rem">· {{ (s.members || []).length }} recipes</span></strong>
            <div class="rr-btns">
              <button type="button" class="secondary" :disabled="isActing(s.id)" @click="accept(s, clusters)">Accept &amp; tag</button>
              <button type="button" class="ghost" :disabled="isActing(s.id)" @click="reject(s, clusters)">Reject</button>
            </div>
          </div>
          <div class="muted rr-members">{{ (s.members || []).map(m => m.name).join(', ') }}</div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.review-row {
  display:flex; justify-content:space-between; align-items:center; gap:12px;
  padding:10px 0; border-bottom:1px solid var(--border, #e5e7eb);
}
.review-row:last-child { border-bottom:0; }
.review-row.cluster { flex-direction:column; align-items:stretch; gap:4px; }
.rr-head { display:flex; justify-content:space-between; align-items:center; gap:12px; }
.rr-main { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.rr-members { font-size:.85rem; overflow:hidden; text-overflow:ellipsis;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }
.rr-btns { display:flex; gap:6px; flex-shrink:0; }
.rr-lead { font-size:.9rem; margin:0 0 8px; }
.dupe-row {
  display:flex; justify-content:space-between; align-items:center; gap:12px;
  padding:6px 0 6px 12px; border-left:2px solid var(--border, #e5e7eb);
}
.pill {
  font-size:.8rem; padding:2px 8px; border-radius:999px;
  background:var(--surface-2, #f1f3f5); border:1px solid var(--border, #e5e7eb);
}
.ghost { background:none; border:1px solid transparent; color:var(--muted, #6b7280); }
.ghost:hover:not(:disabled) { color:var(--text, #111); border-color:var(--border, #e5e7eb); }
.empty { text-align:center; }
.empty-ico { font-size:2rem; }
@media (max-width:560px) {
  .review-row { flex-direction:column; align-items:stretch; }
  .dupe-row { flex-direction:column; align-items:stretch; gap:6px; }
  .rr-head { flex-direction:column; align-items:stretch; gap:6px; }
  .rr-btns { justify-content:flex-end; }
}
</style>
