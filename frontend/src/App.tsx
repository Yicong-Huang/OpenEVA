import { useState, useEffect, useCallback } from 'react'
import { SideBar } from './components/SideBar'
import { TopBar } from './components/TopBar'
import { SetupBanner } from './components/SetupBanner'
import { useEventBus } from './hooks/useEventBus'
import { ToastProvider, useToast } from './components/Toast'
import { AlertProvider } from './components/Alert'
import { SessionStatusProvider } from './hooks/SessionStatusProvider'
import { api } from './api'
import type { EvaEvent } from './types'
import './pages'  // side-effect: registers built-in + extension pages
import { getPage, getUrlParamsByView } from './pages/registry'

// Base path from Vite config (e.g. '/app/')
const BASE_PATH = import.meta.env.BASE_URL.replace(/\/$/, '') // '/app'

function AppInner() {
  const [projectId, setProjectId] = useState<string | null>(null)
  const [view, setView] = useState('graph')
  const [taskId, setTaskId] = useState<string | null>(null)
  const [selectedPR, setSelectedPR] = useState<{ repo: string; number: number; taskId?: string; projectId?: string } | null>(null)
  // All Reviews: selected PR's url (URL is the review_prs primary key).
  // Kept separate from `selectedPR` so it round-trips through the URL
  // under a distinct param (`?review=<url>`), and so the All Reviews /
  // All PRs flows don't stomp each other's selection when the user
  // tabs between views.
  const [selectedReviewUrl, setSelectedReviewUrl] = useState<string | null>(null)
  // Cron Jobs: round-trips through `?cron_job=<id>` so refresh /
  // deep-link land back on the same job. Mirrors the ReviewsPage
  // selectedReviewUrl pattern -- selection lives at App level so the
  // URL reflects it.
  const [selectedCronJobId, setSelectedCronJobId] = useState<number | null>(null)
  // Tickets: when a TicketLink elsewhere in the app fires an
  // `eva:navigate-ticket` custom event we switch to the Tickets view
  // and stash the (key, instance) so TicketsPage pre-selects on
  // mount. The pair lives in URL params too so reload re-lands on
  // the same ticket.
  const [requestedTicket, setRequestedTicket] = useState<{
    key: string; instance?: string
  } | null>(null)
  const [unreadEventCount, setUnreadEventCount] = useState(0)
  const { showToast } = useToast()

  // GitHub events: toast + badge
  useEventBus('github.*', useCallback((event: Record<string, unknown>) => {
    setUnreadEventCount((c) => c + 1)
    const severity = typeof event.severity === 'string' ? event.severity : ''
    const eventType = typeof event.type === 'string' ? event.type : ''
    showToast({
      title: (typeof event.title === 'string' ? event.title : '') || eventType,
      message: typeof event.message === 'string' ? event.message : undefined,
      type: severity === 'error' ? 'error' : severity === 'warning' ? 'warning' : 'info',
      duration: 5000,
    })
  }, [showToast]))

  // Auth events: badge
  useEventBus('auth.*', useCallback(() => {
    setUnreadEventCount((c) => c + 1)
  }, []))

  // Slack events: badge
  useEventBus('slack.*', useCallback(() => {
    setUnreadEventCount((c) => c + 1)
  }, []))

  // Restore from URL on mount, auto-select first project if none specified
  useEffect(() => {
    const params = new URLSearchParams(location.search)
    if (params.get('project')) setProjectId(params.get('project'))
    if (params.get('view')) {
      // Bookmark redirect: the per-project `list` and `sessions`
      // sub-views are gone -- the unified Project Page replaces them.
      const v = params.get('view')!
      setView(v === 'list' || v === 'sessions' ? 'graph' : v)
    }
    if (params.get('task')) setTaskId(params.get('task'))
    if (params.get('pr') && params.get('pr_repo')) {
      const number = parseInt(params.get('pr')!, 10)
      const rawRepo = params.get('pr_repo')!
      // Be defensive: stale URL state (or older code paths) sometimes
      // carries just the bare repo name instead of the full
      // "owner/repo" gh CLI needs. Set the bare value provisionally,
      // then upgrade once lookupPR returns a URL we can parse.
      setSelectedPR({ repo: rawRepo, number })
      api.lookupPR(number).then(r => {
        if (!r.found) return
        // Derive owner/repo from the canonical PR URL if we got one.
        const m = r.url ? r.url.match(/github\.com\/([^/]+\/[^/]+)\/pull\//) : null
        const repo = m ? m[1] : rawRepo
        setSelectedPR(prev => (prev && prev.number === number)
          ? { ...prev, repo, taskId: r.task_id, projectId: r.project }
          : prev)
      }).catch(() => {})
    }
    // All Reviews selection -- the PR URL itself is the primary key so
    // the round-trip is simpler than for All PRs.
    if (params.get('review')) {
      setSelectedReviewUrl(params.get('review'))
    }

    // Cron Jobs selection (numeric row id). Defensive parse: a stale
    // URL pointing at a deleted job lands on the no-selection state.
    if (params.get('cron_job')) {
      const n = parseInt(params.get('cron_job')!, 10)
      if (Number.isFinite(n) && n > 0) setSelectedCronJobId(n)
    }

    // Tickets selection (URL deep-link). TicketLink also writes these
    // params at click time so reload re-lands on the same ticket.
    if (params.get('ticket')) {
      setRequestedTicket({
        key: params.get('ticket')!,
        instance: params.get('ticket_instance') || undefined,
      })
    }

    // Auto-select first project when no project or special view in URL
    if (!params.get('project') && !params.get('view')) {
      api.getProjects().then(data => {
        if (data.projects.length > 0) {
          setProjectId(data.projects[0].id)
          setView('graph')
        }
      }).catch(() => {})
    }
  }, [])

  // Update URL when navigation state changes. Per-view whitelist
  // prevents leakage: e.g. opening a ticket then switching to
  // cron-jobs used to keep `?ticket=...` in the URL forever, even
  // though TicketsPage wasn't visible. Each view declares the set of
  // params it owns; everything else gets dropped on emit (state is
  // preserved in React so navigating back re-emits cleanly).
  useEffect(() => {
    const params = new URLSearchParams()
    if (view) params.set('view', view)
    const allowed = getUrlParamsByView()[view] ?? new Set<string>()
    if (projectId && allowed.has('project')) params.set('project', projectId)
    if (taskId && allowed.has('task')) params.set('task', taskId)
    if (selectedPR && allowed.has('pr')) {
      params.set('pr', String(selectedPR.number))
      params.set('pr_repo', selectedPR.repo)
    }
    if (selectedReviewUrl && allowed.has('review')) {
      params.set('review', selectedReviewUrl)
    }
    if (selectedCronJobId && allowed.has('cron_job')) {
      params.set('cron_job', String(selectedCronJobId))
    }
    if (requestedTicket && allowed.has('ticket')) {
      params.set('ticket', requestedTicket.key)
      if (requestedTicket.instance) {
        params.set('ticket_instance', requestedTicket.instance)
      }
    }
    history.replaceState(null, '', BASE_PATH + (params.toString() ? '?' + params : ''))
  }, [projectId, view, taskId, selectedPR, selectedReviewUrl,
      selectedCronJobId, requestedTicket])

  // In-app navigation from <TicketLink> components. The component
  // pushes its own URL state then dispatches this custom event so
  // we know to switch to the Tickets view.
  useEffect(() => {
    const handler = (e: Event) => {
      const ce = e as CustomEvent<{ key: string; instance?: string }>
      if (!ce.detail?.key) return
      setRequestedTicket({ key: ce.detail.key, instance: ce.detail.instance })
      setView('tickets')
    }
    window.addEventListener('eva:navigate-ticket', handler as EventListener)
    return () => window.removeEventListener(
      'eva:navigate-ticket', handler as EventListener,
    )
  }, [])

  const handleEventNavigate = useCallback(async (ev: EvaEvent) => {
    // GitHub events: navigate to PR detail with task context
    if (ev.type.startsWith('github.') && ev.url) {
      const match = ev.url.match(/github\.com\/([^/]+\/[^/]+)\/pull\/(\d+)/)
      if (match) {
        const repo = match[1]
        const number = parseInt(match[2], 10)
        setView('all-prs')
        // Look up task info so action buttons render
        try {
          const lookup = await api.lookupPR(number)
          if (lookup.found) {
            setSelectedPR({ repo, number, taskId: lookup.task_id, projectId: lookup.project })
          } else {
            setSelectedPR({ repo, number })
          }
        } catch {
          setSelectedPR({ repo, number })
        }
        return
      }
    }
    // Slack events: open in new tab
    if (ev.type === 'slack.message' && ev.url) {
      window.open(ev.url, '_blank')
      return
    }
    // Agent events: navigate to all live tasks
    if (ev.type === 'agent.needs_permission' || ev.type === 'agent.task_done') {
      setView('all-tasks')
    }
  }, [])

  const handleNavigate = (pid: string | null, v: string) => {
    setProjectId(pid)
    setView(v)
    setTaskId(null)
    setSelectedPR(null)
  }

  const handleSelectLiveTask = useCallback((pid: string | null, tid: string | null) => {
    setProjectId(pid)
    setTaskId(tid)
  }, [])

  return (
    <>
      <TopBar
        unreadEventCount={unreadEventCount}
        onEventsOpened={() => setUnreadEventCount(0)}
        onEventNavigate={handleEventNavigate}
        onNavigate={handleNavigate}
        onSelectTask={setTaskId}
        onSelectPR={setSelectedPR}
        onSelectReview={setSelectedReviewUrl}
        onSelectTicket={setRequestedTicket}
      />
      <SetupBanner />
      <div className="app-body">
        <SideBar activeProject={projectId} activeView={view} onNavigate={handleNavigate} />
        <div id="main-panel" style={{ flex: 1, overflow: 'auto', padding: 16 }}>
          {getPage(view)?.render({
            projectId, setProjectId,
            taskId, setTaskId,
            view, setView,
            selectedPR, setSelectedPR,
            selectedReviewUrl, setSelectedReviewUrl,
            selectedCronJobId, setSelectedCronJobId,
            requestedTicket,
            handleNavigate, handleSelectLiveTask,
          })}
        </div>
      </div>
    </>
  )
}

export default function App() {
  return (
    <AlertProvider>
      <ToastProvider>
        <SessionStatusProvider>
          <AppInner />
        </SessionStatusProvider>
      </ToastProvider>
    </AlertProvider>
  )
}
