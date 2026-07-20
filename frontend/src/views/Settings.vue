<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'

const providers = ref([])
const loading = ref(true)

onMounted(async () => {
  try {
    const res = await api.get('/ai/providers')
    providers.value = res.providers
  } finally {
    loading.value = false
  }
})

const labels = { claude: 'Claude (Anthropic)', openai: 'OpenAI', ollama: 'Ollama (local)' }
</script>

<template>
  <div class="page-head"><h1>Settings</h1></div>

  <div class="card">
    <h2>AI provider</h2>
    <p class="muted">
      myMeal talks to a pluggable AI backend for recipe import, meal planning, and
      the cooking assistant. Configure it with the <code>MYMEAL_AI_PROVIDER</code>
      environment variable (or the add-on options) plus its API key/host — this
      page shows status only.
    </p>
    <p class="muted" style="font-size:0.82rem">
      Already run <strong>Ollama</strong> for Home Assistant's conversation agent?
      Reuse the same server: set <code>MYMEAL_AI_PROVIDER=ollama</code> and
      <code>MYMEAL_OLLAMA_HOST</code> to that Ollama's URL. (myMeal can't route
      through HA's Ollama integration, but it talks to the same server directly.)
    </p>
    <div v-if="loading" class="skeleton" style="height:120px;margin-top:12px"></div>
    <div v-else style="margin-top:12px">
      <div
        v-for="p in providers"
        :key="p.name"
        class="row"
        style="padding:12px 0;border-bottom:1px solid var(--border)"
      >
        <div class="fill">
          <div style="font-weight:600">{{ labels[p.name] || p.name }}</div>
          <div class="muted" style="font-size:0.82rem">
            {{ p.available ? 'Configured' : 'Not configured' }}
          </div>
        </div>
        <span v-if="p.active" class="chip">Active</span>
        <span v-else-if="p.available" class="badge ok">Ready</span>
        <span v-else class="badge">Off</span>
      </div>
    </div>
  </div>
</template>
