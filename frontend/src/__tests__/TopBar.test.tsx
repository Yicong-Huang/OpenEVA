import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { TopBar } from '../components/TopBar'

vi.mock('../components/status/AuthStatus', () => ({
  AuthStatus: () => <div data-testid="auth-status">Auth</div>,
}))
vi.mock('../components/status/AIUsageStatus', () => ({
  AIUsageStatus: () => <div data-testid="ai-usage">Usage</div>,
}))
vi.mock('../components/status/EventStatus', () => ({
  EventStatus: ({ sseUnread, onOpened }: { sseUnread?: number; onOpened?: () => void }) => (
    <div data-testid="event-status" onClick={onOpened}>
      Events {sseUnread ?? 0}
    </div>
  ),
}))
vi.mock('../hooks/useClickOutside', () => ({
  useClickOutside: vi.fn(),
}))

const mockToggle = vi.fn()
let mockTheme = 'dark'
vi.mock('../hooks/useTheme', () => ({
  useTheme: () => ({ theme: mockTheme, toggle: mockToggle, setTheme: vi.fn() }),
}))

describe('TopBar', () => {
  it('renders OpenEVA title', () => {
    render(<TopBar />)
    expect(screen.getByText('OpenEVA')).toBeInTheDocument()
  })

  it('renders all status components', () => {
    render(<TopBar />)
    expect(screen.getByTestId('auth-status')).toBeInTheDocument()
    expect(screen.getByTestId('ai-usage')).toBeInTheDocument()
    expect(screen.getByTestId('event-status')).toBeInTheDocument()
  })

  it('passes unreadEventCount to EventStatus', () => {
    render(<TopBar unreadEventCount={5} />)
    expect(screen.getByTestId('event-status').textContent).toContain('5')
  })

  it('calls onEventsOpened when events is clicked', () => {
    const handler = vi.fn()
    render(<TopBar onEventsOpened={handler} />)
    fireEvent.click(screen.getByTestId('event-status'))
    expect(handler).toHaveBeenCalledOnce()
  })

  it('renders menu button', () => {
    render(<TopBar />)
    expect(screen.getByTestId('menu-btn')).toBeInTheDocument()
  })

  it('opens menu dropdown on click', () => {
    render(<TopBar />)
    fireEvent.click(screen.getByTestId('menu-btn'))
    expect(screen.getByText('Settings')).toBeInTheDocument()
    expect(screen.getByText('Help')).toBeInTheDocument()
  })

  it('closes menu dropdown on second click', () => {
    render(<TopBar />)
    fireEvent.click(screen.getByTestId('menu-btn'))
    expect(screen.getByText('Settings')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('menu-btn'))
    expect(screen.queryByText('Settings')).not.toBeInTheDocument()
  })

  it('shows "Light Mode" toggle in dark theme', () => {
    mockTheme = 'dark'
    render(<TopBar />)
    fireEvent.click(screen.getByTestId('menu-btn'))
    expect(screen.getByTestId('theme-toggle')).toHaveTextContent('Light Mode')
  })

  it('shows "Dark Mode" toggle in light theme', () => {
    mockTheme = 'light'
    render(<TopBar />)
    fireEvent.click(screen.getByTestId('menu-btn'))
    expect(screen.getByTestId('theme-toggle')).toHaveTextContent('Dark Mode')
  })

  it('calls toggle when theme toggle is clicked', () => {
    mockTheme = 'dark'
    mockToggle.mockClear()
    render(<TopBar />)
    fireEvent.click(screen.getByTestId('menu-btn'))
    fireEvent.click(screen.getByTestId('theme-toggle'))
    expect(mockToggle).toHaveBeenCalledOnce()
  })

  it('closes menu after theme toggle click', () => {
    mockTheme = 'dark'
    render(<TopBar />)
    fireEvent.click(screen.getByTestId('menu-btn'))
    fireEvent.click(screen.getByTestId('theme-toggle'))
    // Menu should close
    expect(screen.queryByText('Settings')).not.toBeInTheDocument()
  })

  it('clicking Settings closes the menu and opens the modal', () => {
    // Stub fetch so SettingsModal's initial GET doesn't error.
    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ settings: {} }), { status: 200 })
    ) as typeof fetch
    try {
      render(<TopBar />)
      fireEvent.click(screen.getByTestId('menu-btn'))
      expect(screen.getByTestId('menu-settings')).toBeInTheDocument()
      fireEvent.click(screen.getByTestId('menu-settings'))
      // Menu closes (no menu item visible) but the modal is up.
      expect(screen.queryByTestId('menu-settings')).not.toBeInTheDocument()
      expect(screen.getByTestId('settings-modal')).toBeInTheDocument()
    } finally {
      globalThis.fetch = origFetch
    }
  })

  it('clicking Help closes the menu', () => {
    render(<TopBar />)
    fireEvent.click(screen.getByTestId('menu-btn'))
    expect(screen.getByText('Help')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Help'))
    // Menu should close
    expect(screen.queryByText('Help')).not.toBeInTheDocument()
  })

  it('opens the SettingsModal when Settings menu item is clicked', async () => {
    // Stub fetch so SettingsModal's initial GET resolves cleanly.
    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn(async (url: string | URL | Request) => {
      const u = String(url)
      if (u.includes('/api/settings')) {
        return new Response(JSON.stringify({ settings: {} }), { status: 200 })
      }
      return new Response('', { status: 404 })
    }) as typeof fetch
    try {
      render(<TopBar />)
      fireEvent.click(screen.getByTestId('menu-btn'))
      fireEvent.click(screen.getByTestId('menu-settings'))
      expect(await screen.findByTestId('settings-modal')).toBeInTheDocument()
      // Backdrop click closes the modal.
      fireEvent.click(screen.getByTestId('settings-backdrop'))
      expect(screen.queryByTestId('settings-modal')).not.toBeInTheDocument()
    } finally {
      globalThis.fetch = origFetch
    }
  })
})
