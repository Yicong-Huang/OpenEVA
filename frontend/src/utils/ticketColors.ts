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
  if (t.includes('bug') || t.includes('defect')
      || t.includes('incident') || t.includes('outage')) {
    return { bg: 'rgba(239,68,68,0.18)', fg: 'var(--red)' }
  }
  if (t.includes('story') || t.includes('feature') || t.includes('spike')) {
    return { bg: 'rgba(59,130,246,0.18)', fg: 'var(--blue)' }
  }
  if (t.includes('improv') || t.includes('tech')
      || t.includes('refactor') || t.includes('debt')) {
    return { bg: 'rgba(20,184,166,0.18)', fg: 'var(--teal, #14b8a6)' }
  }
  if (t.includes('epic')) {
    return { bg: 'rgba(168,85,247,0.18)', fg: 'var(--purple)' }
  }
  if (t.includes('sub')) {
    return { bg: 'rgba(168,162,158,0.18)', fg: 'var(--text-dim)' }
  }
  if (t.includes('task')) {
    return { bg: 'rgba(99,102,241,0.18)', fg: 'var(--accent)' }
  }
  return { bg: 'var(--panel-bg)', fg: 'var(--text-dim)' }
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
