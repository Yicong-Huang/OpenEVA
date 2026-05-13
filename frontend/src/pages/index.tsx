// Built-in page registrations + extension auto-discovery.
//
// Importing this module has the side-effect of populating the page
// registry. App.tsx imports it once at module-load. Extensions ship a
// `<ext>/src/frontend/pages.tsx` file picked up by the glob below;
// because Vite's `@<ext>` alias maps to `<ext>/src/`, extension code
// can `import { registerPage } from '@app/pages/registry'` without
// caring about relative paths.

import { registerPage } from './registry'
import { ProjectPage } from './ProjectPage'
import { PRsPage } from './PRsPage'
import { ReviewsPage } from './ReviewsPage'
import { CronJobsPage } from './CronJobsPage'
import { TicketsPage } from './TicketsPage'
import { SessionsPage } from './SessionsPage'
import { WorkLogPage } from './WorkLogPage'

registerPage({
  id: 'graph',
  // Per-project view; reached by clicking a project. No SideBar slot.
  urlParams: new Set(['project', 'task']),
  render: (ctx) => ctx.projectId ? (
    <ProjectPage
      projectId={ctx.projectId}
      selectedTask={ctx.taskId}
      onSelectTask={ctx.setTaskId}
      selectedPR={ctx.selectedPR}
      onSelectPR={ctx.setSelectedPR}
    />
  ) : null,
})

registerPage({
  id: 'all-tasks',
  nav: { label: 'Live Tasks', icon: '\u{1F4CB}', order: 10 },
  urlParams: new Set(['project', 'task', 'pr', 'pr_repo']),
  render: (ctx) => (
    <SessionsPage
      onNavigate={ctx.handleNavigate}
      selectedPR={ctx.selectedPR}
      onSelectPR={ctx.setSelectedPR}
      selectedProjectId={ctx.projectId}
      selectedTaskId={ctx.taskId}
      onSelectLiveTask={ctx.handleSelectLiveTask}
    />
  ),
})

registerPage({
  id: 'all-prs',
  nav: { label: 'Pull Requests', icon: '\u{1F517}', order: 20 },
  urlParams: new Set(['project', 'pr', 'pr_repo']),
  render: (ctx) => (
    <PRsPage
      selectedPR={ctx.selectedPR}
      onSelectPR={ctx.setSelectedPR}
    />
  ),
})

registerPage({
  id: 'all-reviews',
  nav: { label: 'Reviews', icon: '\u{1F440}', order: 30 },
  urlParams: new Set(['review']),
  render: (ctx) => (
    <ReviewsPage
      selectedReviewUrl={ctx.selectedReviewUrl}
      onSelectReview={ctx.setSelectedReviewUrl}
    />
  ),
})

registerPage({
  id: 'tickets',
  nav: { label: 'Tickets', icon: '\u{1F39F}', order: 40 },
  urlParams: new Set(['ticket', 'ticket_instance']),
  render: (ctx) => (
    <TicketsPage
      requestedKey={ctx.requestedTicket?.key}
      requestedInstance={ctx.requestedTicket?.instance}
      onSelectLinkedTask={(pid, tid) => {
        ctx.setProjectId(pid)
        ctx.setTaskId(tid)
        ctx.setView('graph')
      }}
    />
  ),
})

registerPage({
  id: 'cron-jobs',
  nav: { label: 'Cron Jobs', icon: '\u{23F2}', order: 60 },
  urlParams: new Set(['cron_job']),
  render: (ctx) => (
    <CronJobsPage
      selectedJobId={ctx.selectedCronJobId}
      onSelectJob={ctx.setSelectedCronJobId}
    />
  ),
})

registerPage({
  id: 'work-log',
  nav: { label: 'Work Log', icon: '\u{1F4DD}', order: 70 },
  urlParams: new Set([]),
  render: () => <WorkLogPage />,
})

// Extension auto-discovery: any sibling-of-`frontend/` folder that
// ships a `src/frontend/pages.tsx` file gets imported here. Each such
// module is expected to call `registerPage(...)` for its own pages.
// Vite expands the glob at build time, so missing folders simply
// don't appear -- a clean OSS checkout with no extensions still
// builds and runs.
import.meta.glob('../../../*/src/frontend/pages.tsx', { eager: true })
