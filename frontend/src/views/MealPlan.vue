<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { api, apiUrl } from '../api'
import { useUI } from '../stores/ui'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import { useLoader } from '../composables/useLoader'

const ui = useUI()
const entries = ref([])
const recipes = ref([])
const view = ref('week') // 'day' | 'week' | 'month'
const anchor = ref(startOfDay(new Date()))
const busy = ref(false)

// Constraint-aware planning options (feature #6). Sent to /ai/plan.
const showPlanOpts = ref(false)
const planOpts = ref({ maxMinutes: null, exclude: '', servings: null })

function startOfDay(d) {
  const x = new Date(d)
  x.setHours(0, 0, 0, 0)
  return x
}
function mondayOf(d) {
  const x = startOfDay(d)
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7)) // 0 = Monday
  return x
}
function iso(d) {
  // Local calendar date (not UTC) so a day never shifts across the tz boundary.
  const x = new Date(d)
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`
}
function addDays(d, n) {
  const x = new Date(d)
  x.setDate(x.getDate() + n)
  return x
}
const TODAY = iso(new Date())
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

// Visible date range per view (month spans whole weeks for a clean grid).
const range = computed(() => {
  if (view.value === 'day') return { start: anchor.value, end: anchor.value }
  if (view.value === 'month') {
    const first = new Date(anchor.value.getFullYear(), anchor.value.getMonth(), 1)
    const last = new Date(anchor.value.getFullYear(), anchor.value.getMonth() + 1, 0)
    return { start: mondayOf(first), end: addDays(mondayOf(last), 6) }
  }
  const s = mondayOf(anchor.value)
  return { start: s, end: addDays(s, 6) }
})

// Day cards for day/week (same markup, different container).
const dayList = computed(() => {
  const n = view.value === 'day' ? 1 : 7
  const start = view.value === 'day' ? anchor.value : mondayOf(anchor.value)
  return Array.from({ length: n }, (_, i) => {
    const d = addDays(start, i)
    return {
      date: iso(d),
      label: d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }),
    }
  })
})

// Compact cells for the month calendar.
const monthCells = computed(() => {
  const out = []
  const m = anchor.value.getMonth()
  for (let d = new Date(range.value.start); iso(d) <= iso(range.value.end); d = addDays(d, 1)) {
    out.push({ date: iso(d), num: d.getDate(), inMonth: d.getMonth() === m, isToday: iso(d) === TODAY })
  }
  return out
})

const title = computed(() => {
  if (view.value === 'day') {
    return anchor.value.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })
  }
  if (view.value === 'month') {
    return anchor.value.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
  }
  return `Week of ${iso(mondayOf(anchor.value))}`
})

// null = not yet known. Tells the two empty states apart: a household that has
// never planned anything needs the concept explained; one that has just paged to
// a quiet week only needs the way back in.
const everPlanned = ref(null)

async function load() {
  const [plan, recs] = await Promise.all([
    api.get(`/mealplans?start=${iso(range.value.start)}&end=${iso(range.value.end)}`),
    api.get('/recipes'),
  ])
  entries.value = plan.items
  recipes.value = recs.items
  if (plan.items.length) {
    // Anything in view proves they have planned before — free, no extra call.
    everPlanned.value = true
  } else if (everPlanned.value === null) {
    // Only when the view is genuinely empty and we don't already know. `limit=1`
    // because the question is "has anything EVER been planned?" — the undated
    // call returns the household's entire history to answer a yes/no.
    //
    // Outside the loader's try: this only chooses WHICH empty state to show, so
    // a failed probe must not turn a perfectly-loaded plan into an error screen.
    // Unknown falls back to the gentler range-empty copy.
    try {
      const any = await api.get('/mealplans?limit=1')
      everPlanned.value = any.items.length > 0
    } catch (e) {
      everPlanned.value = true
    }
  }
}
const { loading, error, reload } = useLoader(load)
watch([view, anchor], reload)

function shift(dir) {
  if (view.value === 'day') anchor.value = addDays(anchor.value, dir)
  else if (view.value === 'week') anchor.value = addDays(anchor.value, dir * 7)
  else {
    const a = new Date(anchor.value)
    a.setDate(1)
    a.setMonth(a.getMonth() + dir)
    anchor.value = a
  }
}
function openDay(date) {
  anchor.value = startOfDay(new Date(`${date}T00:00:00`))
  view.value = 'day'
}

function entriesFor(date) {
  return entries.value.filter((e) => e.date && e.date.slice(0, 10) === date)
}

const adding = ref(null)
const form = ref({ mealType: 'dinner', recipeId: '', title: '' })

// The empty state's CTA. Drops the reader straight onto a day with the add form
// already open, rather than back at the grid to hunt for "＋ Add meal".
function startFirstMeal() {
  const days = dayList.value
  adding.value = days.some((d) => d.date === TODAY) ? TODAY : days[0].date
}

async function addEntry(date) {
  try {
    await api.post('/mealplans', { date, ...form.value })
    everPlanned.value = true
    form.value = { mealType: 'dinner', recipeId: '', title: '' }
    adding.value = null
    await reload()
  } catch (e) {
    ui.error(e.message)
  }
}
async function del(id) {
  try {
    await api.del(`/mealplans/${id}`)
    await reload()
  } catch (e) {
    ui.error(e.message)
  }
}
async function generate() {
  busy.value = true
  try {
    const body = { start: iso(mondayOf(anchor.value)), days: 7 }
    if (planOpts.value.maxMinutes) body.maxMinutes = planOpts.value.maxMinutes
    if (planOpts.value.servings) body.servings = planOpts.value.servings
    const exclude = planOpts.value.exclude
      .split(',').map((s) => s.trim()).filter(Boolean)
    if (exclude.length) body.exclude = exclude
    await api.post('/ai/plan', body)
    showPlanOpts.value = false
    ui.toast('Week planned')
    await reload()
  } catch (e) {
    ui.error(e.message)
  } finally {
    busy.value = false
  }
}
// --- Calendar subscription ---------------------------------------------
// A feed URL, not a download: calendar apps and Home Assistant poll it, so the
// plan stays live wherever the household already looks.
const calToken = ref(null)
const calLoading = ref(true)
const calBusy = ref('')          // '' | 'publish' | 'rotate' | 'stop'
// Which destructive action is awaiting confirmation: '' | 'rotate' | 'stop'.
// BOTH are confirmed, and that symmetry is the point — turning the feed off is
// strictly more destructive than replacing the link (the link can never come
// back), so guarding only the replace would put the seatbelt on the safer one.
const calConfirm = ref('')

// Behind HA ingress the browser's URL is a random, session-scoped
// /api/hassio_ingress/<token>/ path that only an authenticated HA session can
// use — a calendar app fetching it gets a login page, not an .ics. So we must
// NOT hand the user that URL. We detect ingress and show the direct host:port
// form instead, naming the prerequisite (mapping port 7850 in the add-on's
// Network tab, which is null by default).
const underIngress = computed(() => window.location.pathname.includes('/api/hassio_ingress/'))
const feedPath = computed(() => (calToken.value ? `/calendar/${calToken.value}.ics` : ''))
const feedUrl = computed(() => {
  if (!calToken.value) return ''
  if (underIngress.value) return `http://<home-assistant-host>:7850/api/v1${feedPath.value}`
  return new URL(apiUrl(feedPath.value), window.location.href).href
})

const calError = ref('')
// The URL is a bearer credential on a page people open daily, and meal-plan
// screenshots are a routine support artifact — so it is masked until asked for,
// matching how the Settings page treats API keys. Copy still works while
// masked, and publishing or replacing reveals it, because that is the one
// moment the user actually needs to read it.
const calRevealed = ref(false)
const maskedUrl = computed(() =>
  calToken.value ? feedUrl.value.replace(calToken.value, '•'.repeat(24)) : '')

async function loadSubscription() {
  calLoading.value = true
  calError.value = ''
  try {
    calToken.value = (await api.get('/calendar/subscription')).token
  } catch (e) {
    // This card must be able to show its own error rather than sitting on a
    // permanent skeleton — it loads independently of the plan grid.
    calError.value = e.message || 'Could not load the calendar feed settings.'
  } finally {
    calLoading.value = false
  }
}
onMounted(loadSubscription)

async function publishFeed() {
  calBusy.value = 'publish'
  try {
    calToken.value = (await api.post('/calendar/subscription')).token
    calRevealed.value = true   // they need to read it right now
    ui.toast('Calendar feed published')
  } catch (e) {
    ui.error(e.message || 'Could not publish the feed')
  } finally {
    calBusy.value = ''
  }
}
async function rotateFeed() {
  calBusy.value = 'rotate'
  try {
    calToken.value = (await api.post('/calendar/subscription/rotate')).token
    calConfirm.value = ''
    calRevealed.value = true   // the new link has to be handed out
    ui.toast('New link generated — the old one no longer works')
  } catch (e) {
    ui.error(e.message || 'Could not generate a new link')
  } finally {
    calBusy.value = ''
  }
}
async function stopFeed() {
  calBusy.value = 'stop'
  try {
    await api.del('/calendar/subscription')
    calToken.value = null
    calConfirm.value = ''
    calRevealed.value = false
    ui.toast('Calendar feed turned off')
  } catch (e) {
    ui.error(e.message || 'Could not turn off the feed')
  } finally {
    calBusy.value = ''
  }
}
function copyFeed() {
  // navigator.clipboard is undefined on an insecure origin — which is exactly
  // where this feature sends people (http://<ha-host>:7850). Optional chaining
  // alone made the whole expression short-circuit to undefined: no copy, no
  // toast, no error, nothing. Say so instead, and the masked field is still
  // selectable because the underlying value is the real URL.
  if (!navigator.clipboard?.writeText) {
    ui.error('Copying needs a secure (https) connection here — select the link and copy it manually.')
    calRevealed.value = true
    return
  }
  navigator.clipboard.writeText(feedUrl.value).then(
    () => ui.toast('Link copied'),
    () => {
      calRevealed.value = true
      ui.error('Could not copy — select the link and copy it manually.')
    },
  )
}

async function buildList() {
  try {
    const sl = await api.post('/shopping-lists', { name: `Plan ${iso(range.value.start)}` })
    const res = await api.post(`/shopping-lists/${sl.id}/from-mealplan`, {
      start: iso(range.value.start),
      end: iso(range.value.end),
    })
    ui.toast(`Shopping list created (${res.added} items)`)
  } catch (e) {
    ui.error(e.message)
  }
}
</script>

<template>
  <div class="page-head">
    <h1>Meal plan</h1>
    <div class="grow"></div>
    <button class="secondary" :class="{ active: showPlanOpts }"
      :aria-pressed="showPlanOpts" @click="showPlanOpts = !showPlanOpts"
      title="Planning constraints">⚙️ Options</button>
    <button class="secondary" @click="generate" :disabled="busy">
      {{ busy ? 'Planning…' : '✨ Plan with AI' }}
    </button>
    <!-- With nothing planned there is nothing to build a list FROM, so the
         accent moves to the empty state's CTA and this drops to secondary.
         One primary per view, and it points at what actually helps. -->
    <button :class="{ secondary: !entries.length }" @click="buildList">🛒 Build shopping list</button>
  </div>

  <div v-if="showPlanOpts" class="card plan-opts">
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr))">
      <label class="field">
        <span>Max time per meal (min)</span>
        <input v-model.number="planOpts.maxMinutes" type="number" min="0" placeholder="any" />
      </label>
      <label class="field">
        <span>Servings</span>
        <input v-model.number="planOpts.servings" type="number" min="0" placeholder="default" />
      </label>
      <label class="field">
        <span>Exclude ingredients</span>
        <input v-model="planOpts.exclude" placeholder="e.g. mushrooms, cilantro" />
      </label>
    </div>
    <p class="muted" style="margin:8px 0 0;font-size:0.82rem">
      Your saved diet &amp; allergies (Settings) are always applied on top of these.
    </p>
  </div>

  <div class="toolbar">
    <div class="seg" role="tablist" aria-label="Calendar view">
      <button
        v-for="v in ['day', 'week', 'month']"
        :key="v"
        role="tab"
        :aria-selected="view === v"
        :class="{ active: view === v }"
        @click="view = v"
      >{{ v[0].toUpperCase() + v.slice(1) }}</button>
    </div>
    <div class="grow"></div>
    <div class="mp-nav">
      <button class="secondary sm" :aria-label="`Previous ${view}`" @click="shift(-1)">←</button>
      <strong class="mp-title">{{ title }}</strong>
      <button class="secondary sm" :aria-label="`Next ${view}`" @click="shift(1)">→</button>
    </div>
  </div>

  <div v-if="loading" class="mp-week">
    <div v-for="n in (view === 'day' ? 1 : 7)" :key="n" class="skeleton" style="height:150px"></div>
  </div>

  <ErrorState v-else-if="error" :message="error" @retry="reload" />

  <!-- Month: compact calendar; click a day to plan it. -->
  <template v-else-if="view === 'month'">
    <div class="mp-weekhead"><span v-for="w in WEEKDAYS" :key="w">{{ w }}</span></div>
    <div class="mp-month">
      <div
        v-for="c in monthCells"
        :key="c.date"
        class="mp-cell"
        :class="{ dim: !c.inMonth, today: c.isToday }"
        role="button"
        tabindex="0"
        :aria-label="`Plan ${c.date}`"
        @click="openDay(c.date)"
        @keydown.enter="openDay(c.date)"
        @keydown.space.prevent="openDay(c.date)"
      >
        <div class="num">{{ c.num }}</div>
        <div v-for="e in entriesFor(c.date)" :key="e.id" class="chip-sm">
          {{ e.recipe ? e.recipe.name : e.title }}
        </div>
      </div>
    </div>
  </template>

  <!-- Nothing planned in view. Two different messages, because "you have never
       done this" and "this particular week is quiet" need different help. The
       month view keeps its calendar instead — the grid of dates IS the content
       there, and clicking a day is how you plan from it. -->
  <EmptyState
    v-else-if="!entries.length && adding === null"
    icon="🗓️"
    :title="everPlanned === false ? 'Plan your week' : `Nothing planned for this ${view}`"
    :hint="everPlanned === false
      ? 'A meal plan is a calendar of what you\'ll cook. Put a recipe on a day, fill in as much of the week as you like, then build one shopping list from the lot.'
      : 'Add a meal to any day, or let AI fill the week from your recipes.'"
  >
    <button @click="startFirstMeal">
      {{ everPlanned === false ? '＋ Plan your first meal' : '＋ Add a meal' }}
    </button>
  </EmptyState>

  <!-- Day (single, focused) / Week (7 equal columns) share the day-card markup. -->
  <div v-else :class="view === 'day' ? 'mp-day' : 'mp-week'">
    <div v-for="d in dayList" :key="d.date" class="card mp-daycard">
      <h3>{{ d.label }}</h3>
      <div v-for="e in entriesFor(d.date)" :key="e.id" class="row" style="margin-bottom:6px">
        <span class="fill">
          <span class="badge">{{ e.mealType }}</span>
          {{ e.recipe ? e.recipe.name : e.title }}
        </span>
        <button class="ghost sm danger" :aria-label="`Remove ${e.recipe ? e.recipe.name : e.title}`" @click="del(e.id)">✕</button>
      </div>

      <template v-if="adding === d.date">
        <select v-model="form.mealType" style="margin-bottom:6px">
          <option>breakfast</option><option>lunch</option>
          <option>dinner</option><option>snack</option>
        </select>
        <select v-model="form.recipeId" style="margin-bottom:6px">
          <option value="">— free text —</option>
          <option v-for="r in recipes" :key="r.id" :value="r.id">{{ r.name }}</option>
        </select>
        <input v-if="!form.recipeId" v-model="form.title" placeholder="Meal" style="margin-bottom:6px" />
        <div class="row">
          <button class="sm" @click="addEntry(d.date)">Add</button>
          <button class="secondary sm" @click="adding = null">Cancel</button>
        </div>
      </template>
      <button v-else class="ghost sm" style="margin-top:4px" @click="adding = d.date">＋ Add meal</button>
    </div>
  </div>

  <!-- Calendar subscription. An inline card at the foot of the page rather than
       a dialog off the page head, matching "Share & export" on RecipeDetail —
       it is the same mental model (mint a secret link, copy it, revoke it) and
       the app should not have two patterns for that. It also keeps the page
       head at one primary action, which a fourth button broke on a phone. -->
  <div class="card mp-subscribe">
    <h2>📅 Subscribe in your calendar</h2>

    <div v-if="calLoading" class="skeleton mp-sub-skeleton"></div>

    <p v-else-if="calError" class="mp-sub-error">
      {{ calError }}
      <button class="ghost sm" @click="loadSubscription">Retry</button>
    </p>

    <!-- First run: teach what the feed IS before asking for the action. -->
    <template v-else-if="!calToken">
      <p class="mp-sub-lead">
        Publish a private link to this meal plan and your calendar app keeps it in
        sync — planned meals appear as all-day events in Apple&nbsp;Calendar,
        Google&nbsp;Calendar or Home&nbsp;Assistant, updating whenever you change
        the plan.
      </p>
      <p class="muted mp-sub-note">
        Anyone with the link can read your meal plan, so treat it like a password.
        You can replace it at any time.
      </p>
      <!-- secondary, not accent: the page's one accent fill stays on "Build
           shopping list" (same call RecipeDetail makes for "Create public link"). -->
      <button class="secondary" @click="publishFeed" :disabled="calBusy === 'publish'">
        {{ calBusy === 'publish' ? 'Publishing…' : '🔗 Publish calendar link' }}
      </button>
    </template>

    <template v-else>
      <p v-if="underIngress" class="mp-sub-lead">
        You're viewing myMeal through Home&nbsp;Assistant, and that address only
        works while you're signed in to HA — a calendar app can't use it. Use the
        link below with <strong>your Home&nbsp;Assistant machine's address</strong>
        in place of <code>&lt;home-assistant-host&gt;</code>, and first map
        <strong>7850/tcp</strong> in this add-on's Network tab (it's off by default).
      </p>
      <p v-else class="mp-sub-lead">
        Add this link in your calendar app as a subscribed calendar, or in
        Home&nbsp;Assistant via the Remote&nbsp;Calendar integration. Anyone with
        it can read your meal plan.
      </p>

      <div class="mp-sub-labelrow">
        <span class="field-label" id="mp-feed-label">Calendar feed link</span>
        <button class="ghost sm" :aria-pressed="calRevealed"
          @click="calRevealed = !calRevealed">
          {{ calRevealed ? 'Hide' : 'Show link' }}
        </button>
      </div>
      <div class="row mp-sub-row">
        <!-- A wrapping textarea, not an input: the token is the only part of
             this URL that carries information and a single line hides it at
             every width — which also means a replaced link looks identical to
             the old one. -->
        <!-- 3 rows, not 2: the ingress variant's URL carries a placeholder host
             AND the token, and clipping the token is the one thing this field
             exists to prevent. -->
        <textarea class="fill mp-sub-url" rows="3" readonly aria-labelledby="mp-feed-label"
          :value="calRevealed ? feedUrl : maskedUrl"
          @focus="calRevealed && $event.target.select()"></textarea>
        <button class="secondary" @click="copyFeed">Copy</button>
      </div>

      <div v-if="!calConfirm" class="row mp-sub-actions">
        <button class="secondary sm" @click="calConfirm = 'rotate'">↻ Replace link</button>
        <button class="ghost sm danger" @click="calConfirm = 'stop'">Stop sharing</button>
      </div>

      <!-- Both destructive actions confirm, and each names its own consequence. -->
      <div v-else class="mp-sub-confirm">
        <p v-if="calConfirm === 'rotate'">
          Replace this link? The current link stops working immediately, and every
          calendar already subscribed to it stops updating until you give them the
          new one.
        </p>
        <p v-else>
          Stop sharing this plan? The link stops working immediately and cannot be
          brought back — every subscribed calendar stops updating, and publishing
          again gives you a different link to hand out.
        </p>
        <div class="row mp-sub-actions">
          <button v-if="calConfirm === 'rotate'" class="secondary danger sm"
            @click="rotateFeed" :disabled="calBusy === 'rotate'">
            {{ calBusy === 'rotate' ? 'Replacing…' : 'Replace link' }}
          </button>
          <button v-else class="secondary danger sm" @click="stopFeed"
            :disabled="calBusy === 'stop'">
            {{ calBusy === 'stop' ? 'Stopping…' : 'Stop sharing' }}
          </button>
          <button class="secondary sm" @click="calConfirm = ''">Cancel</button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* Calendar subscription card. Separated from the plan grid above it by the
   card's own top margin; everything inside uses the 4/8/12/16 spacing scale. */
.mp-subscribe { margin-top: 24px; max-width: 640px; }
.mp-subscribe h2 { margin: 0 0 8px; }
/* The lead is instructional content, not a caption — it reads at body colour;
   only the secondary caveat is muted. */
.mp-sub-lead { margin: 0 0 12px; font-size: 0.9rem; }
.mp-sub-note { margin: 0 0 16px; font-size: 0.82rem; }
/* Skeleton shaped like the taller (first-run) body, so the card doesn't jump. */
.mp-sub-skeleton { height: 160px; }
.mp-sub-error { display: flex; align-items: center; gap: 8px; margin: 0; color: var(--danger); }
/* Matches label.field > span in style.css; used as a standalone label because
   the control it names sits beside a button, and wrapping a button in a <label>
   proxies its clicks to the field. */
.field-label {
  display: block; font-size: 0.8rem; font-weight: 600;
  color: var(--muted); margin-bottom: 5px;
}
.mp-sub-labelrow { display: flex; align-items: baseline; gap: 8px; }
.mp-sub-labelrow .field-label { margin-bottom: 4px; }
.mp-sub-row { align-items: flex-start; gap: 8px; }
/* Wraps rather than truncates: the token is the informative part of the URL. */
.mp-sub-url { resize: vertical; font-size: 0.82rem; line-height: 1.4; }
/* On a phone the field is ~32 characters wide, so the URL needs 4–5 lines and
   the ingress variant (placeholder host + token) needs more still. `rows` is a
   character-count hint that can't know the width — clipping the token is the
   one failure this field exists to prevent, so give it room here. */
@media (max-width: 560px) {
  .mp-sub-url { min-height: 7em; }
}
.mp-sub-actions { gap: 8px; flex-wrap: wrap; margin-top: 12px; }
/* A bordered block, not a nested .card — a card inside a card is the heaviest
   box on the page and it is only a confirmation. */
.mp-sub-confirm {
  margin-top: 12px; padding: 12px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.mp-sub-confirm p { margin: 0; font-size: 0.88rem; }
.mp-sub-confirm .mp-sub-actions { margin-top: 12px; }

/* .seg (segmented view toggle) is the shared control in style.css. */
.mp-nav { display: flex; align-items: center; gap: 8px; }
.mp-title { min-width: 8ch; text-align: center; }
.plan-opts { margin-bottom: 16px; }
/* Pressed toggle: accent text + border only — the sole orange FILL stays on the
   view's primary action (Build shopping list), keeping the accent scarce. */
button.secondary.active { color: var(--accent-text); border-color: var(--accent); }

/* Week: 7 equal-width day columns (no uneven/stretched box). Day: one focused card. */
/* stretch (default) → every day box is identical width AND height, regardless of
   how many meals a day has (no "first/today box is larger"). */
.mp-week { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 12px; }
/* The global `.card + .card { margin-top }` (for STACKED cards) leaks into the
   grid: cards 2–7 are adjacent-sibling .cards and get a top margin the first
   card doesn't — which made the first box look larger/misaligned. Neutralize it. */
.mp-week .card + .card { margin-top: 0; }
.mp-day { max-width: 620px; }
.mp-daycard { padding: 14px; }
.mp-daycard h3 { margin-bottom: 8px; }

/* Month calendar. */
.mp-weekhead, .mp-month { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 6px; }
.mp-weekhead { margin-bottom: 6px; }
.mp-weekhead span { text-align: center; font-size: 0.75rem; font-weight: 600; color: var(--muted); }
.mp-cell {
  min-height: 92px; padding: 6px; background: var(--surface);
  border: 1px solid var(--border); border-radius: var(--radius-sm); cursor: pointer;
  transition: border-color 0.12s;
}
.mp-cell:hover { border-color: var(--accent); }
.mp-cell.dim { opacity: 0.45; }
.mp-cell.today { border-color: var(--accent); }
.mp-cell .num { font-size: 0.8rem; font-weight: 650; margin-bottom: 2px; }
.mp-cell .chip-sm {
  font-size: 0.68rem; background: var(--accent-soft); color: var(--accent-text);
  border-radius: 4px; padding: 1px 4px; margin-top: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

/* Stack the week into a single column on small screens (still uniform boxes). */
@media (max-width: 900px) {
  .mp-week { grid-template-columns: 1fr; }
}
</style>
