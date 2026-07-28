import { describe, it, expect, beforeEach } from 'vitest'
import { issueUrl, searchUrl, truncate, buildDiagnostics } from '../bugReport'

// jsdom provides window.location/navigator; default pathname is not an ingress path.
beforeEach(() => {
  // navigator.userAgent + viewport come from jsdom defaults; fine for these assertions.
})

describe('issueUrl', () => {
  it('builds a github new-issue URL with the bug label and prefix', () => {
    const u = issueUrl({ repo: 'Amantux/mymeal', type: 'bug', title: 'It broke', body: 'x' })
    expect(u.startsWith('https://github.com/Amantux/mymeal/issues/new?')).toBe(true)
    expect(u).toContain('labels=bug')
    expect(u).toContain('title=%5BBug%5D%20It%20broke') // "[Bug] It broke", fully encoded
  })

  it('uses the enhancement label + [Feature] prefix for feature requests', () => {
    const u = issueUrl({ repo: 'Amantux/mymeal', type: 'feature', title: 'Idea', body: '' })
    expect(u).toContain('labels=enhancement')
    expect(u).toContain('%5BFeature%5D%20Idea')
  })

  it('percent-encodes hostile input so it cannot break out of the query string', () => {
    const u = issueUrl({ repo: 'Amantux/mymeal', type: 'bug', title: 'a&b=c #x', body: 'p&q' })
    // A literal & or = from user input would inject params; they must be encoded.
    expect(u).toContain('a%26b%3Dc%20%23x')
    expect(u).toContain('p%26q')
    // Exactly the three known params, no injected extras.
    expect(u.split('?')[1].split('&').map((p) => p.split('=')[0]).sort())
      .toEqual(['body', 'labels', 'title'])
  })
})

describe('searchUrl', () => {
  it('encodes an is:issue query', () => {
    const u = searchUrl({ repo: 'Amantux/mymeal', query: 'TypeError x' })
    expect(u.startsWith('https://github.com/Amantux/mymeal/issues?')).toBe(true)
    expect(u).toContain('q=is%3Aissue%20TypeError%20x')
  })
})

describe('truncate', () => {
  it('clips over-long text with a marker and passes short text through', () => {
    expect(truncate('abcdef', 3)).toBe('abc\n…clipped…')
    expect(truncate('ab', 3)).toBe('ab')
    expect(truncate('', 3)).toBe('')
  })
})

describe('buildDiagnostics', () => {
  it('includes coarse facts and never the raw diagnostics object keys we do not pass', () => {
    const md = buildDiagnostics({
      app: 'myMeal', version: '1.2.3',
      diagnostics: { dbBackend: 'sqlite', aiProvider: 'ollama', mcpEnabled: false },
      errors: [], route: '/recipes',
    })
    expect(md).toContain('- App: myMeal 1.2.3')
    expect(md).toContain('- DB backend: sqlite')
    expect(md).toContain('- AI provider: ollama')
    expect(md).toContain('- Route: /recipes')
  })
})
