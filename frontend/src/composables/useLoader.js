import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useUI } from '../stores/ui'

// Standard async-load state for a view: loading / error / reload. Wraps a fetch
// function so every screen gets a consistent skeleton -> error+retry -> content
// flow instead of a stuck skeleton (or blank page) when the API fails.
//
// Live updating (on by default): the view refetches on the in-app signal
// (ui.dataChanged), which is fired by a chat-assistant action and by the central
// change-cursor poller (see useLiveSync) when data changes anywhere. This
// refresh is QUIET — no skeleton flash, and a transient failure keeps the
// current data rather than replacing the view with an error.
export function useLoader(loadFn, { immediate = true, live = true } = {}) {
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
    // Refresh whenever the in-app signal fires (chat action, or the central
    // change-cursor poller detecting an external change). One source of truth
    // for "when to sync"; this composable just reacts to it.
    const stopWatch = watch(() => ui.dataVersion, () => refresh())
    onUnmounted(stopWatch)
  }

  return { loading, error, reload, refresh }
}
