<script setup>
import { useUI } from '../stores/ui'
const ui = useUI()
</script>

<template>
  <div class="toast-wrap" aria-live="polite" aria-atomic="false">
    <div v-for="t in ui.toasts" :key="t.id" class="toast" :class="t.type"
         :role="t.type === 'error' ? 'alert' : 'status'" @click="ui.dismiss(t.id)">
      <span>{{ t.message }}</span>
      <button v-if="t.type === 'error'" class="ghost icon-btn toast-close"
              aria-label="Dismiss" @click.stop="ui.dismiss(t.id)">✕</button>
    </div>
  </div>
</template>
