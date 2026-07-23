import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useUI } from '../stores/ui'

// How often a live view re-polls the API to catch changes made elsewhere (the
// chat assistant, MCP, Home Assistant, another device). Deliberately light —
// focus/visibility and the in-app signal cover the responsive cases; this is
// just the safety net, and it's paused while the tab is hidden.
const POLL_MS = 45000

// Standard async-load state for a view: loading / error / reload. Wraps a fetch
// function so every screen gets a consistent skeleton -> error+retry -> content
// flow instead of a stuck skeleton (or blank page) when the API fails.
//
// Live updating (on by default): the view refetches when data is mutated in-app
// (ui.dataChanged), when the tab/window regains focus, and on a light poll.
// These refreshes are QUIET — they don't flash the skeleton and a transient
// failure keeps the current data rather than replacing the view with an error.
export function useLoader(loadFn, { immediate = true, live = true, poll = POLL_MS } = {}) {
  const loading = ref(immediate)
  const error = ref(null)
  const ui = useUI()

  async function reload() {
    loading.value = true
    error.value = null
    try {
      await loadFn()
    } catch (e) {
      error.value = e?.message || 'Something went wrong loading this page.'
    } finally {
      loading.value = false
    }
  }

  // Quiet background refresh for live updates: no skeleton flash, and a transient
  // error is swallowed (keep showing the last-good data). reload() stays the
  // explicit, visible path (initial load + user-triggered "Try again").
  async function refresh() {
    if (loading.value) return // don't race an in-flight full load
    try {
      await loadFn()
      error.value = null
    } catch {
      // Keep stale data; leave any existing error untouched.
    }
  }

  if (immediate) onMounted(reload)

  if (live) {
    // 1. In-app signal — a mutation anywhere (a chat-assistant action, etc.)
    //    bumps ui.dataVersion and every live view refreshes at once.
    const stopWatch = watch(() => ui.dataVersion, () => refresh())

    // 2. Refetch when the tab/window regains focus (catches changes made while
    //    the user was away without waiting for the poll).
    const onFocus = () => refresh()
    const onVisible = () => {
      if (!document.hidden) refresh()
    }

    // 3. Light poll, paused while the tab is hidden.
    let timer = null
    if (poll > 0) {
      timer = setInterval(() => {
        if (!document.hidden) refresh()
      }, poll)
    }

    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onVisible)

    onUnmounted(() => {
      stopWatch()
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onVisible)
      if (timer) clearInterval(timer)
    })
  }

  return { loading, error, reload, refresh }
}
