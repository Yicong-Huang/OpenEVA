// Page registry. Built-in pages register themselves in `pages/index.tsx`;
// extensions register their pages by dropping a `<ext>/src/frontend/pages.tsx`
// file that the same module picks up via `import.meta.glob`.
//
// Each page provides a `render(ctx)` function so App.tsx doesn't need to
// know any page's prop shape -- pages cherry-pick what they want off the
// shared context. The context is wider than any single page needs, but
// it's the same set of state App.tsx already maintains.

import type { ReactElement } from 'react'

export type SelectedPR = {
  repo: string
  number: number
  taskId?: string
  projectId?: string
}

export interface PageContext {
  projectId: string | null
  setProjectId: (p: string | null) => void
  taskId: string | null
  setTaskId: (t: string | null) => void
  view: string
  setView: (v: string) => void

  selectedPR: SelectedPR | null
  setSelectedPR: (pr: SelectedPR | null) => void

  selectedReviewUrl: string | null
  setSelectedReviewUrl: (u: string | null) => void

  selectedCronJobId: number | null
  setSelectedCronJobId: (id: number | null) => void

  requestedTicket: { key: string; instance?: string } | null

  handleNavigate: (projectId: string | null, view: string) => void
  handleSelectLiveTask: (projectId: string | null, taskId: string | null) => void
}

export interface PageDef {
  // Matches the `view` URL/state value.
  id: string
  // SideBar nav. Pages without `nav` aren't rendered as nav buttons
  // (e.g. the per-project graph view, which is reached by clicking a
  // project rather than a nav item).
  nav?: {
    label: string
    icon: string  // emoji codepoint, e.g. '\u{1F517}'
    order: number // ascending sort
  }
  // Per-view URL param whitelist. Always-emitted `view` is implicit.
  urlParams: ReadonlySet<string>
  // Returns null when the page can't render in the current state
  // (e.g. graph view with no projectId selected).
  render: (ctx: PageContext) => ReactElement | null
}

const _pages = new Map<string, PageDef>()

export function registerPage(def: PageDef): void {
  _pages.set(def.id, def)
}

export function getRegisteredPages(): PageDef[] {
  return Array.from(_pages.values())
}

export function getNavPages(): PageDef[] {
  return getRegisteredPages()
    .filter((p): p is PageDef & { nav: NonNullable<PageDef['nav']> } => !!p.nav)
    .sort((a, b) => a.nav.order - b.nav.order)
}

export function getPage(id: string): PageDef | undefined {
  return _pages.get(id)
}

export function getUrlParamsByView(): Record<string, ReadonlySet<string>> {
  const out: Record<string, ReadonlySet<string>> = {}
  for (const p of _pages.values()) {
    out[p.id] = p.urlParams
  }
  return out
}
