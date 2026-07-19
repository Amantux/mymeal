<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useUI } from '../stores/ui'

const router = useRouter()
const ui = useUI()

const mode = ref('url')
const url = ref('')
const text = ref('')
const busy = ref(false)

async function run() {
  busy.value = true
  try {
    const body = mode.value === 'url' ? { url: url.value } : { text: text.value }
    const recipe = await api.post('/ai/import', body)
    ui.toast('Recipe imported')
    router.push(`/recipes/${recipe.id}`)
  } catch (e) {
    ui.error(e.message)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page-head"><h1>Import a recipe</h1></div>

  <div class="card">
    <div class="tabs" style="display:flex;gap:8px;margin-bottom:16px">
      <button :class="mode === 'url' ? '' : 'secondary'" @click="mode = 'url'">From a link</button>
      <button :class="mode === 'text' ? '' : 'secondary'" @click="mode = 'text'">Paste text</button>
    </div>

    <template v-if="mode === 'url'">
      <label class="field">
        <span>Recipe URL</span>
        <input v-model="url" placeholder="https://…" @keyup.enter="run" />
      </label>
      <p class="muted" style="font-size:0.85rem">
        Structured recipe pages import instantly. For pages without recipe markup,
        your configured AI provider parses the content.
      </p>
    </template>

    <template v-else>
      <label class="field">
        <span>Recipe text</span>
        <textarea v-model="text" rows="12" placeholder="Paste a full recipe here…"></textarea>
      </label>
      <p class="muted" style="font-size:0.85rem">
        Parsed by your configured AI provider — set one up in Settings if import fails.
      </p>
    </template>

    <div class="row" style="justify-content:flex-end;margin-top:8px">
      <button :disabled="busy || (mode === 'url' ? !url : !text)" @click="run">
        {{ busy ? 'Importing…' : 'Import' }}
      </button>
    </div>
  </div>
</template>
