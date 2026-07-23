import { defineStore } from 'pinia'

let toastId = 0

export const useUI = defineStore('ui', {
  state: () => ({
    theme: localStorage.getItem('mymeal_theme') || 'auto',
    toasts: [],
    // The ambient cooking assistant's open state lives here so any view (e.g.
    // a dashboard "Ask the assistant" card) can open it — the FAB is not the
    // only way in. ChatAssistant owns the panel; this is just the toggle.
    assistantOpen: false,
    // Optional text to prefill/send when the assistant opens from elsewhere.
    assistantPrompt: null,
    // Bumped whenever data is mutated in-app (e.g. a chat-assistant action) so
    // every live view refetches instantly instead of waiting for the poll.
    dataVersion: 0,
  }),
  actions: {
    // Signal that shared data changed — live views (useLoader) refresh on this.
    dataChanged() {
      this.dataVersion++
    },
    openAssistant(prompt = null) {
      this.assistantPrompt = prompt
      this.assistantOpen = true
    },
    closeAssistant() {
      this.assistantOpen = false
    },
    toggleAssistant() {
      this.assistantOpen = !this.assistantOpen
    },
    applyTheme() {
      const resolved =
        this.theme === 'auto'
          ? window.matchMedia('(prefers-color-scheme: dark)').matches
            ? 'dark'
            : 'light'
          : this.theme
      document.documentElement.setAttribute('data-theme', resolved)
    },
    setTheme(t) {
      this.theme = t
      localStorage.setItem('mymeal_theme', t)
      this.applyTheme()
    },
    toggleTheme() {
      const resolved = document.documentElement.getAttribute('data-theme')
      this.setTheme(resolved === 'dark' ? 'light' : 'dark')
    },
    toast(message, type = 'success') {
      const id = ++toastId
      this.toasts.push({ id, message, type })
      // Errors persist until dismissed; success/info auto-dismiss.
      if (type !== 'error') setTimeout(() => this.dismiss(id), 3200)
    },
    error(message) {
      this.toast(message, 'error')
    },
    dismiss(id) {
      this.toasts = this.toasts.filter((t) => t.id !== id)
    },
  },
})
