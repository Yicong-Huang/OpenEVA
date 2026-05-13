/**
 * Shared utility functions for the Eva frontend.
 */
import { marked } from 'marked'

/**
 * Parse a backend-issued ISO timestamp.
 *
 * Eva's Python helper (`eva_db._now_iso`) emits UTC values WITHOUT a
 * trailing `Z` (e.g. "2026-04-22T16:30:45"). Browsers spec'd since
 * ES2015 read that naive form as LOCAL time -- so pointing `new Date()`
 * at it directly mis-interprets every backend timestamp by the local UTC
 * offset. This helper forces UTC when no timezone suffix is present,
 * and otherwise defers to the browser for strings that already carry
 * a `Z` / `+hh:mm` designator.
 */
export function parseBackendDate(isoStr: string): Date {
  if (!isoStr) return new Date(NaN)
  const hasTz = /[Zz]|[+-]\d\d:?\d\d$/.test(isoStr)
  return new Date(hasTz ? isoStr : isoStr + 'Z')
}

/** Format a backend UTC timestamp as compact local `MM-DD HH:MM`. */
export function formatLocalShort(isoStr: string): string {
  const d = parseBackendDate(isoStr)
  if (isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** Returns a human-readable relative time string (e.g. "5m ago", "2h ago"). */
export function timeAgo(isoStr: string): string {
  const now = Date.now()
  const then = parseBackendDate(isoStr).getTime()
  const seconds = Math.floor((now - then) / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

/** Returns a GitHub avatar URL for a given login. */
export function ghAvatar(login: string): string {
  return `https://avatars.githubusercontent.com/${login}?s=40`
}

/**
 * Extract `owner/repo` from a GitHub PR URL.
 *
 * `"https://github.com/acme/widgets/pull/100"` -> `"acme/widgets"`.
 * Returns `""` for anything that doesn't look like a PR URL so callers
 * can bail cleanly. Matches the backend helper in `utils.py:repo_from_pr_url`.
 */
export function repoFromPrUrl(url: string | null | undefined): string {
  return (url || '').replace('https://github.com/', '').split('/pull/')[0] || ''
}

/** Render markdown to HTML using the marked library, matching vanilla behavior. */
export function renderMarkdown(md: string): string {
  try {
    return marked.parse(md || '', { async: false }) as string
  } catch {
    // Fallback: escape HTML
    return (md || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
  }
}
