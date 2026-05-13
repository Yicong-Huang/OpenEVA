/**
 * Convert the bullet-indented markdown worklog into the flat, Slack-friendly
 * format the user pastes into their status updates.
 *
 * Input example (what we render in Eva):
 *   - Tue Apr 21 standup
 *       - Status:
 *           - Project A:
 *               - [[EX-1001] Fix flaky ...](url) :white_check_mark:
 *           - Tickets:
 *               - [[EX-1002] Cleanup ...](url)
 *       - Meeting Notes:
 *
 * Output (what gets copied to the clipboard):
 *   Status:
 *
 *   Project A:
 *   [EX-1001] Fix flaky ... :white_check_mark:
 *
 *   Tickets:
 *   [EX-1002] Cleanup ...
 *
 *   Meeting Notes:
 */

/** Convert a markdown link `[text](url)` into Slack's native `<url|text>`
 *  mrkdwn syntax so the pasted message stays clickable. Previously this
 *  stripped the URL entirely -- Slack then showed just the title, so the
 *  user had to hunt for the actual PR link elsewhere. The `[\s\S]*?`
 *  non-greedy wildcard handles nested brackets like
 *  `[[EX-123] My PR](https://x)` where the link text itself contains
 *  `[...]`. Leaves plain text untouched. */
function convertLinks(text: string): string {
  return text.replace(/\[([\s\S]*?)\]\(([^)]+)\)/g, '<$2|$1>')
}

/** Parse one line into `{ indent, content }` where indent is the number of
 *  leading 4-space indent levels (0 = top, 1 = Status/Meeting Notes, etc.).
 *  Lines that don't start with `- ` are treated as raw content at level 0. */
function parseLine(line: string): { indent: number; content: string } | null {
  // Match: leading spaces, '- ', rest. 4 spaces == 1 level by our generator.
  const m = line.match(/^(\s*)- (.*)$/)
  if (!m) return null
  const leading = m[1].length
  const content = m[2]
  return { indent: Math.floor(leading / 4), content }
}

export function markdownToSlack(md: string): string {
  const out: string[] = []

  for (const rawLine of md.split('\n')) {
    const line = rawLine.trimEnd()
    if (!line) continue
    const parsed = parseLine(line)
    if (!parsed) {
      // Non-bullet line -- pass through as-is (rare, e.g. blank prefix).
      out.push(convertLinks(line))
      continue
    }
    const { indent, content } = parsed
    const stripped = convertLinks(content).trim()

    // indent 0: the `- <date>` header -- skip; Slack paste anchors on `Status:`.
    if (indent === 0) continue

    // indent 1 (Status: / Meeting Notes:) and indent 2 (project headers):
    // blank-line before each, so sections read cleanly in Slack.
    if (indent === 1 || indent === 2) {
      if (out.length) out.push('')
      out.push(stripped)
      continue
    }

    // indent 3 entries flush-left; indent >=4 sub-bullets get two leading
    // spaces so they still read as continuation of the previous entry.
    out.push(indent === 3 ? stripped : '  ' + stripped)
  }

  return out.join('\n')
}
