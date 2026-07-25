<script setup>
// Lightweight accessible autocomplete (Mealie-style): type to filter existing
// options in a dropdown, arrow-key/click to pick, or keep free text (a "Use …"
// row confirms it — new units/foods are find-or-created on save). v-model is the
// string value.
import { ref, computed } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  options: { type: Array, default: () => [] },
  placeholder: { type: String, default: '' },
  ariaLabel: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const open = ref(false)
const active = ref(-1)

const val = computed(() => (props.modelValue || '').trim())
const filtered = computed(() => {
  const q = val.value.toLowerCase()
  const opts = q ? props.options.filter((o) => o.toLowerCase().includes(q)) : props.options
  return opts.slice(0, 8)
})
const exact = computed(() => props.options.some((o) => o.toLowerCase() === val.value.toLowerCase()))
const showAdd = computed(() => val.value && !exact.value)

function set(v) {
  emit('update:modelValue', v)
  open.value = false
  active.value = -1
}
function onInput(e) {
  emit('update:modelValue', e.target.value)
  open.value = true
  active.value = -1
}
function onKeydown(e) {
  const n = filtered.value.length + (showAdd.value ? 1 : 0)
  if (e.key === 'ArrowDown') {
    e.preventDefault(); open.value = true; active.value = Math.min(active.value + 1, n - 1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault(); active.value = Math.max(active.value - 1, 0)
  } else if (e.key === 'Enter') {
    if (open.value && active.value >= 0) {
      e.preventDefault()
      set(active.value < filtered.value.length ? filtered.value[active.value] : val.value)
    } else {
      open.value = false
    }
  } else if (e.key === 'Escape') {
    open.value = false
  }
}
</script>

<template>
  <div class="combo">
    <input
      :value="modelValue"
      :placeholder="placeholder"
      :aria-label="ariaLabel"
      role="combobox"
      :aria-expanded="open"
      autocomplete="off"
      @input="onInput"
      @focus="open = true"
      @keydown="onKeydown"
      @blur="open = false"
    />
    <ul v-if="open && (filtered.length || showAdd)" class="menu" role="listbox">
      <li
        v-for="(o, i) in filtered"
        :key="o"
        role="option"
        :class="{ active: i === active }"
        @mousedown.prevent="set(o)"
      >{{ o }}</li>
      <li
        v-if="showAdd"
        class="add"
        :class="{ active: active === filtered.length }"
        @mousedown.prevent="set(val)"
      >Use “{{ val }}”</li>
    </ul>
  </div>
</template>

<style scoped>
.combo { position: relative; }
.combo input { width: 100%; }
.menu {
  position: absolute; top: calc(100% + 2px); left: 0; right: 0; z-index: 30;
  margin: 0; padding: 4px; list-style: none;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius-sm); box-shadow: 0 6px 20px rgba(0, 0, 0, 0.12);
  max-height: 240px; overflow: auto;
}
.menu li {
  padding: 7px 10px; border-radius: 6px; cursor: pointer; font-size: 0.9rem;
}
.menu li:hover, .menu li.active { background: var(--surface-2); }
.menu li.add { color: var(--muted); }
.menu li.add::before { content: "＋ "; }
</style>
