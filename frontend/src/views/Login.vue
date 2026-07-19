<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../stores/auth'
import { useUI } from '../stores/ui'

const router = useRouter()
const auth = useAuth()
const ui = useUI()

const mode = ref('login')
const name = ref('')
const email = ref('')
const password = ref('')
const busy = ref(false)

async function submit() {
  busy.value = true
  try {
    if (mode.value === 'login') {
      await auth.login(email.value, password.value)
    } else {
      await auth.register({ name: name.value, email: email.value, password: password.value })
    }
    router.push('/')
  } catch (e) {
    ui.error(e.message || 'Sign in failed')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="center-screen">
    <div class="card auth-card">
      <div class="brand" style="display:flex;align-items:center;gap:10px;font-weight:800;font-size:1.4rem;margin-bottom:6px">
        <span class="logo" style="width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,var(--accent),#f59e0b);display:grid;place-items:center;font-size:18px">🍽️</span>
        myMeal
      </div>
      <p class="muted" style="margin-top:0;margin-bottom:20px">
        {{ mode === 'login' ? 'Sign in to your kitchen.' : 'Create your kitchen.' }}
      </p>
      <form @submit.prevent="submit">
        <label v-if="mode === 'register'" class="field">
          <span>Name</span>
          <input v-model="name" autocomplete="name" />
        </label>
        <label class="field">
          <span>Email</span>
          <input v-model="email" type="email" autocomplete="email" required />
        </label>
        <label class="field">
          <span>Password</span>
          <input v-model="password" type="password" autocomplete="current-password" required />
        </label>
        <button type="submit" :disabled="busy" style="width:100%;justify-content:center">
          {{ busy ? 'Please wait…' : (mode === 'login' ? 'Sign in' : 'Create account') }}
        </button>
      </form>
      <p class="muted" style="text-align:center;margin-bottom:0;margin-top:16px;font-size:0.88rem">
        <template v-if="mode === 'login'">
          No account?
          <a href="#" @click.prevent="mode = 'register'">Create one</a>
        </template>
        <template v-else>
          Already have an account?
          <a href="#" @click.prevent="mode = 'login'">Sign in</a>
        </template>
      </p>
    </div>
  </div>
</template>
