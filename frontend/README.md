# myMeal frontend — design system & conventions

Vue 3 + Vite SPA served by the backend. Keep it cohesive: reach for the shared
tokens and components below before inventing a one-off.

## Design tokens (`src/style.css`)

All colour, spacing, radius, shadow, and type values are CSS custom properties
with a light and dark variant. **Never hard-code a hex/px that a token covers.**
Roles: `--accent` (one accent, primary actions + links only), `--surface` /
`--surface-2`, `--border`, `--danger` / `--success`, `--radius` / `--radius-sm`,
`--shadow` / `--shadow-lg`. One radius, two shadow levels.

## Async state — every data view

Use the **`useLoader`** composable (`src/composables/useLoader.js`) so every
screen has the same lifecycle instead of a stuck skeleton on failure:

```js
async function load() { data.value = (await api.get('/thing')).items }
const { loading, error, reload } = useLoader(load)   // runs on mount
```

Template order, always:

```html
<div v-if="loading" class="skeleton" ...></div>       <!-- fixed height, no layout shift -->
<ErrorState v-else-if="error" :message="error" @retry="reload" />
<EmptyState v-else-if="!data.length" ... ><button>primary action</button></EmptyState>
<div v-else> ...content... </div>
```

Re-run `reload()` (not `load()`) after a mutation that should re-fetch, so the
loading/error state stays consistent. Surface mutation failures with
`ui.error(e.message)`.

## Shared components (`src/components/`)

| Component | Use for |
|---|---|
| `EmptyState` | Any empty list/section: icon + title + hint + a primary-action slot. Required for every list view. |
| `ErrorState` | Failed load: message + Try-again (emits `retry`). |
| `Modal` | Any dialog. Focus-trapped, Esc-closes, backdrop-closes, `role="dialog" aria-modal`, restores focus to the opener. Pass `title`; emits `close`. Do NOT hand-roll fixed overlays. |
| `Toasts` | Global; via the `ui` store — `ui.toast(msg)` (auto-dismiss) / `ui.error(msg)` (persists). It's an `aria-live` region. |
| `ChatAssistant` | The ambient FAB assistant (mounted globally). |

## Buttons & forms

- One **primary** (`<button>`) action per view; everything else `.secondary` /
  `.ghost`; destructive uses `.danger`. `.sm` for compact, `.icon-btn` for icon-only.
- **Every icon-only button needs an `aria-label`** (screen-reader name).
- Labels go **above** inputs (`label.field > span`), never placeholder-as-label.
- Destructive/irreversible actions: confirm (or offer undo). Low-risk removes
  (a meal-plan entry, a shopping item) may act immediately.

## Accessibility (target WCAG 2.2 AA)

- Visible keyboard focus is global (`:focus-visible` ring); don't remove it.
- Clickable non-buttons (cards) get `role="button"`, `tabindex="0"`, and
  Enter/Space handlers.
- Respect `prefers-reduced-motion` (handled globally; don't add unconditional
  animation).
- Don't convey meaning by colour alone; give status a label/icon too.

## Responsive

Mobile-first breakpoint at 720px (`@media (max-width: 720px)`): the sidebar
becomes a drawer (hamburger in the topbar), `.page-head` wraps. Verify new views
at 390px — zero horizontal overflow.
