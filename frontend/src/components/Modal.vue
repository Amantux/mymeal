<script setup>
import { onMounted, onBeforeUnmount, ref, nextTick } from 'vue'

const props = defineProps({ title: { type: String, default: '' } })
const emit = defineEmits(['close'])

const panel = ref(null)
let lastFocused = null

function focusables() {
  if (!panel.value) return []
  return [...panel.value.querySelectorAll(
    'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])',
  )]
}

function onKeydown(e) {
  if (e.key === 'Escape') { e.stopPropagation(); emit('close'); return }
  if (e.key !== 'Tab') return
  // Focus trap: keep Tab / Shift+Tab inside the dialog.
  const f = focusables()
  if (!f.length) { e.preventDefault(); return }
  const first = f[0], last = f[f.length - 1]
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
}

onMounted(async () => {
  lastFocused = document.activeElement
  document.addEventListener('keydown', onKeydown, true)
  await nextTick()
  ;(focusables()[0] || panel.value)?.focus()
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown, true)
  // Restore focus to whatever opened the dialog (WCAG focus management).
  if (lastFocused && lastFocused.focus) lastFocused.focus()
})
</script>

<template>
  <Teleport to="body">
    <div class="modal-scrim" @click.self="emit('close')">
      <div
        ref="panel"
        class="card modal-panel"
        role="dialog"
        aria-modal="true"
        :aria-label="title || 'Dialog'"
        tabindex="-1"
      >
        <div v-if="title" class="modal-head">
          <h2>{{ title }}</h2>
          <button class="ghost icon-btn" aria-label="Close" @click="emit('close')">✕</button>
        </div>
        <slot />
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.modal-scrim {
  position: fixed; inset: 0; z-index: 100;
  background: rgba(28, 25, 23, 0.5);
  display: flex; align-items: flex-start; justify-content: center;
  padding: 10vh 16px 16px;
}
.modal-panel { width: 100%; max-width: 460px; }
.modal-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.modal-head h2 { margin: 0; }
@media (prefers-reduced-motion: no-preference) {
  .modal-panel { animation: modal-in 0.16s ease; }
  @keyframes modal-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
}
</style>
