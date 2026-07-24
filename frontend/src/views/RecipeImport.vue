<script setup>
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useUI } from '../stores/ui'

const router = useRouter()
const ui = useUI()

const mode = ref('url')
const url = ref('')
const text = ref('')
const photoFile = ref(null)
const busy = ref(false)

const canRun = computed(() => {
  if (mode.value === 'url') return !!url.value
  if (mode.value === 'text') return !!text.value
  return !!photoFile.value
})

function onPhoto(e) {
  photoFile.value = e.target.files[0] || null
}

async function run() {
  if (!canRun.value) return
  busy.value = true
  try {
    let recipe
    if (mode.value === 'photo') {
      const fd = new FormData()
      fd.append('image', photoFile.value)
      recipe = await api.uploadPost('/ai/photo', fd)
      ui.toast('Recipe scanned from photo')
    } else {
      const body = mode.value === 'url' ? { url: url.value } : { text: text.value }
      recipe = await api.post('/ai/import', body)
      ui.toast('Recipe imported')
    }
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
      <button :class="mode === 'photo' ? '' : 'secondary'" @click="mode = 'photo'">From a photo</button>
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

    <template v-else-if="mode === 'text'">
      <label class="field">
        <span>Recipe text</span>
        <textarea v-model="text" rows="12" placeholder="Paste a full recipe here…"></textarea>
      </label>
      <p class="muted" style="font-size:0.85rem">
        Parsed by your configured AI provider — set one up in Settings if import fails.
      </p>
    </template>

    <template v-else>
      <label class="field">
        <span>Recipe photo</span>
        <input type="file" accept="image/jpeg,image/png,image/webp" @change="onPhoto" />
      </label>
      <p class="muted" style="font-size:0.85rem">
        Snap a recipe card, cookbook page, or handwritten note — your AI provider
        reads it (needs a vision-capable model). The photo becomes the recipe image.
      </p>
    </template>

    <div class="row" style="justify-content:flex-end;margin-top:8px">
      <button :disabled="busy || !canRun" @click="run">
        {{ busy ? (mode === 'photo' ? 'Scanning…' : 'Importing…') : (mode === 'photo' ? 'Scan photo' : 'Import') }}
      </button>
    </div>
  </div>
</template>
