// The chat assistant ends a completed bug-report walkthrough with this marker on its
// own line. The frontend hides it from the bubble and offers to open the Report-a-bug
// modal prefilled with the summary. Isolated + pure so it can be unit-tested — a leaked
// marker would otherwise ride into a public GitHub issue body.
export const BUG_MARKER = '[[REPORT_BUG]]'

// Streaming display: strip any COMPLETE marker, plus a trailing PARTIAL prefix of it
// (e.g. "…text [[REPO" → "…text"), so the marker never flashes character-by-character
// while the reply streams in. Applied to the accumulated raw text on every delta.
export function hideMarker(text) {
  if (!text) return text
  let t = text.split(BUG_MARKER).join('')
  for (let i = BUG_MARKER.length - 1; i > 0; i--) {
    if (t.endsWith(BUG_MARKER.slice(0, i))) { t = t.slice(0, -i); break }
  }
  return t.trimEnd()
}

// Final reply (POST result or stream `done`): the text is complete, so strip only the
// COMPLETE marker (no trailing-partial trim, which could eat a legit trailing bracket).
// Returns { content, summary } — summary is the clean text when a marker was present
// (that's what prefills the bug reporter), else null.
export function finalizeReply(text) {
  if (!text || !text.includes(BUG_MARKER)) return { content: text, summary: null }
  const content = text.split(BUG_MARKER).join('').trimEnd()
  return { content, summary: content }
}
