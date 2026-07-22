import { ref, onMounted } from 'vue'

// Standard async-load state for a view: loading / error / reload. Wraps a fetch
// function so every screen gets a consistent skeleton -> error+retry -> content
// flow instead of a stuck skeleton (or blank page) when the API fails.
export function useLoader(loadFn, { immediate = true } = {}) {
  const loading = ref(immediate)
  const error = ref(null)

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

  if (immediate) onMounted(reload)
  return { loading, error, reload }
}
