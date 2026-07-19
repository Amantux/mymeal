<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { api } from '../api'
import { useUI } from '../stores/ui'

const ui = useUI()
const sessions = ref([])
const sessionId = ref(null)
const messages = ref([])
const input = ref('')
const busy = ref(false)
const scroller = ref(null)

async function loadSessions() {
  sessions.value = (await api.get('/ai/chat/sessions')).items
}
async function openSession(id) {
  sessionId.value = id
  const s = await api.get(`/ai/chat/sessions/${id}`)
  messages.value = s.messages
  scrollDown()
}
function newChat() {
  sessionId.value = null
  messages.value = []
}
onMounted(loadSessions)

function scrollDown() {
  nextTick(() => {
    if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
  })
}

async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  input.value = ''
  messages.value.push({ id: `tmp-${Date.now()}`, role: 'user', content: text, toolTrace: [] })
  scrollDown()
  busy.value = true
  try {
    const res = await api.post('/ai/chat', { sessionId: sessionId.value, message: text })
    sessionId.value = res.sessionId
    messages.value.push(res.message)
    await loadSessions()
    scrollDown()
  } catch (e) {
    ui.error(e.message)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page-head">
    <h1>Cooking assistant</h1>
    <div class="grow"></div>
    <select :value="sessionId || ''" @change="(e) => e.target.value ? openSession(e.target.value) : newChat()" style="max-width:220px">
      <option value="">＋ New chat</option>
      <option v-for="s in sessions" :key="s.id" :value="s.id">{{ s.title }}</option>
    </select>
    <button class="secondary" @click="newChat">New</button>
  </div>

  <div class="card" style="display:flex;flex-direction:column;height:calc(100vh - 200px)">
    <div ref="scroller" style="flex:1;overflow-y:auto;padding-right:4px">
      <div v-if="!messages.length" class="empty-state">
        <div class="ico">🍳</div>
        <h3>Ask me anything about cooking</h3>
        <p>"What can I make for dinner?" · "Find me a quick pasta" · "Add eggs to my shopping list"</p>
      </div>
      <div v-for="m in messages" :key="m.id" style="margin-bottom:14px">
        <div :style="m.role === 'user' ? 'text-align:right' : ''">
          <span :style="m.role === 'user'
            ? 'display:inline-block;background:var(--accent-soft);color:var(--accent);padding:9px 13px;border-radius:14px;max-width:80%;text-align:left'
            : 'display:inline-block;background:var(--surface-2);padding:9px 13px;border-radius:14px;max-width:85%;white-space:pre-wrap'">
            {{ m.content }}
          </span>
        </div>
        <div v-if="m.toolTrace && m.toolTrace.length" class="muted" style="font-size:0.72rem;margin-top:4px">
          🔧 {{ m.toolTrace.map((t) => t.tool).join(', ') }}
        </div>
      </div>
      <div v-if="busy" class="muted" style="font-size:0.85rem">Thinking…</div>
    </div>

    <div class="row" style="margin-top:12px">
      <input v-model="input" class="fill" placeholder="Message the assistant…" @keyup.enter="send" :disabled="busy" />
      <button @click="send" :disabled="busy || !input.trim()">Send</button>
    </div>
  </div>
</template>
