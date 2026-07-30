import { describe, it, expect } from 'vitest'
import { hideMarker, finalizeReply, BUG_MARKER } from './bugMarker'

describe('bugMarker', () => {
  it('finalizeReply strips a complete marker and exposes the summary', () => {
    const { content, summary } = finalizeReply(`Fix the widget\n${BUG_MARKER}`)
    expect(content).toBe('Fix the widget')
    expect(summary).toBe('Fix the widget')
  })

  it('finalizeReply leaves a normal reply untouched with no summary', () => {
    const { content, summary } = finalizeReply('normal reply')
    expect(content).toBe('normal reply')
    expect(summary).toBeNull()
  })

  it('hideMarker hides a trailing PARTIAL marker mid-stream', () => {
    expect(hideMarker('x [[REPO')).toBe('x')
    expect(hideMarker('some text [[')).toBe('some text')
  })

  it('hideMarker hides a complete marker', () => {
    expect(hideMarker(`done ${BUG_MARKER}`)).toBe('done')
  })

  it('hideMarker leaves ordinary text (incl. lone bracket words) alone', () => {
    expect(hideMarker('just chatting')).toBe('just chatting')
  })
})
