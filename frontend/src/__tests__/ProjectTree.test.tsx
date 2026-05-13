import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ProjectTree } from '../components/sidebar/ProjectTree'
import type { Project } from '../types'
import '../pages'  // populate page registry (nav items)

const mockProjects: Project[] = [
  {
    id: 'proj-1',
    name: 'Alpha Project',
    description: 'First project',
    has_tickets: true,
    progress: 75,
    task_counts: {},
    tasks: {},
  },
  {
    id: 'proj-2',
    name: 'Beta Project',
    description: 'Second project',
    has_tickets: false,
    progress: 40,
    task_counts: {},
    tasks: {},
  },
]

vi.mock('../api', () => ({
  api: {
    getProjects: vi.fn(),
  },
}))

import { api } from '../api'

const mockedGetProjects = vi.mocked(api.getProjects)

describe('ProjectTree', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders project names', async () => {
    mockedGetProjects.mockResolvedValue({ projects: mockProjects })
    render(
      <ProjectTree activeProject={null} activeView="graph" onNavigate={() => {}} />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('sidebar-tree')).toBeInTheDocument()
    })

    expect(screen.getByText('Alpha Project')).toBeInTheDocument()
    expect(screen.getByText('Beta Project')).toBeInTheDocument()
  })

  it('click project calls onNavigate with the unified Project Page view', async () => {
    mockedGetProjects.mockResolvedValue({ projects: mockProjects })
    const onNavigate = vi.fn()
    render(
      <ProjectTree activeProject={null} activeView="graph" onNavigate={onNavigate} />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('sidebar-tree')).toBeInTheDocument()
    })

    // Clicking the project name navigates straight to the unified
    // Project Page (formerly "Task Tracker"). No more child views.
    fireEvent.click(screen.getByTestId('project-proj-1'))
    expect(onNavigate).toHaveBeenCalledWith('proj-1', 'graph')
  })

  it('active project highlighted', async () => {
    mockedGetProjects.mockResolvedValue({ projects: mockProjects })
    render(
      <ProjectTree activeProject="proj-1" activeView="graph" onNavigate={() => {}} />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('sidebar-tree')).toBeInTheDocument()
    })

    // The active project's name node carries the .active class.
    expect(screen.getByTestId('project-proj-1').className).toContain('active')
    // The OTHER project does not.
    expect(screen.getByTestId('project-proj-2').className).not.toContain('active')
  })

  it('shows loading state then recovers on fetch error', async () => {
    mockedGetProjects.mockRejectedValue(new Error('network error'))
    render(
      <ProjectTree activeProject={null} activeView="graph" onNavigate={() => {}} />,
    )

    // Initially shows loading
    expect(screen.getByText('Loading...')).toBeInTheDocument()

    // After the rejection settles, loading should disappear and tree should render (empty)
    await waitFor(() => {
      expect(screen.queryByText('Loading...')).not.toBeInTheDocument()
    })
  })

  it('renders All Sessions nav item and navigates on click', async () => {
    mockedGetProjects.mockResolvedValue({ projects: mockProjects })
    const onNavigate = vi.fn()
    render(
      <ProjectTree activeProject={null} activeView="graph" onNavigate={onNavigate} />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('sidebar-tree')).toBeInTheDocument()
    })

    const allTasksBtn = screen.getByTestId('all-tasks-btn')
    expect(allTasksBtn).toBeInTheDocument()
    expect(allTasksBtn).toHaveTextContent('Live Tasks')
    fireEvent.click(allTasksBtn)
    expect(onNavigate).toHaveBeenCalledWith(null, 'all-tasks')
  })

  it('renders Pull Requests nav item and navigates on click', async () => {
    mockedGetProjects.mockResolvedValue({ projects: mockProjects })
    const onNavigate = vi.fn()
    render(
      <ProjectTree activeProject={null} activeView="graph" onNavigate={onNavigate} />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('sidebar-tree')).toBeInTheDocument()
    })

    const allPrsBtn = screen.getByTestId('all-prs-btn')
    expect(allPrsBtn).toBeInTheDocument()
    expect(allPrsBtn).toHaveTextContent('Pull Requests')
    fireEvent.click(allPrsBtn)
    expect(onNavigate).toHaveBeenCalledWith(null, 'all-prs')
  })

  it('Live Tasks button is active when activeView is all-tasks', async () => {
    mockedGetProjects.mockResolvedValue({ projects: mockProjects })
    render(
      <ProjectTree activeProject={null} activeView="all-tasks" onNavigate={() => {}} />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('sidebar-tree')).toBeInTheDocument()
    })

    const allTasksBtn = screen.getByTestId('all-tasks-btn')
    expect(allTasksBtn.className).toContain('active')
  })

  it('Pull Requests button is active when activeView is all-prs', async () => {
    mockedGetProjects.mockResolvedValue({ projects: mockProjects })
    render(
      <ProjectTree activeProject={null} activeView="all-prs" onNavigate={() => {}} />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('sidebar-tree')).toBeInTheDocument()
    })

    const allPrsBtn = screen.getByTestId('all-prs-btn')
    expect(allPrsBtn.className).toContain('active')
  })

  // ---- Global nav buttons (no per-project scope) ----
  // Each top-level nav button (all-reviews, tickets, benchmarks,
  // cron-jobs, work-log) calls `onNavigate(null, <view>)` with a null
  // projectId. These guarantee the user can reach every page without
  // first selecting a project.

  it.each([
    ['all-reviews-btn', 'all-reviews'],
    ['tickets-btn', 'tickets'],
    ['cron-jobs-btn', 'cron-jobs'],
    ['work-log-btn', 'work-log'],
  ])('%s clicks navigate to %s with null projectId', async (testId, view) => {
    mockedGetProjects.mockResolvedValue({ projects: mockProjects })
    const onNavigate = vi.fn()
    render(
      <ProjectTree
        activeProject={null} activeView="graph" onNavigate={onNavigate}
      />,
    )
    await waitFor(() => {
      expect(screen.getByTestId('sidebar-tree')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId(testId))
    expect(onNavigate).toHaveBeenCalledWith(null, view)
  })

  it.each([
    ['all-reviews-btn', 'all-reviews'],
    ['tickets-btn', 'tickets'],
    ['cron-jobs-btn', 'cron-jobs'],
    ['work-log-btn', 'work-log'],
  ])('%s shows .active class when activeView matches', async (testId, view) => {
    mockedGetProjects.mockResolvedValue({ projects: mockProjects })
    render(
      <ProjectTree
        activeProject={null} activeView={view} onNavigate={vi.fn()}
      />,
    )
    await waitFor(() => {
      expect(screen.getByTestId('sidebar-tree')).toBeInTheDocument()
    })
    expect(screen.getByTestId(testId).className).toContain('active')
  })
})
