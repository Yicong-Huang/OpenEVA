import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ProjectSessionCard } from '../components/ProjectSessionCard'

// Keep tests focused on the card's own logic; mock the heavy integrations.
vi.mock('../hooks/useTerminal', () => ({
  useTerminal: vi.fn(),
}))
vi.mock('../hooks/useEventBus', () => ({
  useEventBus: vi.fn(),
}))

vi.mock('../api', () => ({
  api: {
    getProjectManager: vi.fn(),
    openProjectManager: vi.fn(),
    killProjectManager: vi.fn(),
    runProjectManagerAction: vi.fn(),
  },
}))

import { api } from '../api'

const RUNNING_INFO = {
  project_id: 'p1', tmux_name: 'pm-p1', running: true, status: 'idle',
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ProjectSessionCard - empty state (no session)', () => {
  it('renders an Open button when no session exists yet', async () => {
    vi.mocked(api.getProjectManager).mockRejectedValue(new Error('404'))
    render(<ProjectSessionCard projectId="p1" projectName="Proj One" />)
    // Wait for the initial fetch to settle.
    await waitFor(() => {
      expect(screen.getByText(/Project Manager/)).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /Open/i })).toBeInTheDocument()
  })

  it('clicking Open calls openProjectManager and flips to the live card', async () => {
    vi.mocked(api.getProjectManager).mockRejectedValue(new Error('404'))
    vi.mocked(api.openProjectManager).mockResolvedValue(RUNNING_INFO)
    render(<ProjectSessionCard projectId="p1" projectName="Proj One" />)
    await waitFor(() => screen.getByRole('button', { name: /Open/i }))
    fireEvent.click(screen.getByRole('button', { name: /Open/i }))
    await waitFor(() => {
      expect(api.openProjectManager).toHaveBeenCalledWith('p1')
    })
    // Once opened, the running card shows the (manager) label
    await waitFor(() => {
      expect(screen.getByText(/\(manager\)/)).toBeInTheDocument()
    })
  })
})

describe('ProjectSessionCard - running session', () => {
  it('renders project name + (manager) label + kill button', async () => {
    vi.mocked(api.getProjectManager).mockResolvedValue(RUNNING_INFO)
    render(<ProjectSessionCard projectId="p1" projectName="My Proj" />)
    await waitFor(() => screen.getByText(/My Proj/))
    expect(screen.getByText(/\(manager\)/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Kill/ })).toBeInTheDocument()
  })

  it('renders 3 manager action buttons', async () => {
    vi.mocked(api.getProjectManager).mockResolvedValue(RUNNING_INFO)
    render(<ProjectSessionCard projectId="p1" projectName="My Proj" />)
    await waitFor(() => screen.getByText(/My Proj/))
    expect(screen.getByRole('button', { name: /Sync Project/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Audit Anomalies/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Suggest Next/ })).toBeInTheDocument()
  })

  it('clicking an action button posts the prompt to /manager/run', async () => {
    vi.mocked(api.getProjectManager).mockResolvedValue(RUNNING_INFO)
    vi.mocked(api.runProjectManagerAction).mockResolvedValue({ ok: true, tmux_name: 'pm-p1', ran: true })
    render(<ProjectSessionCard projectId="p1" projectName="My Proj" />)
    await waitFor(() => screen.getByRole('button', { name: /Sync Project/ }))
    fireEvent.click(screen.getByRole('button', { name: /Sync Project/ }))
    await waitFor(() => {
      expect(api.runProjectManagerAction).toHaveBeenCalledWith(
        'p1',
        expect.stringContaining('eva-cli list-tasks p1'),
      )
    })
  })

  it('kill button prompts confirmation before deleting', async () => {
    vi.mocked(api.getProjectManager).mockResolvedValue(RUNNING_INFO)
    vi.mocked(api.killProjectManager).mockResolvedValue({ killed: true, tmux_name: 'pm-p1' })
    const origConfirm = window.confirm
    window.confirm = vi.fn().mockReturnValue(true)
    try {
      render(<ProjectSessionCard projectId="p1" projectName="My Proj" />)
      await waitFor(() => screen.getByRole('button', { name: /Kill/ }))
      fireEvent.click(screen.getByRole('button', { name: /Kill/ }))
      await waitFor(() => {
        expect(api.killProjectManager).toHaveBeenCalledWith('p1')
      })
    } finally {
      window.confirm = origConfirm
    }
  })

  it('kill confirm=false does NOT delete', async () => {
    vi.mocked(api.getProjectManager).mockResolvedValue(RUNNING_INFO)
    const origConfirm = window.confirm
    window.confirm = vi.fn().mockReturnValue(false)
    try {
      render(<ProjectSessionCard projectId="p1" projectName="My Proj" />)
      await waitFor(() => screen.getByRole('button', { name: /Kill/ }))
      fireEvent.click(screen.getByRole('button', { name: /Kill/ }))
      await new Promise(r => setTimeout(r, 50))
      expect(api.killProjectManager).not.toHaveBeenCalled()
    } finally {
      window.confirm = origConfirm
    }
  })

  it('action prompts interpolate the project_id into <PROJECT_ID> placeholder', async () => {
    vi.mocked(api.getProjectManager).mockResolvedValue({ ...RUNNING_INFO, project_id: 'demo-proj' })
    vi.mocked(api.runProjectManagerAction).mockResolvedValue({ ok: true, tmux_name: 'pm-demo', ran: true })
    render(<ProjectSessionCard projectId="demo-proj" projectName="Demo" />)
    await waitFor(() => screen.getByRole('button', { name: /Audit Anomalies/ }))
    fireEvent.click(screen.getByRole('button', { name: /Audit Anomalies/ }))
    await waitFor(() => {
      const prompt = vi.mocked(api.runProjectManagerAction).mock.calls[0][1]
      expect(prompt).not.toContain('<PROJECT_ID>')
    })
  })

  it('stopped (not running) session hides action buttons', async () => {
    vi.mocked(api.getProjectManager).mockResolvedValue({
      project_id: 'p1', tmux_name: 'pm-p1', running: false, status: 'stopped',
    })
    render(<ProjectSessionCard projectId="p1" projectName="P" />)
    await waitFor(() => screen.getByText('stopped'))
    // Action buttons render but are disabled (not running gates them)
    const sync = screen.getByRole('button', { name: /Sync Project/ })
    expect(sync).toBeDisabled()
    // Restart button is offered instead of Kill
    expect(screen.getByRole('button', { name: /Restart/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Kill$/ })).not.toBeInTheDocument()
  })
})

describe('ProjectSessionCard - error paths', () => {
  it('shows an alert when opening the project manager fails', async () => {
    vi.mocked(api.getProjectManager).mockRejectedValue(new Error('404'))
    vi.mocked(api.openProjectManager).mockRejectedValue(new Error('tmux broken'))
    const origAlert = window.alert
    window.alert = vi.fn()
    try {
      render(<ProjectSessionCard projectId="p1" projectName="Proj One" />)
      await waitFor(() => screen.getByRole('button', { name: /Open/i }))
      fireEvent.click(screen.getByRole('button', { name: /Open/i }))
      await waitFor(() => {
        expect(window.alert).toHaveBeenCalled()
      })
      const msg = (window.alert as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
      expect(msg).toContain('Failed to open project session')
      expect(msg).toContain('tmux broken')
    } finally {
      window.alert = origAlert
    }
  })

  it('shows an alert when running an action fails', async () => {
    vi.mocked(api.getProjectManager).mockResolvedValue(RUNNING_INFO)
    vi.mocked(api.runProjectManagerAction).mockRejectedValue(new Error('session dead'))
    const origAlert = window.alert
    window.alert = vi.fn()
    try {
      render(<ProjectSessionCard projectId="p1" projectName="Proj One" />)
      await waitFor(() => screen.getByRole('button', { name: /Sync Project/ }))
      fireEvent.click(screen.getByRole('button', { name: /Sync Project/ }))
      await waitFor(() => {
        expect(window.alert).toHaveBeenCalled()
      })
      const msg = (window.alert as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
      expect(msg).toContain('Action failed: Sync Project')
      expect(msg).toContain('session dead')
    } finally {
      window.alert = origAlert
    }
  })

  it('interpolates <PROJECT_ID> into every action prompt', async () => {
    vi.mocked(api.getProjectManager).mockResolvedValue({
      project_id: 'my-proj', tmux_name: 'pm-my-proj', running: true, status: 'idle',
    })
    vi.mocked(api.runProjectManagerAction).mockResolvedValue({ ok: true, tmux_name: 'pm-my-proj', ran: true })
    render(<ProjectSessionCard projectId="my-proj" projectName="My Proj" />)
    // Sync Project prompt contains <PROJECT_ID> in two places; both must be
    // filled in before the HTTP call fires.
    await waitFor(() => screen.getByRole('button', { name: /Sync Project/ }))
    fireEvent.click(screen.getByRole('button', { name: /Sync Project/ }))
    await waitFor(() => {
      expect(api.runProjectManagerAction).toHaveBeenCalled()
    })
    const prompt = vi.mocked(api.runProjectManagerAction).mock.calls[0][1]
    expect(prompt).not.toContain('<PROJECT_ID>')
    expect(prompt).toContain('my-proj')
  })
})
