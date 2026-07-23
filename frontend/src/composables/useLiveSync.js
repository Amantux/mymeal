import { onMounted, onUnmounted } from 'vue'
import { api } from '../api'
import { useUI } from '../stores/ui'
import { useAuth } from '../stores/auth'

// Central near-live sync. Polls a cheap server-side change-cursor (/ha/version)
// and fires the in-app signal (ui.dataChanged) whenever it moves, so every live
// view refetches the moment data changes anywhere — the chat assistant, the MCP
// server, Home Assistant, or another device (all of which write the same DB).
//
// Why not SSE/WebSocket: the backend runs sync gunicorn (2 workers × 4 threads);
// a held streaming connection occupies a thread for its lifetime, so a handful
// of open tabs would starve the request pool. And an event produced in the MCP
// process or another worker can't reach an in-process stream without a
// cross-process bus the stack doesn't have. Polling a tiny DB-derived cursor is
// cheaper, sees every writer, and degrades gracefully. Mount once (App.vue).
const POLL_MS = 12000

export function useLiveSync() {
  const ui = useUI()
  const auth = useAuth()
  let last = null
  let timer = null

  async function check() {
    if (document.hidden || !auth.isAuthed) return
    try {
      const { v } = await api.get('/ha/version')
      // First successful read only sets the baseline — the view already loaded
      // fresh, so don't fire a redundant refetch.
      if (last !== null && v !== last) ui.dataChanged()
      last = v
    } catch {
      // Best-effort: a transient failure just means no signal this tick.
    }
  }

  const onFocus = () => check()
  const onVisible = () => {
    if (!document.hidden) check()
  }

  onMounted(() => {
    check()
    timer = setInterval(check, POLL_MS)
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onVisible)
  })

  onUnmounted(() => {
    if (timer) clearInterval(timer)
    window.removeEventListener('focus', onFocus)
    document.removeEventListener('visibilitychange', onVisible)
  })
}
