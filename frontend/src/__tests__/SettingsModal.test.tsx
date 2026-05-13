import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { SettingsModal } from '../components/SettingsModal'

let listResponse: Record<string, unknown> = {}
let resolvedResponse: {
  rules: string[]
  fork_to_upstream: Record<string, string>
  resolved: Array<{ repo: string; source: 'rule' | 'wildcard'; wildcard?: string; pr_count: number }>
} = { rules: [], fork_to_upstream: {}, resolved: [] }
let ticketsResponse: {
  tickets: Array<unknown>
  configured: boolean
  instances: Array<{ name: string; base_url: string; auth_type: string; email: string; jql: string; has_token: boolean }>
} = { tickets: [], configured: false, instances: [] }
let putRequests: Array<{ url: string; body: unknown }> = []
let resolveCalls = 0

function mockFetch() {
  globalThis.fetch = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const u = String(url)
    if (init?.method === 'PUT' && u.includes('/api/settings/')) {
      const body = init.body ? JSON.parse(init.body as string) : null
      putRequests.push({ url: u, body })
      return new Response(JSON.stringify({ key: u.split('/').pop(), value: (body as { value: unknown })?.value }), { status: 200 })
    }
    if (init?.method === 'PUT' && u.includes('/api/tickets/instances/')) {
      const body = init.body ? JSON.parse(init.body as string) : null
      putRequests.push({ url: u, body })
      return new Response(JSON.stringify({ ok: true, name: (body as { name: string }).name }), { status: 200 })
    }
    if (init?.method === 'DELETE' && u.includes('/api/tickets/instances/')) {
      putRequests.push({ url: u, body: null })
      return new Response(JSON.stringify({ ok: true }), { status: 200 })
    }
    if (u.endsWith('/api/repos/resolved')) {
      resolveCalls += 1
      return new Response(JSON.stringify(resolvedResponse), { status: 200 })
    }
    if (u.startsWith('/api/tickets') || u.includes('/api/tickets?')) {
      return new Response(JSON.stringify(ticketsResponse), { status: 200 })
    }
    if (u.endsWith('/api/settings')) {
      return new Response(JSON.stringify({ settings: listResponse }), { status: 200 })
    }
    return new Response('', { status: 404 })
  }) as typeof fetch
}

describe('SettingsModal', () => {
  let origFetch: typeof fetch

  beforeEach(() => {
    listResponse = {}
    resolvedResponse = { rules: [], fork_to_upstream: {}, resolved: [] }
    ticketsResponse = { tickets: [], configured: false, instances: [] }
    putRequests = []
    resolveCalls = 0
    origFetch = globalThis.fetch
    mockFetch()
  })

  afterEach(() => {
    globalThis.fetch = origFetch
  })

  it('opens with the Repos tab as the default', async () => {
    listResponse = { 'service.github.allowed_repos': ['example/repo'] }
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    // Sidebar shows the four root entries.
    expect(screen.getByTestId('sidebar-repos')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-appearance')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-plugins')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-intervals')).toBeInTheDocument()
    // Repos content is rendered by default.
    // Repos-specific element: the resolved-list refresh button only
    // exists on the Repos tab.
    expect(await screen.findByTestId('settings-resolved-refresh'))
      .toBeInTheDocument()
  })

  it('clicking a root sidebar item switches the tab', async () => {
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-appearance'))
    expect(await screen.findByTestId('settings-theme-toggle')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('sidebar-plugins'))
    // Plugins tab shows the quick-toggles section.
    expect(await screen.findByText(/Quick toggles/i)).toBeInTheDocument()
  })

  it('plugin sub-items are listed under the Plugins root entry', async () => {
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    // Sub-items are visible because plugins is expanded by default.
    expect(screen.getByTestId('sidebar-plugin-forkable')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-plugin-ubereats')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-plugin-boba')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-plugin-slack')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-plugin-github')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-plugin-cert')).toBeInTheDocument()
  })

  it('clicking a plugin sub-item switches to the Plugins tab (single content area)', async () => {
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    // Start on Repos.
    // Repos-specific element: the resolved-list refresh button only
    // exists on the Repos tab.
    expect(await screen.findByTestId('settings-resolved-refresh'))
      .toBeInTheDocument()
    // Click plugin sub-item -> Plugins tab activates.
    fireEvent.click(screen.getByTestId('sidebar-plugin-forkable'))
    expect(await screen.findByText(/Quick toggles/i)).toBeInTheDocument()
    // The Forkable section is rendered (anchor lives on the Plugins tab).
    expect(document.getElementById('plugin-forkable')).toBeInTheDocument()
  })

  it('plugin enable toggle PUTs the boolean to the matching key', async () => {
    listResponse = {}
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-plugins'))
    // Toggle off Forkable from the per-section header (default is on).
    const sectionToggle = await screen.findByTestId('settings-toggle-plugin-forkable') as HTMLInputElement
    expect(sectionToggle.checked).toBe(true)
    fireEvent.click(sectionToggle)
    await waitFor(() => expect(putRequests.length).toBeGreaterThan(0))
    const last = putRequests[putRequests.length - 1]
    expect(last.url).toContain('plugin.forkable.enabled')
    expect(last.body).toEqual({ value: false })
  })

  it('quick-toggles row PUTs the same key as the section toggle', async () => {
    listResponse = {}
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-plugins'))
    const quick = await screen.findByTestId('settings-bool-ubereats') as HTMLInputElement
    expect(quick.checked).toBe(true)
    fireEvent.click(quick)
    await waitFor(() => expect(putRequests.length).toBeGreaterThan(0))
    const last = putRequests[putRequests.length - 1]
    expect(last.url).toContain('plugin.ubereats.enabled')
    expect(last.body).toEqual({ value: false })
  })

  it('loaded settings hydrate the cookie field', async () => {
    listResponse = {
      'plugin.forkable.cookie': 'my-cookie',
    }
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-plugins'))
    const cookieInput = await screen.findByTestId('settings-input-cookie') as HTMLInputElement
    expect(cookieInput.value).toBe('my-cookie')
  })

  it('saving an edited text field PUTs the new value', async () => {
    listResponse = { 'plugin.boba.user_filter': 'old' }
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-plugins'))
    const input = await screen.findByTestId('settings-input-user-filter') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'new-name' } })
    const saveBtn = input.parentElement?.querySelector('button.accent') as HTMLButtonElement
    fireEvent.click(saveBtn)
    await waitFor(() => {
      expect(putRequests.some((r) => r.url.includes('plugin.boba.user_filter'))).toBe(true)
    })
  })

  it('ESC key closes the modal', async () => {
    const onClose = vi.fn()
    render(<SettingsModal onClose={onClose} />)
    await screen.findByTestId('settings-modal')
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })

  it('Repos tab fetches and renders the resolved repo list', async () => {
    resolvedResponse = {
      rules: ['example/repo', 'myorg/*'],
      fork_to_upstream: {},
      resolved: [
        { repo: 'example/repo', source: 'rule', pr_count: 154 },
        { repo: 'myorg/svc', source: 'wildcard',
          wildcard: 'myorg/*', pr_count: 41 },
      ],
    }
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    // Wait for resolveRepos to land.
    await waitFor(() => expect(resolveCalls).toBeGreaterThan(0))
    expect(await screen.findByTestId('settings-resolved-row-example/repo'))
      .toBeInTheDocument()
    expect(screen.getByTestId('settings-resolved-row-myorg/svc'))
      .toBeInTheDocument()
    // Both wildcard origin and PR counts surface in the row.
    const trendRow = screen.getByTestId('settings-resolved-row-example/repo')
    expect(trendRow.textContent).toContain('154')
    const rtRow = screen.getByTestId('settings-resolved-row-myorg/svc')
    expect(rtRow.textContent).toContain('myorg/*')
    expect(rtRow.textContent).toContain('41')
  })

  it('Refresh button re-fetches the resolved list', async () => {
    resolvedResponse = { rules: [], fork_to_upstream: {}, resolved: [] }
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    await waitFor(() => expect(resolveCalls).toBeGreaterThan(0))
    const callsBefore = resolveCalls
    fireEvent.click(screen.getByTestId('settings-resolved-refresh'))
    await waitFor(() => expect(resolveCalls).toBe(callsBefore + 1))
  })

  it('Repos tab shows empty-state hint when nothing resolves', async () => {
    resolvedResponse = { rules: ['x/*'], fork_to_upstream: {}, resolved: [] }
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    await waitFor(() => expect(resolveCalls).toBeGreaterThan(0))
    expect(await screen.findByText(/No matching repos yet/i)).toBeInTheDocument()
  })

  it('Repos tab pre-fills rules editor from /api/repos/resolved when settings is empty', async () => {
    // Settings table doesn't have the rules key yet, but the resolver
    // returns the hardcoded fallback. The editor MUST show those.
    listResponse = {}
    resolvedResponse = {
      rules: ['example/repo', 'myorg/*'],
      fork_to_upstream: { 'me/repo': 'example/repo' },
      resolved: [],
    }
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    await waitFor(() => expect(resolveCalls).toBeGreaterThan(0))
    // ListField renders an input for each rule with the value set.
    const inputs = await screen.findAllByPlaceholderText(/Repo \(org\/name or org\/\*\)/i)
    expect(inputs.length).toBe(2)
    const values = inputs.map((i) => (i as HTMLInputElement).value)
    expect(values).toContain('example/repo')
    expect(values).toContain('myorg/*')
    // Fork map: at least one input pair populated.
    const forkInputs = screen.getAllByPlaceholderText(/Fork \(org\/name\)/i)
    expect((forkInputs[0] as HTMLInputElement).value).toBe('me/repo')
  })

  it('Intervals tab lists all 6 poller cadence editors', async () => {
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-intervals'))
    // Each row has an input keyed by setting key (with non-alnum
    // chars replaced with `_`). Lock down the contract: all 6
    // pollers are reachable from the UI.
    const slugs = [
      'service_intervals_github_poll_seconds',
      'service_intervals_slack_monitor_seconds',
      'service_intervals_ubereats_seconds',
      'service_intervals_usage_refresh_seconds',
      'service_intervals_cert_check_seconds',
      'service_jira_sync_interval_seconds',
    ]
    for (const slug of slugs) {
      expect(await screen.findByTestId(`settings-interval-input-${slug}`))
        .toBeInTheDocument()
      expect(screen.getByTestId(`settings-interval-save-${slug}`))
        .toBeInTheDocument()
    }
  })

  it('Intervals tab Save PUTs the new cadence', async () => {
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-intervals'))
    const slug = 'service_intervals_github_poll_seconds'
    const input = await screen.findByTestId(
      `settings-interval-input-${slug}`,
    ) as HTMLInputElement
    fireEvent.change(input, { target: { value: '20' } })
    fireEvent.click(screen.getByTestId(`settings-interval-save-${slug}`))
    await waitFor(() => {
      const matched = putRequests.find((r) =>
        r.url.includes('service.intervals.github_poll_seconds'),
      )
      expect(matched).toBeTruthy()
      expect((matched!.body as { value: unknown }).value).toBe(20)
    })
  })

  it('Intervals Save with empty input writes the canonical default', async () => {
    listResponse = { 'service.intervals.github_poll_seconds': 99 }
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-intervals'))
    const slug = 'service_intervals_github_poll_seconds'
    const input = await screen.findByTestId(
      `settings-interval-input-${slug}`,
    ) as HTMLInputElement
    // Pre-loaded with override 99; clear it.
    expect(input.value).toBe('99')
    fireEvent.change(input, { target: { value: '' } })
    fireEvent.click(screen.getByTestId(`settings-interval-save-${slug}`))
    // GitHub poll default is 10s.
    await waitFor(() => {
      const matched = putRequests.find((r) =>
        r.url.includes('service.intervals.github_poll_seconds'),
      )
      expect(matched).toBeTruthy()
      expect((matched!.body as { value: unknown }).value).toBe(10)
    })
  })

  it('Themes tab shows all 16 palette swatches', async () => {
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-appearance'))
    for (const t of ['dark', 'light', 'solarized-dark', 'solarized-light',
                     'high-contrast', 'nord',
                     'rose-pine-moon', 'github-dimmed', 'cobalt',
                     'tokyo-night', 'catppuccin-mocha',
                     'gruvbox-dark', 'everforest-dark', 'dracula',
                     'crimson-dark', 'slate-pro']) {
      expect(await screen.findByTestId(`settings-theme-${t}`))
        .toBeInTheDocument()
    }
  })

  it('engineering-dashboard themes (crimson-dark / slate-pro) apply', async () => {
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-appearance'))
    for (const t of ['crimson-dark', 'slate-pro']) {
      fireEvent.click(await screen.findByTestId(`settings-theme-${t}`))
      expect(document.documentElement.getAttribute('data-theme')).toBe(t)
    }
  })

  it('clicking a palette swatch applies that theme to <html>', async () => {
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-appearance'))
    fireEvent.click(await screen.findByTestId('settings-theme-nord'))
    expect(document.documentElement.getAttribute('data-theme')).toBe('nord')
  })

  it('new modern themes (rose-pine-moon / github-dimmed / cobalt) apply', async () => {
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-appearance'))
    for (const t of ['rose-pine-moon', 'github-dimmed', 'cobalt']) {
      fireEvent.click(await screen.findByTestId(`settings-theme-${t}`))
      expect(document.documentElement.getAttribute('data-theme')).toBe(t)
    }
  })

  it('latest themes (tokyo-night / catppuccin-mocha / dracula) apply', async () => {
    // The user kept asking for more theme variety. Each new palette
    // must round-trip through the same CSS-only data-theme apply path
    // as the existing ones -- no special-casing.
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-appearance'))
    for (const t of ['tokyo-night', 'catppuccin-mocha', 'dracula']) {
      fireEvent.click(await screen.findByTestId(`settings-theme-${t}`))
      expect(document.documentElement.getAttribute('data-theme')).toBe(t)
    }
  })

  it('retro / earthy themes (gruvbox-dark / everforest-dark) apply', async () => {
    // Two complementary additions: gruvbox-dark fills the "warm
    // retro" gap (no other palette uses earthy browns + olive),
    // everforest-dark fills the "green-forward" gap (popular among
    // nvim users). Both must round-trip through the same CSS apply
    // path as the others.
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-appearance'))
    for (const t of ['gruvbox-dark', 'everforest-dark']) {
      fireEvent.click(await screen.findByTestId(`settings-theme-${t}`))
      expect(document.documentElement.getAttribute('data-theme')).toBe(t)
    }
  })

  it('font scale picker offers 4 sizes and applies the selection', async () => {
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-appearance'))
    expect(await screen.findByTestId('settings-font-scale-0.85'))
      .toBeInTheDocument()
    expect(screen.getByTestId('settings-font-scale-1.3')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('settings-font-scale-1.15'))
    expect(document.documentElement.style.getPropertyValue('--font-scale'))
      .toBe('1.15')
  })

  it('density picker writes data-density and --gap', async () => {
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-appearance'))
    fireEvent.click(await screen.findByTestId('settings-density-spacious'))
    expect(document.documentElement.getAttribute('data-density'))
      .toBe('spacious')
    expect(document.documentElement.style.getPropertyValue('--gap'))
      .toBe('1.4')
  })

  it('font family picker offers 5 stacks and applies the selection', async () => {
    // Regression: themes only swapped colour vars, never typography.
    // Adding font-family as an independent personalisation knob means
    // any of the 14 palettes can pair with any of the 5 font stacks
    // (system / sans / serif / mono / rounded). Each option must
    // round-trip through --font-family + data-font-family on :root.
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-appearance'))
    for (const f of ['system', 'sans', 'serif', 'mono', 'rounded']) {
      expect(await screen.findByTestId(`settings-font-family-${f}`))
        .toBeInTheDocument()
    }
    fireEvent.click(await screen.findByTestId('settings-font-family-serif'))
    expect(document.documentElement.getAttribute('data-font-family'))
      .toBe('serif')
    // Stack actually written so body picks it up via var(--font-family).
    const stack = document.documentElement.style.getPropertyValue('--font-family')
    expect(stack).toMatch(/Georgia/)
  })

  it('font family persists to localStorage and survives a remount', async () => {
    // Future-proof the contract: changing palette, font-scale, density,
    // or font-family writes to its own localStorage key so a refresh
    // (which re-imports the module) re-reads the user's choice.
    const { unmount } = render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-appearance'))
    fireEvent.click(await screen.findByTestId('settings-font-family-mono'))
    expect(localStorage.getItem('eva-font-family')).toBe('mono')
    unmount()
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-appearance'))
    // Active selection still reads from storage.
    expect(document.documentElement.getAttribute('data-font-family'))
      .toBe('mono')
  })

  it('Repos tab shows GitHub account rules editor', async () => {
    listResponse = {
      'service.github.account_rules': [
        { match: 'company-x/', account: 'alice-work' },
      ],
    }
    resolvedResponse = {
      rules: [], fork_to_upstream: {}, resolved: [],
    }
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    // The Account rules section uses ListField with two columns
    // ('match' and 'account'), so the existing list-input testids
    // identify the inputs.
    expect(await screen.findByTestId('settings-list-input-0-match'))
      .toHaveValue('company-x/')
    expect(screen.getByTestId('settings-list-input-0-account'))
      .toHaveValue('alice-work')
  })

  it('JIRA section shows existing instances from /api/tickets', async () => {
    ticketsResponse = {
      tickets: [], configured: true,
      instances: [
        { name: 'example', base_url: 'https://issues.example.org/jira',
          auth_type: 'bearer', email: '', jql: 'assignee=me', has_token: true },
        { name: 'cloud', base_url: 'https://acme.atlassian.net',
          auth_type: 'basic', email: 'a@b.com', jql: '', has_token: false },
      ],
    }
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-plugins'))
    expect(document.getElementById('plugin-jira')).toBeInTheDocument()
    // Each instance gets its own card with a Save + Delete button.
    expect(await screen.findByTestId('settings-jira-instance-example'))
      .toBeInTheDocument()
    expect(screen.getByTestId('settings-jira-instance-cloud'))
      .toBeInTheDocument()
    expect(screen.getByTestId('settings-jira-add-instance'))
      .toBeInTheDocument()
  })

  it('Save on a JIRA instance card PUTs the right payload', async () => {
    ticketsResponse = {
      tickets: [], configured: true,
      instances: [{
        name: 'cloud', base_url: 'https://acme.atlassian.net',
        auth_type: 'basic', email: 'a@b.com', jql: '', has_token: false,
      }],
    }
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-plugins'))
    const card = await screen.findByTestId('settings-jira-instance-cloud')
    // Find the API token input (last password input on the card)
    const tokenInput = card.querySelector('input[type="password"]') as HTMLInputElement
    fireEvent.change(tokenInput, { target: { value: 'NEW_TOKEN' } })
    fireEvent.click(screen.getByTestId('settings-jira-save-cloud'))
    await waitFor(() => {
      const put = putRequests.find((r) => r.url.endsWith('/api/tickets/instances/cloud'))
      expect(put).toBeTruthy()
    })
    const put = putRequests.find((r) => r.url.endsWith('/api/tickets/instances/cloud'))!
    const body = put.body as { name: string; api_token: string; auth_type: string }
    expect(body.name).toBe('cloud')
    expect(body.api_token).toBe('NEW_TOKEN')
    expect(body.auth_type).toBe('basic')
  })

  it('Save without a token errors inline (does not hit API)', async () => {
    ticketsResponse = {
      tickets: [], configured: true,
      instances: [{
        name: 'cloud', base_url: 'https://x', auth_type: 'basic',
        email: '', jql: '', has_token: true,  // existing token, user didn't repaste
      }],
    }
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-plugins'))
    await screen.findByTestId('settings-jira-instance-cloud')
    fireEvent.click(screen.getByTestId('settings-jira-save-cloud'))
    expect(await screen.findByText(/api_token is required/i)).toBeInTheDocument()
    // No PUT was issued.
    expect(putRequests.find((r) => r.url.endsWith('/api/tickets/instances/cloud')))
      .toBeUndefined()
  })

  it('Add instance creates a blank card with no name', async () => {
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-plugins'))
    fireEvent.click(await screen.findByTestId('settings-jira-add-instance'))
    expect(screen.getByTestId('settings-jira-instance-new'))
      .toBeInTheDocument()
  })

  it('Delete on a blank stub just drops the local card (no API call)', async () => {
    // A freshly-added stub has name="" -- the user hasn't filled it in
    // yet. Delete should be a local-only drop, NOT a confirm + API hit
    // (there's no row to delete server-side). Covers the
    // `if (!name) { setInstances(...); return }` early-return branch.
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-plugins'))
    fireEvent.click(await screen.findByTestId('settings-jira-add-instance'))
    expect(screen.getByTestId('settings-jira-instance-new'))
      .toBeInTheDocument()
    fireEvent.click(screen.getByTestId('settings-jira-delete-new'))
    // Card gone, no DELETE issued.
    expect(screen.queryByTestId('settings-jira-instance-new')).toBeNull()
    expect(putRequests.find((r) => r.url.includes('/api/tickets/instances/')))
      .toBeUndefined()
  })

  it('Delete on a saved instance confirms and DELETEs', async () => {
    ticketsResponse = {
      tickets: [], configured: true,
      instances: [{
        name: 'cloud', base_url: 'https://x', auth_type: 'basic',
        email: 'a@b.com', jql: '', has_token: true,
      }],
    }
    const origConfirm = window.confirm
    window.confirm = vi.fn().mockReturnValue(true)
    try {
      render(<SettingsModal onClose={vi.fn()} />)
      await screen.findByTestId('settings-modal')
      fireEvent.click(screen.getByTestId('sidebar-plugins'))
      await screen.findByTestId('settings-jira-instance-cloud')
      fireEvent.click(screen.getByTestId('settings-jira-delete-cloud'))
      await waitFor(() => {
        const del = putRequests.find((r) =>
          r.url.endsWith('/api/tickets/instances/cloud') && r.body === null,
        )
        expect(del).toBeTruthy()
      })
    } finally {
      window.confirm = origConfirm
    }
  })

  it('Themes tab shows Reviews 3-pane ratios editor with current value', async () => {
    listResponse = {
      'ui.layout.reviews_col_ratios': [40, 30, 30],
    }
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-layout'))
    const queue = await screen.findByTestId('settings-reviews-ratio-queue') as HTMLInputElement
    expect(queue.value).toBe('40')
    expect((screen.getByTestId('settings-reviews-ratio-card') as HTMLInputElement).value).toBe('30')
    expect((screen.getByTestId('settings-reviews-ratio-detail') as HTMLInputElement).value).toBe('30')
  })

  it('Reviews ratios editor PUTs the new triple on Save', async () => {
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-layout'))
    const queue = await screen.findByTestId('settings-reviews-ratio-queue')
    fireEvent.change(queue, { target: { value: '50' } })
    fireEvent.click(screen.getByTestId('settings-reviews-ratios-save'))
    await waitFor(() => {
      const put = putRequests.find((r) =>
        r.url.endsWith('/api/settings/ui.layout.reviews_col_ratios'),
      )
      expect(put).toBeTruthy()
      expect((put!.body as { value: number[] }).value).toEqual([50, 35, 40])
    })
  })

  it('Reviews ratios Save is disabled until a field changes', async () => {
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-layout'))
    const saveBtn = await screen.findByTestId('settings-reviews-ratios-save') as HTMLButtonElement
    expect(saveBtn.disabled).toBe(true)
    // Change a field -> button enables.
    fireEvent.change(
      screen.getByTestId('settings-reviews-ratio-card'),
      { target: { value: '40' } },
    )
    expect(saveBtn.disabled).toBe(false)
  })

  it('Delete confirm cancel does NOT issue DELETE', async () => {
    ticketsResponse = {
      tickets: [], configured: true,
      instances: [{
        name: 'cloud', base_url: 'https://x', auth_type: 'basic',
        email: '', jql: '', has_token: true,
      }],
    }
    const origConfirm = window.confirm
    window.confirm = vi.fn().mockReturnValue(false)
    try {
      render(<SettingsModal onClose={vi.fn()} />)
      await screen.findByTestId('settings-modal')
      fireEvent.click(screen.getByTestId('sidebar-plugins'))
      await screen.findByTestId('settings-jira-instance-cloud')
      fireEvent.click(screen.getByTestId('settings-jira-delete-cloud'))
      // Brief tick for any async path.
      await new Promise((r) => setTimeout(r, 10))
      expect(putRequests.find((r) =>
        r.url.endsWith('/api/tickets/instances/cloud') && r.body === null,
      )).toBeUndefined()
    } finally {
      window.confirm = origConfirm
    }
  })

  it('JIRA sub-item appears in the sidebar', async () => {
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    expect(screen.getByTestId('sidebar-plugin-jira')).toBeInTheDocument()
  })

  it('JIRA appears in the quick-toggles list as a first-class plugin', async () => {
    // After the auto-sync rollout JIRA became a periodic poller and
    // got the same enable/disable affordance as other plugins.
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-plugins'))
    expect(await screen.findByTestId('settings-bool-jira-(tickets)'))
      .toBeInTheDocument()
    // The PluginSection header inside the Plugins tab also shows
    // an enable toggle keyed off the same setting.
    expect(screen.getByTestId('settings-toggle-plugin-jira'))
      .toBeInTheDocument()
  })

  it('toggling JIRA quick-toggle PUTs plugin.jira.enabled', async () => {
    listResponse = {}
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    fireEvent.click(screen.getByTestId('sidebar-plugins'))
    const quick = await screen.findByTestId('settings-bool-jira-(tickets)') as HTMLInputElement
    expect(quick.checked).toBe(true)
    fireEvent.click(quick)
    await waitFor(() => expect(putRequests.length).toBeGreaterThan(0))
    const last = putRequests[putRequests.length - 1]
    expect(last.url).toContain('plugin.jira.enabled')
    expect(last.body).toEqual({ value: false })
  })

  it('saving rules PUTs to settings and re-fetches resolved list', async () => {
    listResponse = {}
    resolvedResponse = {
      rules: ['example/repo'],
      fork_to_upstream: {},
      resolved: [],
    }
    render(<SettingsModal onClose={vi.fn()} />)
    await screen.findByTestId('settings-modal')
    await waitFor(() => expect(resolveCalls).toBeGreaterThan(0))
    // Modify the first input.
    const inputs = await screen.findAllByPlaceholderText(/Repo \(org\/name or org\/\*\)/i)
    fireEvent.change(inputs[0], { target: { value: 'fizz/buzz' } })
    // Click the section's Save button (last one in the rules list field).
    const buttons = screen.getAllByText('Save')
    fireEvent.click(buttons[0])
    await waitFor(() => {
      expect(putRequests.some((r) => r.url.includes('service.github.allowed_repos'))).toBe(true)
    })
    const last = putRequests.find((r) => r.url.includes('service.github.allowed_repos'))!
    expect(last.body).toEqual({ value: ['fizz/buzz'] })
  })
})
