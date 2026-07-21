<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
import { useUI } from '../stores/ui'

const ui = useUI()
const tokens = ref([])
const newToken = ref(null)

async function load() {
  tokens.value = await api.get('/tokens')
}
onMounted(load)

async function createToken() {
  const res = await api.post('/tokens', { name: 'Home Assistant' })
  newToken.value = res.token
  await load()
}

async function remove(id) {
  await api.del(`/tokens/${id}`)
  await load()
}

function copy(text) {
  navigator.clipboard?.writeText(text)
  ui.toast('Copied')
}

// A one-paste "connect link" bundling this myMeal's address + a key, for a
// sibling app (e.g. Edibl) to consume. Frontend-only, mirrors Edibl's format.
function connectLink(token) {
  const url = typeof window !== 'undefined' ? window.location.origin : ''
  const payload = JSON.stringify({ app: 'mymeal', url, token, v: 1 })
  return 'mymeal-connect:' + btoa(unescape(encodeURIComponent(payload)))
}
</script>

<template>
  <div class="page-head"><h1>Home Assistant</h1></div>

  <div class="card">
    <h2>Connect myMeal to Home Assistant</h2>
    <p class="muted">
      myMeal runs a Model Context Protocol (MCP) server so Home Assistant's Assist can
      answer questions like <em>"what's for dinner tonight?"</em> and manage your
      shopping list by voice. When running as an add-on behind ingress this works with
      no token; for a standalone install, create an API key below and paste it into the
      integration.
    </p>
    <div class="row" style="margin-top:12px">
      <button @click="createToken">Create API key</button>
    </div>
    <div v-if="newToken" class="card" style="margin-top:14px;background:var(--surface-2)">
      <p style="margin-top:0"><strong>Copy this key now</strong> — it won't be shown again.</p>
      <div class="row">
        <code style="flex:1;word-break:break-all">{{ newToken }}</code>
        <button class="secondary sm" @click="copy(newToken)">Copy</button>
        <button class="sm" @click="copy(connectLink(newToken))">🔗 Connect link</button>
      </div>
      <p class="muted" style="font-size:.78rem;margin:8px 0 0">The <strong>connect link</strong> bundles this myMeal address + key — paste it into <em>Edibl → Settings → myMeal</em> to connect in one step.</p>
    </div>
  </div>

  <div class="card">
    <h2>API keys</h2>
    <p v-if="!tokens.length" class="muted">No API keys yet.</p>
    <div v-for="t in tokens" :key="t.id" class="row" style="padding:8px 0;border-bottom:1px solid var(--border)">
      <div class="fill">
        <div>{{ t.name || 'Unnamed key' }}</div>
        <div class="muted" style="font-size:0.8rem">{{ t.hint }}</div>
      </div>
      <button class="ghost sm danger" @click="remove(t.id)">Revoke</button>
    </div>
  </div>
</template>
