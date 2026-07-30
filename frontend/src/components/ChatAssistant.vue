<script setup>
// Ambient cooking assistant — a floating button available on every page, ported
// from Edibl's ChatAssistant so the two apps share one chat experience. Reuses
// myMeal's session-based /ai/chat backend (multi-turn within an open panel);
// shows suggestion chips when empty and action chips for what the assistant did.
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { api, streamPost } from '../api'
import { useUI } from '../stores/ui'
import { hideMarker, finalizeReply } from '../utils/bugMarker'

const ui = useUI()

// Transport is controlled by the household setting (fetched below) — no per-widget
// toggle. Defaults to classic POST. See docs/chat-and-providers.md.
const householdDefault = ref(false)
const streaming = computed(() => householdDefault.value)
// Whether a usable AI provider is configured, so the widget can show a proactive
// setup state instead of only failing when you send. Optimistic until loaded.
const aiStatus = ref({ enabled: true, provider: '' })
const notConfigured = computed(() => aiStatus.value.enabled === false)
onMounted(async () => {
  try { householdDefault.value = !!(await api.get('/ai/chat-settings')).stream } catch (e) { /* default false */ }
  try { aiStatus.value = await api.get('/ai/status') } catch (e) { /* stay optimistic */ }
})

// Maps a structured undo descriptor (from the server) to a known, safe API
// call. The server never dictates an arbitrary method/path — it names a `kind`
// and an id, and the reversal lives here, on the client, per kind.
// Each fn receives the whole undo descriptor. Cross-app kinds (`edibl_*`) can't
// be reversed from the browser — it can't reach the sibling app — so they post
// the descriptor to the server undo-proxy, which does the reversal.
const UNDO = {
  shopping_item: (u) => api.del(`/shopping-lists/items/${u.id}`),
  recipe: (u) => api.del(`/recipes/${u.id}`),
  edibl_stock: (u) => api.post('/ai/chat/undo', u),
  edibl_shopping: (u) => api.post('/ai/chat/undo', u),
  edibl_unconsume: (u) => api.post('/ai/chat/undo', u),
}

// Open state is shared via the ui store so any view can open the assistant
// (e.g. a dashboard "Ask" card), not just the FAB.
const open = computed(() => ui.assistantOpen)
const msgs = ref([]) // {role, content, actions?, error?}
const input = ref('')
const busy = ref(false)
const sessionId = ref(null)
const body = ref(null)

// Domain-specific openers — the myMeal equivalent of Edibl's stock prompts.
const suggestions = [
  'What can I cook right now?',
  "What's for dinner?",
  'Plan my week',
  'Add eggs to my shopping list',
]

async function scrollDown() {
  await nextTick()
  if (body.value) body.value.scrollTop = body.value.scrollHeight
}

function toggle() {
  ui.toggleAssistant()
}

// React to the panel being opened from anywhere. When another view opens it
// with a prompt, send that prompt once the panel is up.
watch(open, (isOpen) => {
  if (!isOpen) return
  scrollDown()
  if (ui.assistantPrompt) {
    const prompt = ui.assistantPrompt
    ui.assistantPrompt = null
    send(prompt)
  }
})

async function undo(action) {
  const fn = action.undo && UNDO[action.undo.kind]
  if (!fn || action.undoing || action.undone) return
  action.undoing = true
  try {
    await fn(action.undo)
    action.undone = true
    ui.dataChanged() // reverting a change is a change too — refresh live views
  } catch (e) {
    // Most likely the item was already removed elsewhere; treat as undone
    // rather than leaving a stuck spinner, but surface anything unexpected.
    if (e.status === 404) action.undone = true
    else action.undoError = e.message || 'Undo failed'
  } finally {
    action.undoing = false
  }
}

function reset() {
  msgs.value = []
  sessionId.value = null
}

async function sendPost(content) {
  const res = await api.post('/ai/chat', { sessionId: sessionId.value, message: content })
  sessionId.value = res.sessionId
  const actions = res.actions || []
  const { content: clean, summary } = finalizeReply(res.reply)
  msgs.value.push({ role: 'assistant', content: clean, actions, bugReportSummary: summary })
  // If the assistant changed anything (planned a meal, added to the list, …),
  // signal live views to refresh so the change shows without a manual reload.
  if (actions.length) ui.dataChanged()
}

async function sendStream(content) {
  msgs.value.push({ role: 'assistant', content: '', raw: '', actions: [] })
  const idx = msgs.value.length - 1 // mutate via the reactive proxy, not the raw object
  let errored = null
  try {
    await streamPost('/ai/chat/stream', { sessionId: sessionId.value, message: content }, (ev) => {
      const a = msgs.value[idx]
      // Accumulate the raw stream but DISPLAY a marker-hidden view, so [[REPORT_BUG]]
      // (even a half-streamed partial) never flashes in the bubble before `done`.
      if (ev.type === 'delta') { a.raw += ev.text; a.content = hideMarker(a.raw); scrollDown() }
      else if (ev.type === 'done') {
        sessionId.value = ev.sessionId
        const { content: clean, summary } = finalizeReply(ev.reply || a.raw || a.content)
        a.content = clean
        a.bugReportSummary = summary
        a.actions = ev.actions || []
        if (a.actions.length) ui.dataChanged()
      } else if (ev.type === 'error') { errored = new Error(ev.error || 'Something went wrong.') }
    })
  } catch (e) {
    if (!msgs.value[idx].content) msgs.value.splice(idx, 1)
    throw e
  }
  if (errored) { msgs.value.pop(); throw errored }
}

async function send(text) {
  const content = (text ?? input.value).trim()
  if (!content || busy.value) return
  input.value = ''
  msgs.value.push({ role: 'user', content })
  busy.value = true
  await scrollDown()
  try {
    if (streaming.value) await sendStream(content)
    else await sendPost(content)
  } catch (e) {
    // 503 = no AI provider configured; surface the server's guidance inline.
    msgs.value.push({
      role: 'assistant',
      error: true,
      content: e.message || 'Something went wrong.',
    })
  } finally {
    busy.value = false
    await scrollDown()
  }
}
</script>

<template>
  <div class="asst">
    <button
      class="fab"
      :class="{ open }"
      @click="toggle"
      :aria-label="open ? 'Close cooking assistant' : 'Open cooking assistant'"
    >
      <span v-if="!open">💬</span><span v-else>✕</span>
    </button>

    <transition name="asst-panel">
      <section v-if="open" class="panel" role="dialog" aria-label="Cooking assistant">
        <header class="phead">
          <div style="display:flex;gap:8px;align-items:center">
            <strong>🍳 Assistant</strong>
            <span class="ai-tag" :class="aiStatus.enabled ? 'on' : 'off'">
              {{ aiStatus.enabled ? (aiStatus.provider || 'ready') : 'not set up' }}
            </span>
          </div>
          <div style="display:flex;gap:10px;align-items:center">
            <button v-if="msgs.length" class="linkish" @click="reset">New chat</button>
          </div>
        </header>

        <div ref="body" class="pbody">
          <div v-if="!msgs.length" class="empty">
            <template v-if="notConfigured">
              <p class="muted">🔌 No AI provider is set up yet. Choose one — including a
                fully-local <strong>Ollama</strong> — in
                <router-link to="/settings" @click="toggle">Settings → AI provider</router-link>,
                where you can <strong>Find Ollama</strong> and see its available models.</p>
            </template>
            <template v-else>
              <p class="muted">Ask me about recipes, planning, or your shopping list.</p>
              <button
                v-for="s in suggestions"
                :key="s"
                class="chip"
                @click="send(s)"
              >{{ s }}</button>
            </template>
          </div>

          <div v-for="(m, i) in msgs" :key="i" class="turn" :class="m.role">
            <div class="bubble" :class="{ err: m.error }">
              <template v-if="m.content">{{ m.content }}</template>
              <span v-else-if="!m.error" class="muted">…</span>
            </div>
            <div v-if="m.actions && m.actions.length" class="actions">
              <span
                v-for="(a, j) in m.actions"
                :key="j"
                class="action"
                :class="{ undone: a.undone }"
              >
                {{ a.icon }} {{ a.label }}
                <button
                  v-if="a.undo && !a.undone"
                  class="undo"
                  :disabled="a.undoing"
                  @click="undo(a)"
                >{{ a.undoing ? '…' : 'Undo' }}</button>
                <span v-else-if="a.undone" class="undone-tag">Undone</span>
              </span>
            </div>
            <div v-if="m.bugReportSummary" class="actions">
              <button class="bug-report-btn"
                      @click="ui.openBugReport({ description: m.bugReportSummary, type: 'bug' })">
                🐞 Open bug report
              </button>
            </div>
          </div>

          <div v-if="busy && !streaming" class="muted thinking">Thinking…</div>
        </div>

        <div class="pfoot">
          <input
            v-model="input"
            class="pinput"
            :placeholder="notConfigured ? 'Set up an AI provider to chat' : 'Message the assistant…'"
            :disabled="busy || notConfigured"
            @keyup.enter="send()"
            aria-label="Message the assistant"
          />
          <button class="send" :disabled="busy || notConfigured || !input.trim()" @click="send()">Send</button>
        </div>
      </section>
    </transition>
  </div>
</template>

<style scoped>
.fab {
  position: fixed;
  right: 24px;
  bottom: 24px;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  border: none;
  background: var(--accent);
  color: var(--accent-fg);
  font-size: 1.4rem;
  cursor: pointer;
  box-shadow: var(--shadow-lg);
  z-index: 60;
}
.fab.open { background: var(--surface-2); color: var(--text); border: 1px solid var(--border); }

.panel {
  position: fixed;
  right: 24px;
  bottom: 92px;
  width: 360px;
  max-width: calc(100vw - 32px);
  height: 520px;
  max-height: calc(100vh - 128px);
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-lg);
  z-index: 60;
  overflow: hidden;
}
.phead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}
.linkish { background: none; border: none; color: var(--accent); cursor: pointer; font-size: 0.8rem; }
.ai-tag { font-size: 0.66rem; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--border); white-space: nowrap; }
.ai-tag.on { background: var(--accent-soft); color: var(--accent); border-color: transparent; }
.ai-tag.off { background: var(--danger-soft); color: var(--danger); border-color: transparent; }

.pbody { flex: 1; overflow-y: auto; padding: 16px; }
.empty { display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
.chip {
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text);
  border-radius: 999px;
  padding: 7px 13px;
  font-size: 0.82rem;
  cursor: pointer;
  text-align: left;
}
.chip:hover { border-color: var(--accent); }

.turn { margin-bottom: 14px; }
.turn.user { text-align: right; }
.bubble {
  display: inline-block;
  padding: 9px 13px;
  border-radius: 14px;
  max-width: 85%;
  text-align: left;
  white-space: pre-wrap;
  font-size: 0.88rem;
}
.turn.user .bubble { background: var(--accent-soft); color: var(--accent); }
.turn.assistant .bubble { background: var(--surface-2); }
.bubble.err { background: var(--danger-soft); color: var(--danger); }

.actions { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 6px; }
.action {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.74rem;
  background: var(--accent-soft);
  color: var(--accent);
  border-radius: 999px;
  padding: 3px 4px 3px 9px;
}
.action.undone { background: var(--surface-2); color: var(--muted); text-decoration: line-through; }
.bug-report-btn {
  font-size: 0.78rem; font-weight: 600; cursor: pointer;
  background: var(--accent); color: #fff; border: none;
  border-radius: 999px; padding: 5px 12px;
}
.bug-report-btn:hover { filter: brightness(0.95); }
.undo {
  border: 1px solid currentColor;
  background: transparent;
  color: inherit;
  border-radius: 999px;
  padding: 1px 8px;
  font-size: 0.72rem;
  cursor: pointer;
}
.undo:disabled { opacity: 0.6; cursor: default; }
.undone-tag { opacity: 0.7; padding-right: 5px; text-decoration: none; }
.thinking { font-size: 0.85rem; }

.pfoot { display: flex; gap: 8px; padding: 12px; border-top: 1px solid var(--border); }
.pinput {
  flex: 1;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 9px 12px;
  background: var(--surface);
  color: var(--text);
  font-size: 0.88rem;
}
.send {
  border: none;
  background: var(--accent);
  color: var(--accent-fg);
  border-radius: var(--radius-sm);
  padding: 0 16px;
  cursor: pointer;
}
.send:disabled { opacity: 0.5; cursor: default; }

/* One orchestrated motion moment; disabled for reduced-motion. */
.asst-panel-enter-active, .asst-panel-leave-active { transition: opacity 0.16s ease, transform 0.16s ease; }
.asst-panel-enter-from, .asst-panel-leave-to { opacity: 0; transform: translateY(8px); }
@media (prefers-reduced-motion: reduce) {
  .asst-panel-enter-active, .asst-panel-leave-active { transition: none; }
}

/* On phones the panel docks as a full-height sheet instead of a small card
   floating mid-screen over the page content. */
@media (max-width: 560px) {
  .panel {
    right: 0; left: 0; bottom: 0; top: 0;
    width: 100%; max-width: none; height: 100%; max-height: none;
    border: 0; border-radius: 0;
    z-index: 80;
  }
}
</style>
