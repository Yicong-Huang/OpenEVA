/**
 * Pure color-mapping helpers for the Tickets page.
 *
 * JIRA statuses and priorities vary across projects (custom
 * workflows, internal vs cloud naming, P0/P1 vs Highest/Critical),
 * so the matchers are deliberately substring-based rather than
 * exact-equality. Both helpers return CSS variable names so the
 * resulting chips honour every theme without hardcoded RGB values.
 */
export interface ChipColors {
  bg: string
  fg: string
}


/** JIRA priority -> tag colour. Substring match because the strings
 * vary slightly by deployment ('Highest' / 'P0' / 'Critical', etc.). */
export function colorForPriority(priority: string): ChipColors {
  const p = (priority || '').toLowerCase()
  if (p.includes('highest') || p.includes('critical')
      || p.includes('p0') || p.includes('p1')) {
    return { bg: 'rgba(239,68,68,0.18)', fg: 'var(--red)' }
  }
  if (p.includes('high') || p.includes('p2')) {
    return { bg: 'rgba(249,115,22,0.18)', fg: 'var(--orange)' }
  }
  if (p.includes('medium') || p.includes('p3')) {
    return { bg: 'rgba(245,158,11,0.18)', fg: 'var(--yellow)' }
  }
  if (p.includes('low') || p.includes('p4') || p.includes('p5')) {
    return { bg: 'rgba(168,162,158,0.18)', fg: 'var(--text-dim)' }
  }
  return { bg: 'var(--panel-bg)', fg: 'var(--text-dim)' }
}


/** JIRA issue type -> tag colour. Each type gets a distinct hue so a
 * row's "what kind of work is this" reads at a glance:
 *
 *   Bug / Defect          -> red    (something broken, fix-and-go)
 *   Story / New feature   -> blue   (user-visible value)
 *   Task                  -> indigo (generic engineering work)
 *   Improvement / Tech    -> teal   (incremental polish / refactor)
 *   Epic                  -> purple (parent of stories)
 *   Sub-task              -> grey   (under-the-hood child)
 *
 * Substring-based to handle JIRA installs that rename the defaults
 * ("Defect" instead of "Bug", "Spike" mapped to story, etc.). */
export function colorForIssueType(issueType: string): ChipColors {
  const t = (issueType || '').toLowerCase()
  // Order matters: most specific real-world types first. The matchers
  // cover the types actually seen on enterprise/Apache JIRA (Test
  // Failure, Pin, Incident, Release Sign-off, Advanced Support, ...) so
  // a scan down the queue reads as distinct colour bands rather than a
  // wall of identical grey.
  if (t.includes('incident') || t.includes('outage')
      || t.includes('breakage')) {
    return { bg: 'rgba(239,68,68,0.20)', fg: 'var(--red)' }       // red
  }
  if (t.includes('bug') || t.includes('defect')) {
    return { bg: 'rgba(244,63,94,0.18)', fg: '#fb7185' }          // rose
  }
  if (t.includes('test') || t.includes('flak') || t.includes('failure')) {
    return { bg: 'rgba(249,115,22,0.20)', fg: 'var(--orange)' }   // amber
  }
  if (t === 'pin' || t.includes('pin')) {
    return { bg: 'rgba(14,165,233,0.20)', fg: '#38bdf8' }         // sky
  }
  if (t.includes('release') || t.includes('sign')) {
    return { bg: 'rgba(236,72,153,0.18)', fg: '#f472b6' }         // pink
  }
  if (t.includes('support')) {
    return { bg: 'rgba(20,184,166,0.18)', fg: 'var(--teal, #14b8a6)' } // teal
  }
  if (t.includes('story') || t.includes('feature') || t.includes('spike')) {
    return { bg: 'rgba(59,130,246,0.18)', fg: 'var(--blue)' }     // blue
  }
  if (t.includes('improv') || t.includes('tech')
      || t.includes('refactor') || t.includes('debt')) {
    return { bg: 'rgba(34,197,94,0.16)', fg: 'var(--green)' }     // green
  }
  if (t.includes('epic')) {
    return { bg: 'rgba(168,85,247,0.18)', fg: 'var(--purple)' }   // purple
  }
  if (t.includes('sub')) {
    return { bg: 'rgba(168,162,158,0.18)', fg: 'var(--text-dim)' } // grey
  }
  if (t.includes('task')) {
    return { bg: 'rgba(99,102,241,0.18)', fg: 'var(--accent)' }   // indigo
  }
  return { bg: 'var(--panel-bg)', fg: 'var(--text-dim)' }
}


/** JIRA Severity -> tag colour. Severity is the real triage dimension
 * on enterprise JIRA (priority there is uniformly "Major"). The
 * scale runs Sev1 (most severe) -> Sev4/5 (least). Substring + digit
 * match so "Sev. 1" / "Sev1" / "Severity 1" / "S1" all land on the
 * same colour. Highest severities get the loudest colour. */
export function colorForSeverity(severity: string): ChipColors {
  const s = (severity || '').toLowerCase()
  const m = s.match(/(\d)/)
  const n = m ? Number(m[1]) : NaN
  if (n === 0 || n === 1 || s.includes('critical') || s.includes('blocker')) {
    return { bg: 'rgba(239,68,68,0.18)', fg: 'var(--red)' }
  }
  if (n === 2 || s.includes('major') || s.includes('high')) {
    return { bg: 'rgba(249,115,22,0.18)', fg: 'var(--orange)' }
  }
  if (n === 3 || s.includes('moderate') || s.includes('medium')) {
    return { bg: 'rgba(245,158,11,0.18)', fg: 'var(--yellow)' }
  }
  if (n >= 4 || s.includes('minor') || s.includes('low')) {
    return { bg: 'rgba(168,162,158,0.18)', fg: 'var(--text-dim)' }
  }
  return { bg: 'var(--panel-bg)', fg: 'var(--text-dim)' }
}


/** Shorten a Severity string for the narrow queue chip. "Sev. 2" /
 * "Severity 2" -> "SEV2"; anything without a digit passes through
 * upper-cased. */
export function shortSeverity(severity: string): string {
  const m = (severity || '').match(/(\d)/)
  if (m) return `SEV${m[1]}`
  return (severity || '').toUpperCase()
}


/** JIRA status -> tag colour. Same substring strategy because every
 * project can rename its workflow states. The `block` arm is for the
 * "Blocked" / "Blocker" custom states some projects use. */
export function colorForStatus(status: string): ChipColors {
  const s = (status || '').toLowerCase()
  if (s.includes('done') || s.includes('closed')
      || s.includes('resolved')) {
    return { bg: 'rgba(34,197,94,0.18)', fg: 'var(--green)' }
  }
  if (s.includes('progress') || s.includes('review')) {
    return { bg: 'rgba(99,102,241,0.18)', fg: 'var(--accent)' }
  }
  if (s.includes('block')) {
    return { bg: 'rgba(239,68,68,0.18)', fg: 'var(--red)' }
  }
  return { bg: 'var(--panel-bg)', fg: 'var(--text-dim)' }
}
