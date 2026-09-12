import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, streamPost } from './api'

/**
 * Errors thrown by the API wrapper carry the HTTP status.
 *
 * Two callers already branch on it — the chat assistant treats a 404 on undo as
 * "already undone" (ChatAssistant.vue) and Settings distinguishes a 409 — but
 * nothing ever set the field, so both branches were unreachable and a 404 undo
 * surfaced as a failure instead of a success.
 */
function respondWith(status, body) {
  vi.stubGlobal('window', { location: { pathname: '/', hash: '' } })
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {}, removeItem: () => {} })
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: status < 400,
    status,
    statusText: 'Err',
    headers: { get: () => 'application/json' },
    json: async () => body,
    body: null,
  })))
}

afterEach(() => vi.unstubAllGlobals())

describe('api error status', () => {
  it('attaches the status to a thrown error', async () => {
    respondWith(404, { error: 'gone' })
    await expect(api.del('/x')).rejects.toMatchObject({ status: 404 })
  })

  it('still carries the server message', async () => {
    respondWith(409, { error: 'conflict' })
    await expect(api.post('/x', {})).rejects.toThrow('conflict')
  })

  it('attaches the status to a failed stream request too', async () => {
    respondWith(503, { error: 'no provider' })
    await expect(streamPost('/x', {}, () => {})).rejects.toMatchObject({ status: 503 })
  })
})
