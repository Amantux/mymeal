<script setup>
// The six ways to get a recipe in, as one control shown identically wherever a
// recipe is created.
//
// They used to be split across two pages that didn't acknowledge each other:
// "New recipe" offered the form and the AI draft, "Import" offered the other
// four, and the only bridge was a muted text link one way. So the four import
// methods were invisible from the page called "New recipe", and someone who
// landed on Import had no way back to typing it out.
//
// METHODS is deliberately the single source of truth for the set. Two pages each
// keeping their own list is how one of them ends up missing an option.
import { useRoute, useRouter } from 'vue-router'

const props = defineProps({
  // The id of the method currently in use ON THIS PAGE. A method belonging to the
  // other page is never "active" here — selecting it navigates instead.
  modelValue: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const router = useRouter()
const route = useRoute()

// `page` is where the method lives; `mode` is the wire value RecipeImport already
// uses for it. The ids are ours, the modes are its — kept apart so renaming a
// label never silently changes what gets POSTed.
const METHODS = [
  { id: 'type', emoji: '✍️', label: 'Type it out', hint: 'Fill in the form yourself', page: 'builder' },
  { id: 'draft', emoji: '✨', label: 'Draft with AI', hint: 'Describe a dish and edit it', page: 'builder' },
  { id: 'text', emoji: '📋', label: 'Paste', hint: 'Text, or copied from a web page', page: 'import' },
  { id: 'url', emoji: '🔗', label: 'From a link', hint: 'Any recipe URL', page: 'import' },
  { id: 'photo', emoji: '📷', label: 'From a photo', hint: 'A card, page or handwritten note', page: 'import' },
  { id: 'search', emoji: '🔎', label: 'By name', hint: 'Look it up in a free recipe database', page: 'import' },
  { id: 'archive', emoji: '📦', label: 'From another app', hint: 'A Mealie, Tandoor or Paprika export file', page: 'import' },
]

// Which page is hosting us, inferred from the route rather than a prop — one less
// thing a caller can get wrong.
const here = () => (route.path.startsWith('/import') ? 'import' : 'builder')

function choose(method) {
  if (method.id === props.modelValue) return
  if (method.page === here()) {
    emit('update:modelValue', method.id)
    return
  }
  // Cross-page: carry the choice in the URL so refresh, back and a bookmarked
  // link all land on the same method.
  router.push(method.page === 'import'
    ? { path: '/import', query: { mode: method.id } }
    : { path: '/recipes/new', query: method.id === 'draft' ? { mode: 'draft' } : {} })
}
</script>

<template>
  <div class="methods" role="tablist" aria-label="How to add this recipe">
    <button
      v-for="m in METHODS"
      :key="m.id"
      type="button"
      role="tab"
      :aria-selected="m.id === modelValue"
      :class="{ active: m.id === modelValue }"
      :title="m.hint"
      @click="choose(m)"
    >
      <span class="m-emoji" aria-hidden="true">{{ m.emoji }}</span>
      <span class="m-label">{{ m.label }}</span>
    </button>
  </div>
</template>

<style scoped>
/* Wraps rather than scrolls: six options don't fit one phone line, and a
   horizontally-scrolling tab strip hides the very choices this exists to show. */
.methods {
  display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px;
}
.methods button {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 8px 14px; font-weight: 600;
  background: var(--surface); color: var(--muted);
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  cursor: pointer;
}
.methods button:hover { color: var(--text); background: var(--surface-2); }
/* Active is a neutral raised fill, NOT the accent: this is a work surface and
   the accent belongs to the page's one primary action (Save recipe / Import). */
.methods button.active {
  background: var(--surface-2); color: var(--text);
  border-color: var(--text); font-weight: 700;
}
.m-emoji { font-size: 1rem; line-height: 1; }

@media (max-width: 620px) {
  /* Two per row at phone width. The label stays — an emoji-only strip is a
     guessing game, and these are six genuinely different things. */
  .methods { gap: 6px; }
  .methods button { flex: 1 1 calc(50% - 6px); justify-content: flex-start; padding: 10px 12px; }
}
</style>
