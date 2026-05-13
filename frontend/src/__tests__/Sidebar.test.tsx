import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { SideBar } from '../components/SideBar'
import '../pages'  // populate page registry (nav items)

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

// SideBar discovers plugins via `import.meta.glob` at build time;
// the only plugin guaranteed to be present in an OSS install is the
// `core/src/plugins/pr/PRPlugin.tsx` widget. Mock JUST that one here
// (preserving its real Mini variant so the mini-icon assertions still
// work) so this test file stays vendor-neutral and runs on a checkout
// that has no extension namespaces. Per-plugin behaviour tests for
// extension plugins live with the implementation, e.g.
// `<extension>/test/<plugin>/<Plugin>.frontend.test.tsx`.
vi.mock('@core/plugins/pr/PRPlugin', async (importOriginal) => {
  const real = await importOriginal<typeof import('@core/plugins/pr/PRPlugin')>()
  return {
    PRPlugin: () => <div data-testid="pr-bar-mock">PR Stats</div>,
    MiniPRPlugin: real.MiniPRPlugin,
  }
})

// Mock ProjectTree to avoid its own fetch calls
vi.mock('../components/sidebar/ProjectTree', () => ({
  ProjectTree: ({ activeProject, activeView }: {
    activeProject: string | null
    activeView: string
    onNavigate: (pid: string | null, view: string) => void
  }) => (
    <div data-testid="project-tree">
      <span>ProjectTree(active={activeProject || 'null'}, view={activeView})</span>
    </div>
  ),
}))

const projectsData = {
  projects: [
    {
      id: 'proj-alpha',
      name: 'Alpha',
      description: 'Alpha project',
      has_tickets: true,
      progress: 75,
      task_counts: { in_progress: 2, done: 3 },
      tasks: {},
    },
    {
      id: 'proj-beta',
      name: 'Beta Project',
      description: 'Beta project',
      has_tickets: false,
      progress: 30,
      task_counts: { not_started: 5 },
      tasks: {},
    },
  ],
}

const mockLocalStorage: Record<string, string> = {}
const localStorageMock = {
  getItem: vi.fn((key: string) => mockLocalStorage[key] ?? null),
  setItem: vi.fn((key: string, value: string) => { mockLocalStorage[key] = value }),
  removeItem: vi.fn((key: string) => { delete mockLocalStorage[key] }),
  clear: vi.fn(() => { Object.keys(mockLocalStorage).forEach((k) => delete mockLocalStorage[k]) }),
  get length() { return Object.keys(mockLocalStorage).length },
  key: vi.fn((_: number) => null),
}

function mockFetchResponse(data: unknown) {
  mockFetch.mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  })
}

/** Helper to set up collapsed mode via localStorage. */
function setCollapsedState(collapsed: boolean) {
  if (collapsed) {
    mockLocalStorage['eva-sidebar-collapsed'] = '1'
  } else {
    delete mockLocalStorage['eva-sidebar-collapsed']
  }
}

describe('SideBar', () => {
  beforeEach(() => {
    mockFetch.mockReset()
    Object.keys(mockLocalStorage).forEach((k) => delete mockLocalStorage[k])
    Object.defineProperty(window, 'localStorage', { value: localStorageMock, writable: true })
  })

  // ===================== Expanded mode =====================

  describe('expanded mode', () => {
    it('renders sidebar testid', () => {
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)
      expect(screen.getByTestId('sidebar')).toBeInTheDocument()
    })

    it('renders Projects header', () => {
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)
      expect(screen.getByText('Projects')).toBeInTheDocument()
    })

    it('renders ProjectTree component', () => {
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)
      expect(screen.getByTestId('project-tree')).toBeInTheDocument()
    })

    it('renders the Plugins panel with the discovered plugin slots', () => {
      // The Plugins panel rendering is what we assert -- the specific
      // plugin instances present depend on which extensions are
      // checked out. We require the always-present PR widget; per-
      // plugin behaviour for extension plugins is tested with the
      // implementation under `<extension>/test/<plugin>/`.
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)
      expect(screen.getByText('Plugins')).toBeInTheDocument()
      expect(screen.getByTestId('pr-bar-mock')).toBeInTheDocument()
    })

    it('renders collapse button with left arrow', () => {
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)
      expect(screen.getByTitle('Collapse sidebar')).toBeInTheDocument()
    })

    it('does not show expand button in expanded mode', () => {
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)
      expect(screen.queryByTitle('Expand sidebar')).not.toBeInTheDocument()
    })
  })

  // ===================== Collapsed mode =====================

  describe('collapsed mode', () => {
    beforeEach(() => {
      setCollapsedState(true)
    })

    it('renders sidebar in collapsed mode when localStorage has collapsed=1', () => {
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)
      expect(screen.getByTestId('sidebar')).toBeInTheDocument()
      // Should show expand button, not collapse button
      expect(screen.getByTitle('Expand sidebar')).toBeInTheDocument()
      expect(screen.queryByTitle('Collapse sidebar')).not.toBeInTheDocument()
    })

    it('does not render Projects header or ProjectTree in collapsed mode', () => {
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)
      expect(screen.queryByText('Projects')).not.toBeInTheDocument()
      expect(screen.queryByTestId('project-tree')).not.toBeInTheDocument()
    })

    it('does not render full Plugins panel in collapsed mode', () => {
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)
      expect(screen.queryByText('Plugins')).not.toBeInTheDocument()
      expect(screen.queryByTestId('pr-bar-mock')).not.toBeInTheDocument()
      expect(screen.queryByTestId('lunch-bar')).not.toBeInTheDocument()
    })

    it('renders project initials after loading projects', async () => {
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)
      // "Alpha" -> "A", "Beta Project" -> "BP"
      await waitFor(() => {
        expect(screen.getByText('A')).toBeInTheDocument()
        expect(screen.getByText('BP')).toBeInTheDocument()
      })
    })

    it('exposes project progress in collapsed mode (now via title + ring stroke, not bare 7px text)', async () => {
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)
      // The progress used to be rendered as 7px '%' text under the
      // initials; that was barely legible. New design: progress is
      // conveyed by a circular SVG ring stroke (data driven via
      // stroke-dashoffset) AND surfaced via the `title` attribute
      // for hover + a11y. Test the latter contract.
      await waitFor(() => {
        expect(screen.getByTitle(/Alpha \(75%\)/)).toBeInTheDocument()
        expect(screen.getByTitle(/Beta Project \(30%\)/)).toBeInTheDocument()
      })
    })

    it('renders nav icon buttons: Live Tasks and Pull Requests', () => {
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)
      expect(screen.getByTitle('Live Tasks')).toBeInTheDocument()
      expect(screen.getByTitle('Pull Requests')).toBeInTheDocument()
    })

    it('clicking Live Tasks nav button calls onNavigate', () => {
      mockFetchResponse(projectsData)
      const onNavigate = vi.fn()
      render(<SideBar activeProject={null} activeView="graph" onNavigate={onNavigate} />)
      fireEvent.click(screen.getByTitle('Live Tasks'))
      expect(onNavigate).toHaveBeenCalledWith(null, 'all-tasks')
    })

    it('clicking Pull Requests nav button calls onNavigate', () => {
      mockFetchResponse(projectsData)
      const onNavigate = vi.fn()
      render(<SideBar activeProject={null} activeView="graph" onNavigate={onNavigate} />)
      fireEvent.click(screen.getByTitle('Pull Requests'))
      expect(onNavigate).toHaveBeenCalledWith(null, 'all-prs')
    })

    it('clicking Reviews nav button routes to the review queue view', () => {
      mockFetchResponse(projectsData)
      const onNavigate = vi.fn()
      render(<SideBar activeProject={null} activeView="graph" onNavigate={onNavigate} />)
      fireEvent.click(screen.getByTitle('Reviews'))
      expect(onNavigate).toHaveBeenCalledWith(null, 'all-reviews')
    })

    it.each([
      ['Tickets', 'tickets'],
      ['Cron Jobs', 'cron-jobs'],
      ['Work Log', 'work-log'],
    ])('collapsed-mode %s nav button routes to %s view', (label, view) => {
      // The collapsed sidebar exposes the same global nav set as the
      // expanded ProjectTree. Each NavButton's onClick fires
      // onNavigate(null, view); locking that contract here so a
      // future refactor doesn't silently disconnect any of them.
      mockFetchResponse(projectsData)
      const onNavigate = vi.fn()
      render(<SideBar activeProject={null} activeView="graph" onNavigate={onNavigate} />)
      fireEvent.click(screen.getByTitle(label))
      expect(onNavigate).toHaveBeenCalledWith(null, view)
    })

    it('clicking a project button calls onNavigate with project id and graph view', async () => {
      mockFetchResponse(projectsData)
      const onNavigate = vi.fn()
      render(<SideBar activeProject={null} activeView="graph" onNavigate={onNavigate} />)
      await waitFor(() => {
        expect(screen.getByText('A')).toBeInTheDocument()
      })
      // Click the Alpha project button (find by title)
      fireEvent.click(screen.getByTitle('Alpha (75%)'))
      expect(onNavigate).toHaveBeenCalledWith('proj-alpha', 'graph')
    })

    it('renders the PR mini icon in the collapsed plugin column', async () => {
      // The PR widget is the only plugin guaranteed to ship with the
      // OSS install -- it lives under `core/src/plugins/pr/` and its
      // mini variant is rendered in the collapsed sidebar. Behaviour
      // tests for extension plugins (lunch/dinner/boba mini variants)
      // live with the implementation under `<extension>/test/`.
      mockFetch.mockImplementation((url: string) => {
        if (url === '/api/projects') {
          return Promise.resolve({
            ok: true, json: () => Promise.resolve(projectsData),
          })
        }
        if (url === '/api/live-stats') {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ open_prs: { total: 5 } }),
          })
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      })

      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)

      await waitFor(() => {
        expect(screen.getByTitle('PRs: 5 open')).toBeInTheDocument()
      })
    })

    it('fetches /api/projects for collapsed project list', async () => {
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)
      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith('/api/projects')
      })
    })

    it('handles /api/projects fetch failure gracefully', async () => {
      mockFetch.mockRejectedValue(new Error('network error'))
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)
      // Should render sidebar without crashing
      expect(screen.getByTestId('sidebar')).toBeInTheDocument()
      // No project initials should appear
      await waitFor(() => {
        expect(screen.queryByText('A')).not.toBeInTheDocument()
      })
    })

    it('highlights active project button when activeProject matches', async () => {
      mockFetchResponse(projectsData)
      render(<SideBar activeProject="proj-alpha" activeView="graph" onNavigate={vi.fn()} />)
      await waitFor(() => {
        expect(screen.getByText('A')).toBeInTheDocument()
      })
      // The active project button should exist with correct title
      const btn = screen.getByTitle('Alpha (75%)')
      expect(btn).toBeInTheDocument()
      // Active button should have accent color
      expect(btn.style.color).toContain('accent')
    })

    it('highlights active Live Tasks nav button', () => {
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="all-tasks" onNavigate={vi.fn()} />)
      const tasksBtn = screen.getByTitle('Live Tasks')
      expect(tasksBtn.style.background).toContain('rgba(99')
    })
  })

  // ===================== Toggle between modes =====================

  describe('toggle collapsed/expanded', () => {
    it('clicking collapse button switches to collapsed mode', async () => {
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)
      // Start in expanded mode
      expect(screen.getByText('Projects')).toBeInTheDocument()
      expect(screen.getByTitle('Collapse sidebar')).toBeInTheDocument()

      // Click collapse
      fireEvent.click(screen.getByTitle('Collapse sidebar'))

      // Should now be in collapsed mode
      await waitFor(() => {
        expect(screen.queryByText('Projects')).not.toBeInTheDocument()
        expect(screen.getByTitle('Expand sidebar')).toBeInTheDocument()
      })
    })

    it('clicking expand button switches to expanded mode', async () => {
      setCollapsedState(true)
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)
      // Start in collapsed mode
      expect(screen.getByTitle('Expand sidebar')).toBeInTheDocument()
      expect(screen.queryByText('Projects')).not.toBeInTheDocument()

      // Click expand
      fireEvent.click(screen.getByTitle('Expand sidebar'))

      // Should now be in expanded mode
      await waitFor(() => {
        expect(screen.getByText('Projects')).toBeInTheDocument()
        expect(screen.getByTitle('Collapse sidebar')).toBeInTheDocument()
      })
    })

    it('toggle persists collapsed state to localStorage', () => {
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)

      // Click collapse
      fireEvent.click(screen.getByTitle('Collapse sidebar'))
      expect(localStorageMock.setItem).toHaveBeenCalledWith('eva-sidebar-collapsed', '1')
    })

    it('toggle persists expanded state to localStorage', () => {
      setCollapsedState(true)
      mockFetchResponse(projectsData)
      render(<SideBar activeProject={null} activeView="graph" onNavigate={vi.fn()} />)

      // Click expand
      fireEvent.click(screen.getByTitle('Expand sidebar'))
      expect(localStorageMock.setItem).toHaveBeenCalledWith('eva-sidebar-collapsed', '0')
    })
  })
})
