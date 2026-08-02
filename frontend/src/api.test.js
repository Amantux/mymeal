import { afterEach, describe, expect, it, vi } from 'vitest'

import { mediaUrl } from './api'

/**
 * Recipe thumbnails were blank in the list and dashboard views while the detail
 * page's hero image worked.
 *
 * Serializers return root-absolute media paths ("/api/v1/recipes/<id>/image").
 * That is correct at "/" and wrong behind Home Assistant ingress, where the app
 * is served from "/api/hassio_ingress/<token>/" — the browser asked the HA root,
 * which is not this add-on, and got a 404. The detail page happened to build its
 * URL with apiUrl() and so was unaffected; the two list views bound the raw
 * field straight into :src.
 */
function atPath(pathname) {
  vi.stubGlobal('window', { location: { pathname } })
}

afterEach(() => vi.unstubAllGlobals())

describe('mediaUrl', () => {
  it('leaves a served path usable when the app is at the root', () => {
    atPath('/')
    expect(mediaUrl('/api/v1/recipes/abc/image')).toBe('/api/v1/recipes/abc/image')
  })

  it('rebases a served path onto the Home Assistant ingress prefix', () => {
    atPath('/api/hassio_ingress/TOKEN/')
    expect(mediaUrl('/api/v1/recipes/abc/image'))
      .toBe('/api/hassio_ingress/TOKEN/api/v1/recipes/abc/image')
  })

  it('works from a sub-page, not just the ingress root', () => {
    atPath('/api/hassio_ingress/TOKEN/index.html')
    expect(mediaUrl('/api/v1/recipes/abc/image'))
      .toBe('/api/hassio_ingress/TOKEN/api/v1/recipes/abc/image')
  })

  it('accepts a path that has no /api/v1 prefix', () => {
    atPath('/api/hassio_ingress/TOKEN/')
    expect(mediaUrl('/recipes/abc/image'))
      .toBe('/api/hassio_ingress/TOKEN/api/v1/recipes/abc/image')
  })

  it('passes an absolute external URL through untouched', () => {
    atPath('/api/hassio_ingress/TOKEN/')
    expect(mediaUrl('https://cdn.example/x.jpg')).toBe('https://cdn.example/x.jpg')
  })

  it('returns null for a missing image rather than a broken src', () => {
    atPath('/')
    expect(mediaUrl(null)).toBe(null)
    expect(mediaUrl('')).toBe(null)
  })
})
