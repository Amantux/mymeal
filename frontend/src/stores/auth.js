import { defineStore } from 'pinia'
import { api, setToken } from '../api'

export const useAuth = defineStore('auth', {
  state: () => ({
    user: null,
    authDisabled: false,
    allowRegistration: true,
    ready: false,
  }),
  getters: {
    isAuthed: (s) => !!s.user || s.authDisabled,
  },
  actions: {
    async bootstrap() {
      // Ask the server directly whether sign-in is required. We used to INFER
      // this from an unauthenticated /users/self succeeding, but any transient
      // failure of that call then bounced the user to a login screen — which is
      // meaningless behind Home Assistant ingress, where the add-on runs with
      // disable_auth and HA has already authenticated the user upstream.
      try {
        const mode = await api.get('/misc/auth-mode')
        this.authDisabled = !!mode.authDisabled
        this.allowRegistration = !!mode.allowRegistration
      } catch (e) {
        // Unknown mode: fall back to requiring login (fail closed, never open).
        this.authDisabled = false
      }
      try {
        const res = await api.get('/users/self')
        this.user = res.item
      } catch (e) {
        this.user = null
      } finally {
        this.ready = true
      }
    },
    async login(email, password) {
      const res = await api.post('/users/login', { username: email, password })
      setToken(res.token)
      const self = await api.get('/users/self')
      this.user = self.item
    },
    async register(payload) {
      await api.post('/users/register', payload)
      await this.login(payload.email, payload.password)
    },
    logout() {
      setToken(null)
      this.user = null
      location.hash = '#/login'
    },
  },
})
