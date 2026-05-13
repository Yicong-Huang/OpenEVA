import React, { useEffect, useRef, useState, useCallback } from 'react'
import { api, type JiraInstance } from '../api'
import {
  useTheme,
  THEMES,
  FONT_SCALES,
  DENSITIES,
  FONT_FAMILIES,
  BRIGHTNESSES,
  type Theme,
  type FontScale,
  type Density,
  type FontFamily,
  type Brightness,
} from '../hooks/useTheme'
import { refreshPluginsEnabled } from '../hooks/usePluginsEnabled'

/**
 * Settings keys -- mirrors the constants in `core/settings.py`. Add
 * a new field here and the matching backend constant + seed entry.
 */
const KEYS = {
  forkableCookie: 'plugin.forkable.cookie',
  ubereatsDid: 'plugin.ubereats.did',
  ubereatsJwt: 'plugin.ubereats.jwt',
  ubereatsSid: 'plugin.ubereats.sid',
  bobaChannel: 'plugin.boba.channel_id',
  bobaUserFilter: 'plugin.boba.user_filter',
  bobaTestMode: 'plugin.boba.test_mode',
  slackChannels: 'plugin.slack_monitor.channels',
  ghAllowedRepos: 'service.github.allowed_repos',
  ghForkToUpstream: 'service.github.fork_to_upstream',
  ghAccountRules: 'service.github.account_rules',
  intervalUbereats: 'service.intervals.ubereats_seconds',
  intervalGhPoll: 'service.intervals.github_poll_seconds',
  // JIRA -- powers the Tickets page. The instances list itself is
  // edited via dedicated /api/tickets/instances/{name} endpoints (see
  // <JiraInstancesEditor>); only the cross-instance sync interval
  // lives on the generic settings table.
  jiraSyncInterval: 'service.jira.sync_interval_seconds',
  // Master enable flags (default: true if absent).
  enabledPr: 'plugin.pr.enabled',
  enabledForkable: 'plugin.forkable.enabled',
  enabledUbereats: 'plugin.ubereats.enabled',
  enabledBoba: 'plugin.boba.enabled',
  enabledSlack: 'plugin.slack_monitor.enabled',
  enabledGhPoll: 'plugin.github_poll.enabled',
  enabledCert: 'plugin.cert_tracker.enabled',
  enabledJira: 'plugin.jira.enabled',
  // Layout knobs -- per the user's "布局也可以用setting来做" ask.
  // Each value is a list of positive width-percentages, one per pane.
  reviewsRatios: 'ui.layout.reviews_col_ratios',         // 3-pane
  cronJobsRatios: 'ui.layout.cron_jobs_col_ratios',      // 2-pane
  prsRatios: 'ui.layout.prs_col_ratios',                 // 3-pane (PR + task)
  sessionsRatios: 'ui.layout.sessions_col_ratios',       // 3-pane (PR selected)
  ticketsRatios: 'ui.layout.tickets_col_ratios',         // 3-pane (ticket selected)
  // WorkLog horizon knobs -- settings-driven so users with longer or
  // shorter retention windows can tune them. Bounded server-side.
  worklogDays: 'ui.worklog.day_mode_days',               // [7, 365]
  worklogStandupWeeks: 'ui.worklog.standup_mode_weeks',  // [1, 52]
  // CronJobs JobCard history-strip horizon. Default 20 runs.
  cronJobsRunsHistoryLimit: 'ui.cron_jobs.runs_history_limit',  // [5, 500]
  // Tickets queue-pane fetch horizon. Default 100 cached tickets.
  ticketsListLimit: 'ui.tickets.list_limit',                    // [10, 1000]
  // Reviews `gh search prs` per-account `--limit` on every sync. Default 50.
  reviewsSyncSearchLimit: 'ui.reviews.sync_search_limit',       // [10, 200]
  // Background-poller cadences. All live under
  // `service.intervals.*`; clamping/validation is server-side via
  // `core.settings.get_interval_seconds`. (`intervalUbereats` and
  // `intervalGhPoll` are already declared above for the existing
  // plugin sections; the IntervalsTab reuses those.)
  intervalSlackMonitor: 'service.intervals.slack_monitor_seconds',
  intervalUsageRefresh: 'service.intervals.usage_refresh_seconds',
  intervalCertCheck: 'service.intervals.cert_check_seconds',
} as const

type SettingsBag = Record<string, unknown>
type RootTab = 'setup' | 'repos' | 'appearance' | 'layout' | 'plugins' | 'intervals'

interface PluginDef {
  id: string
  label: string
  enabledKey: string
  /** Anchor id used for in-tab scroll-to from the sidebar. */
  anchor: string
}

const PLUGINS: PluginDef[] = [
  { id: 'pr', label: 'PR stats', enabledKey: KEYS.enabledPr, anchor: 'plugin-pr' },
  { id: 'forkable', label: 'Forkable', enabledKey: KEYS.enabledForkable, anchor: 'plugin-forkable' },
  { id: 'ubereats', label: 'UberEats', enabledKey: KEYS.enabledUbereats, anchor: 'plugin-ubereats' },
  { id: 'boba', label: 'Boba', enabledKey: KEYS.enabledBoba, anchor: 'plugin-boba' },
  { id: 'slack', label: 'Slack monitor', enabledKey: KEYS.enabledSlack, anchor: 'plugin-slack' },
  { id: 'github', label: 'GitHub poll', enabledKey: KEYS.enabledGhPoll, anchor: 'plugin-github' },
  { id: 'cert', label: 'Cert tracker', enabledKey: KEYS.enabledCert, anchor: 'plugin-cert' },
  // JIRA: now a periodic poller (services.jira_sync). Treated as a
  // first-class plugin -- shows up in Quick toggles + has its own
  // section header with an enable switch.
  { id: 'jira', label: 'JIRA (Tickets)', enabledKey: KEYS.enabledJira, anchor: 'plugin-jira' },
]


export function SettingsModal({
  onClose, initialTab = 'repos',
}: { onClose: () => void; initialTab?: RootTab }) {
  const [bag, setBag] = useState<SettingsBag | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<RootTab>(initialTab)
  const [pluginsExpanded, setPluginsExpanded] = useState(true)
  const [search, setSearch] = useState('')
  // Per-key save status so the user gets immediate feedback on save.
  const [saving, setSaving] = useState<Record<string, 'pending' | 'ok' | 'error'>>({})
  // The Plugins tab body is the scrolling region for sub-item links.
  const pluginsBodyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.listSettings()
      .then((r) => setBag(r.settings || {}))
      .catch(() => setError('Failed to load settings'))
  }, [])

  // ESC closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const save = useCallback(async (key: string, value: unknown) => {
    setSaving((s) => ({ ...s, [key]: 'pending' }))
    try {
      await api.setSetting(key, value)
      setBag((b) => ({ ...(b || {}), [key]: value }))
      setSaving((s) => ({ ...s, [key]: 'ok' }))
      // Plugin enable flags drive UI render decisions -- refresh the
      // shared cache so other components react immediately.
      if (key.startsWith('plugin.') && key.endsWith('.enabled')) {
        refreshPluginsEnabled()
      }
      window.setTimeout(() => {
        setSaving((s) => {
          if (s[key] !== 'ok') return s
          const next = { ...s }
          delete next[key]
          return next
        })
      }, 1200)
    } catch {
      setSaving((s) => ({ ...s, [key]: 'error' }))
    }
  }, [])

  const navigateToPlugin = useCallback((anchor: string) => {
    setTab('plugins')
    // Defer scroll until the Plugins tab has rendered.
    window.setTimeout(() => {
      const el = pluginsBodyRef.current?.querySelector(`#${anchor}`)
      if (el && pluginsBodyRef.current) {
        const top = (el as HTMLElement).offsetTop -
                    (pluginsBodyRef.current.offsetTop)
        pluginsBodyRef.current.scrollTo({ top, behavior: 'smooth' })
      }
    }, 50)
  }, [])

  return (
    <div
      data-testid="settings-backdrop"
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 9000,
        background: 'rgba(0,0,0,0.45)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        data-testid="settings-modal"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--card-bg)', border: '1px solid var(--border)',
          borderRadius: 8, width: 820, height: '78vh',
          display: 'flex', flexDirection: 'column',
          boxShadow: '0 12px 40px rgba(0,0,0,0.5)',
          color: 'var(--text)', fontFamily: 'inherit',
        }}
      >
        {/* Header: title + search + close */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12,
          padding: '10px 16px', borderBottom: '1px solid var(--border)',
        }}>
          <span style={{ fontSize: 13, fontWeight: 600, flexShrink: 0 }}>Settings</span>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search settings..."
            data-testid="settings-search"
            style={{
              flex: 1, padding: '4px 8px', fontSize: 11,
              background: 'var(--panel-bg)', color: 'var(--text)',
              border: '1px solid var(--border)', borderRadius: 4,
            }}
          />
          <button
            className="btn-action"
            onClick={onClose}
            style={{ fontSize: 11, flexShrink: 0 }}
          >Close</button>
        </div>

        {/* Body: sidebar + content */}
        <SearchFilterContext.Provider value={search}>
        <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
          <Sidebar
            tab={tab}
            onSelectTab={setTab}
            pluginsExpanded={pluginsExpanded}
            onTogglePlugins={() => setPluginsExpanded((v) => !v)}
            onSelectPlugin={navigateToPlugin}
          />
          <div style={{ flex: 1, overflowY: 'auto', padding: 16, fontSize: 12 }}
               ref={tab === 'plugins' ? pluginsBodyRef : undefined}>
            {error && <div style={{ color: 'var(--red)', fontSize: 11 }}>{error}</div>}
            {!bag && !error && <div style={{ color: 'var(--text-dim)' }}>Loading...</div>}
            {bag && tab === 'setup' && (
              <SetupTab />
            )}
            {bag && tab === 'repos' && (
              <ReposTab bag={bag} save={save} saving={saving} />
            )}
            {bag && tab === 'appearance' && (
              <AppearanceTab bag={bag} save={save} saving={saving} />
            )}
            {bag && tab === 'layout' && (
              <LayoutTab bag={bag} save={save} saving={saving} />
            )}
            {bag && tab === 'plugins' && (
              <PluginsTab bag={bag} save={save} saving={saving} />
            )}
            {bag && tab === 'intervals' && (
              <IntervalsTab bag={bag} save={save} saving={saving} />
            )}
          </div>
        </div>
        </SearchFilterContext.Provider>
      </div>
    </div>
  )
}


// ---- Sidebar ----

function Sidebar({
  tab, onSelectTab, pluginsExpanded, onTogglePlugins, onSelectPlugin,
}: {
  tab: RootTab
  onSelectTab: (t: RootTab) => void
  pluginsExpanded: boolean
  onTogglePlugins: () => void
  onSelectPlugin: (anchor: string) => void
}) {
  return (
    <div
      data-testid="settings-sidebar"
      style={{
        width: 180, flexShrink: 0,
        borderRight: '1px solid var(--border)',
        background: 'var(--panel-bg)',
        padding: '8px 0',
        overflowY: 'auto',
        fontSize: 12,
      }}
    >
      <SidebarItem
        label="Setup" active={tab === 'setup'}
        onClick={() => onSelectTab('setup')}
        testId="sidebar-setup"
      />
      <SidebarItem
        label="Repos" active={tab === 'repos'}
        onClick={() => onSelectTab('repos')}
        testId="sidebar-repos"
      />
      <SidebarItem
        label="Appearance" active={tab === 'appearance'}
        onClick={() => onSelectTab('appearance')}
        testId="sidebar-appearance"
      />
      <SidebarItem
        label="Layout" active={tab === 'layout'}
        onClick={() => onSelectTab('layout')}
        testId="sidebar-layout"
      />
      <SidebarItem
        label="Plugins" active={tab === 'plugins'}
        onClick={() => onSelectTab('plugins')}
        testId="sidebar-plugins"
        chevron={pluginsExpanded ? 'down' : 'right'}
        onChevronClick={onTogglePlugins}
      />
      <SidebarItem
        label="Intervals" active={tab === 'intervals'}
        onClick={() => onSelectTab('intervals')}
        testId="sidebar-intervals"
      />
      {pluginsExpanded && PLUGINS.map((p) => (
        <SidebarItem
          key={p.id}
          label={p.label}
          // Sub-items don't switch tab on their own -- they navigate
          // within the Plugins tab via scroll-to-anchor. Highlight only
          // when the parent tab is active.
          active={tab === 'plugins'}
          indent
          onClick={() => onSelectPlugin(p.anchor)}
          testId={`sidebar-plugin-${p.id}`}
        />
      ))}
    </div>
  )
}

function SidebarItem({
  label, active, onClick, testId, indent, chevron, onChevronClick,
}: {
  label: string
  active: boolean
  onClick: () => void
  testId?: string
  indent?: boolean
  chevron?: 'down' | 'right'
  onChevronClick?: () => void
}) {
  return (
    <div
      data-testid={testId}
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 4,
        padding: indent ? '4px 12px 4px 28px' : '6px 12px',
        cursor: 'pointer',
        background: active && !indent ? 'rgba(99,102,241,0.12)' : 'transparent',
        borderLeft: active && !indent ? '2px solid var(--accent)' : '2px solid transparent',
        color: active && !indent ? 'var(--accent)' : 'var(--text)',
        fontWeight: active && !indent ? 600 : 400,
        fontSize: indent ? 11 : 12,
      }}
    >
      <span style={{ flex: 1 }}>{label}</span>
      {chevron && (
        <span
          onClick={(e) => { e.stopPropagation(); onChevronClick?.() }}
          style={{
            fontSize: 8, color: 'var(--text-dim)',
            transform: chevron === 'down' ? 'rotate(0deg)' : 'rotate(-90deg)',
            transition: 'transform 0.15s',
            padding: '0 4px',
          }}
        >v</span>
      )}
    </div>
  )
}


// ---- Tab bodies ----

interface TabProps {
  bag: SettingsBag
  save: (key: string, value: unknown) => void
  saving: Record<string, 'pending' | 'ok' | 'error'>
}

/** Per-poller cadence row: label + current value + numeric input +
 * Save. The server clamps anything out of [min, max] to the defaults
 * for that poller, so we can be lenient on the client side. */
interface IntervalRow {
  key: string
  label: string
  defaultSeconds: number
  minSeconds: number
  description: string
}

const INTERVAL_ROWS: IntervalRow[] = [
  { key: KEYS.intervalGhPoll, label: 'GitHub poll',
    defaultSeconds: 10, minSeconds: 5,
    description: 'How often to fetch GitHub notifications. The Notifications API has its own throttle header; lowering below 10s rarely surfaces fresher data.' },
  { key: KEYS.intervalSlackMonitor, label: 'Slack monitor',
    defaultSeconds: 30, minSeconds: 10,
    description: 'How often to poll subscribed Slack channels for new messages. Slack rate-limits ~1 req/sec on user tokens.' },
  { key: KEYS.intervalUbereats, label: 'UberEats poll',
    defaultSeconds: 60, minSeconds: 30,
    description: 'Active only during the LA evening dinner window. Cheap no-op outside that window.' },
  { key: KEYS.intervalUsageRefresh, label: 'AI usage refresh',
    defaultSeconds: 120, minSeconds: 30,
    description: "How often to re-shell out to `the agent's usage` and emit usage.updated events." },
  { key: KEYS.intervalCertCheck, label: 'Cert tracker',
    defaultSeconds: 300, minSeconds: 30,
    description: 'How often to refresh cert status. Hits a real network endpoint per tick.' },
  { key: KEYS.jiraSyncInterval, label: 'JIRA sync',
    defaultSeconds: 300, minSeconds: 30,
    description: 'How often to refresh the JIRA tickets cache (powers the Tickets page).' },
]

function IntervalsTab({ bag, save, saving }: TabProps) {
  return (
    <>
      <Section title="Background poller cadences">
        <Note>
          Every poller is settings-driven. Values here are seconds; the
          server clamps to a per-poller minimum (e.g., JIRA enforces
          30s for rate-limit reasons). Empty / invalid values fall
          back to the default. Changes take effect on the next server
          restart.
        </Note>
      </Section>
      {INTERVAL_ROWS.map((row) => (
        <Section key={row.key} title={`${row.label} (${row.defaultSeconds}s default)`}>
          <Note>{row.description}</Note>
          <IntervalEditor
            settingKey={row.key}
            value={bag[row.key]}
            defaultSeconds={row.defaultSeconds}
            minSeconds={row.minSeconds}
            label={row.label}
            onSave={(v) => save(row.key, v)}
            status={saving[row.key]}
          />
        </Section>
      ))}
    </>
  )
}

function IntervalEditor({
  settingKey, value, defaultSeconds, minSeconds, label, onSave, status,
}: {
  settingKey: string
  value: unknown
  defaultSeconds: number
  minSeconds: number
  label: string
  onSave: (v: number) => Promise<void> | void
  status: 'pending' | 'ok' | 'error' | undefined
}) {
  // The `bag` value can be undefined (never overridden), a positive
  // number (override), or anything else (treated as default by the
  // server). Surface the override directly; show default as a
  // placeholder so users know what they get if they leave it blank.
  const initial = (typeof value === 'number' && value > 0)
    ? String(value) : ''
  const [local, setLocal] = useState<string>(initial)
  // Reset when the setting flips externally (e.g., another tab set it).
  useEffect(() => { setLocal(initial) }, [initial])

  const dirty = local !== initial
  const handleSave = async () => {
    if (local === '') {
      // Empty -> reset to default by writing the literal default value.
      await onSave(defaultSeconds)
      return
    }
    const parsed = parseFloat(local)
    if (!Number.isFinite(parsed) || parsed <= 0) return
    await onSave(parsed)
  }
  const slug = settingKey.replace(/[^a-z0-9]+/gi, '_')
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <input
        type="number"
        min={minSeconds}
        placeholder={`${defaultSeconds}`}
        value={local}
        data-testid={`settings-interval-input-${slug}`}
        onChange={(e) => setLocal(e.target.value)}
        style={{
          width: 80, padding: '2px 6px',
          border: '1px solid var(--border)', borderRadius: 4,
          background: 'var(--input-bg)', color: 'var(--text)',
          fontSize: 11,
        }}
      />
      <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>seconds</span>
      <button
        className="btn-action accent"
        data-testid={`settings-interval-save-${slug}`}
        disabled={!dirty || status === 'pending'}
        onClick={handleSave}
        style={{ fontSize: 11 }}
        aria-label={`Save ${label} interval`}
      >
        {status === 'pending' ? 'Saving...' : 'Save'}
      </button>
      {status === 'ok' && (
        <span style={{ fontSize: 11, color: 'var(--green)' }}>v</span>
      )}
      {status === 'error' && (
        <span style={{ fontSize: 11, color: 'var(--red)' }}>err</span>
      )}
      <span style={{ fontSize: 10, color: 'var(--text-faint)', marginLeft: 'auto' }}>
        min {minSeconds}s
      </span>
    </div>
  )
}


type SetupCheck = {
  id: string
  label: string
  ok: boolean
  detail: string
  hint: string
}

function SetupTab() {
  const [data, setData] = useState<{ all_ok: boolean; checks: SetupCheck[] } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await api.getSetupStatus())
    } catch {
      setError('Failed to load setup status')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  return (
    <>
      <Section title="Setup status">
        <Note>
          Eva shells out to the `gh` CLI for every GitHub call, so it
          needs gh installed and at least one authenticated account.
          The allow-list tells Eva which repos to track; account rules
          tell it which gh token to use when multiple are loaded.
        </Note>
        <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
          <button
            className="btn-action"
            onClick={refresh}
            disabled={loading}
            data-testid="settings-setup-refresh"
            style={{ fontSize: 10 }}
          >{loading ? 'Checking...' : 'Re-check'}</button>
          {data && (
            <span style={{
              fontSize: 11, fontWeight: 700,
              color: data.all_ok ? 'var(--green)' : 'var(--orange, #d6a300)',
            }}>
              {data.all_ok ? 'All checks passing' : 'Action needed'}
            </span>
          )}
        </div>
        {error && <div style={{ color: 'var(--red)', fontSize: 11 }}>{error}</div>}
        {data && data.checks.map((c) => <SetupCheckRow key={c.id} check={c} />)}
      </Section>
      <Section title="First-time setup">
        <Note>
          1. Install GitHub CLI: `brew install gh` (macOS) /
          `apt install gh` (Debian) / see https://cli.github.com.{'\n'}
          2. Authenticate: `gh auth login`. Repeat per account if you
          have separate work / personal accounts.{'\n'}
          3. Restart Eva so `~/.config/gh/hosts.yml` is picked up.{'\n'}
          4. Open Settings -&gt; Repos: add at least one repo to the
          allow-list (e.g. `acme/widgets` or `my-org/*`).{'\n'}
          5. If 2+ gh accounts are loaded, add at least one account
          rule (last rule with empty match = catch-all).{'\n'}
          6. Repos &gt; "Auto-detect forks" fills in fork-&gt;upstream
          for any real GitHub forks of allow-listed repos.
        </Note>
      </Section>
    </>
  )
}

function SetupCheckRow({ check }: { check: SetupCheck }) {
  return (
    <div
      data-testid={`setup-check-${check.id}`}
      style={{
        display: 'flex', flexDirection: 'column', gap: 2,
        padding: '6px 8px', marginBottom: 4,
        border: '1px solid var(--border)', borderRadius: 4,
        borderLeftWidth: 3,
        borderLeftColor: check.ok ? 'var(--green)' : 'var(--orange, #d6a300)',
      }}
    >
      <div style={{ display: 'flex', gap: 6, alignItems: 'baseline' }}>
        <span style={{
          fontSize: 11, fontWeight: 700,
          color: check.ok ? 'var(--green)' : 'var(--orange, #d6a300)',
        }}>{check.ok ? 'OK' : 'TODO'}</span>
        <span style={{ fontSize: 11, fontWeight: 600 }}>{check.label}</span>
      </div>
      <div style={{ fontSize: 10, color: 'var(--text-dim)' }}>{check.detail}</div>
      {!check.ok && check.hint && (
        <div style={{
          fontSize: 10, color: 'var(--text)',
          background: 'var(--panel-bg)', padding: '3px 6px',
          borderRadius: 3, marginTop: 2,
        }}>{check.hint}</div>
      )}
    </div>
  )
}


function ReposTab({ bag, save, saving }: TabProps) {
  // Repos uses the `/api/repos/resolved` endpoint as the source of
  // truth for editable values: that endpoint returns the *effective*
  // rules + fork map (settings table OR the hardcoded fallback in
  // `adapters/github.py`). Reading directly from `bag` would show
  // an empty form before the user has overridden anything.
  const [data, setData] = useState<{
    rules: string[]
    fork_to_upstream: Record<string, string>
    resolved: Array<{ repo: string; source: 'rule' | 'wildcard'; wildcard?: string; pr_count: number }>
  } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await api.resolveRepos()
      setData(r)
    } catch {
      setError('Failed to load repo rules')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // Re-fetch after a save so the resolved list reflects the edit.
  const saveAndRefresh = useCallback(async (key: string, value: unknown) => {
    save(key, value)
    // Defer slightly so the optimistic bag update lands first.
    window.setTimeout(refresh, 50)
  }, [save, refresh])

  // Use bag overrides if they exist (let the user see their saved
  // value mid-edit), else fall back to the resolved data.
  const rules = bag[KEYS.ghAllowedRepos] !== undefined
    ? asListOfStr(bag[KEYS.ghAllowedRepos])
    : (data?.rules ?? [])
  const forkMap = bag[KEYS.ghForkToUpstream] !== undefined
    ? asDict(bag[KEYS.ghForkToUpstream])
    : (data?.fork_to_upstream ?? {})

  return (
    <>
      <Section title="Rules">
        <Note>
          Each entry is `org/repo` (single repo) or `org/*` (wildcard
          for an entire org). PR sync, hooks, and the contributor-rank
          query all read this list. Empty -&gt; falls back to the
          hardcoded defaults in `adapters/github.py`. Changes take
          effect after a server restart.
        </Note>
        {!data && !error && (
          <div style={{ color: 'var(--text-dim)', fontSize: 11 }}>Loading...</div>
        )}
        {error && (
          <div style={{ color: 'var(--red)', fontSize: 11 }}>{error}</div>
        )}
        {data && (
          <ListField
            itemLabel="rule"
            value={rules}
            fields={[{ key: '', label: 'Repo (org/name or org/*)' }]}
            onSave={(v) => saveAndRefresh(KEYS.ghAllowedRepos, v)}
            status={saving[KEYS.ghAllowedRepos]}
          />
        )}
      </Section>
      <ResolvedReposSection
        data={data}
        loading={loading}
        error={error}
        onRefresh={refresh}
      />
      <Section title="Fork -&gt; upstream">
        <Note>
          Maps a personal fork to its upstream so PR-sync can normalize
          fork PRs back to the canonical repo. "Auto-detect" picks up
          real GitHub forks of allow-listed repos; for branches that
          live directly on an upstream (no GitHub fork relationship,
          e.g. monorepo branches) add the row by hand with "+ Add".
        </Note>
        {!data && !error && (
          <div style={{ color: 'var(--text-dim)', fontSize: 11 }}>Loading...</div>
        )}
        {data && (
          <>
            <ForkDetectButton
              currentMap={forkMap}
              onApply={(merged) => saveAndRefresh(KEYS.ghForkToUpstream, merged)}
            />
            <DictField
              value={forkMap}
              keyLabel="Fork (org/name)"
              valueLabel="Upstream (org/name)"
              onSave={(v) => saveAndRefresh(KEYS.ghForkToUpstream, v)}
              status={saving[KEYS.ghForkToUpstream]}
            />
          </>
        )}
      </Section>
      <Section title="GitHub account rules">
        <Note>
          Each rule maps a repo path substring to a `gh` CLI account.
          Rules are evaluated in order; first match wins. An empty
          match is a catch-all.  Empty list -&gt; falls back to the
          hardcoded heuristic in `adapters/github.py`. Changes take
          effect after a server restart.
        </Note>
        <ListField
          itemLabel="rule"
          value={asListOfDict(bag[KEYS.ghAccountRules])}
          fields={[
            { key: 'match', label: 'Match (substring of org/repo)' },
            { key: 'account', label: 'gh account login' },
          ]}
          onSave={(v) => save(KEYS.ghAccountRules, v)}
          status={saving[KEYS.ghAccountRules]}
        />
      </Section>
    </>
  )
}

/**
 * One-click "scan my gh accounts for forks of allow-listed repos".
 * Shows a preview of what would be added on success so the user can
 * confirm before merging into the existing fork map.
 */
function ForkDetectButton({
  currentMap, onApply,
}: {
  currentMap: Record<string, string>
  onApply: (merged: Record<string, string>) => void
}) {
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<{
    detected: Record<string, string>
    scanned_accounts: string[]
    errors: Array<{ account: string; message: string }>
  } | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const run = async () => {
    setBusy(true)
    setErr(null)
    try {
      const r = await api.detectForks()
      setPreview(r)
    } catch {
      setErr('Detection failed')
    } finally {
      setBusy(false)
    }
  }

  const apply = () => {
    if (!preview) return
    onApply({ ...currentMap, ...preview.detected })
    setPreview(null)
  }

  const newEntries = preview
    ? Object.entries(preview.detected).filter(([k]) => currentMap[k] === undefined)
    : []

  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <button
          className="btn-action"
          onClick={run}
          disabled={busy}
          data-testid="settings-fork-detect"
          style={{ fontSize: 10 }}
        >{busy ? 'Scanning...' : 'Auto-detect forks'}</button>
        {preview && (
          <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>
            Scanned {preview.scanned_accounts.length} account(s);{' '}
            {Object.keys(preview.detected).length} match(es),{' '}
            {newEntries.length} new
          </span>
        )}
      </div>
      {err && <div style={{ color: 'var(--red)', fontSize: 10, marginTop: 4 }}>{err}</div>}
      {preview && preview.errors.length > 0 && (
        <div style={{ fontSize: 10, color: 'var(--red)', marginTop: 4 }}>
          {preview.errors.map((e, i) => (
            <div key={i}>{e.account}: {e.message}</div>
          ))}
        </div>
      )}
      {preview && newEntries.length > 0 && (
        <div style={{
          marginTop: 6, border: '1px solid var(--border)', borderRadius: 4,
          padding: '4px 8px', fontSize: 10,
        }}>
          {newEntries.map(([fork, upstream]) => (
            <div key={fork}>
              <code>{fork}</code> -&gt; <code>{upstream}</code>
            </div>
          ))}
          <button
            className="btn-action"
            onClick={apply}
            data-testid="settings-fork-detect-apply"
            style={{ fontSize: 10, marginTop: 4 }}
          >Add {newEntries.length} to map</button>
        </div>
      )}
      {preview && newEntries.length === 0 && Object.keys(preview.detected).length === 0 && (
        <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 4 }}>
          No forks of allow-listed repos found.
        </div>
      )}
    </div>
  )
}

/**
 * Live list of `org/repo` pairs the rules currently match. For
 * explicit `org/repo` rules the row source is `rule`; for `org/*`
 * wildcards we surface every repo under that org we've seen at least
 * one PR for (the `wildcard` field carries the originating rule).
 *
 * Data is owned by the parent ReposTab so a single fetch hydrates
 * both the editable rules form AND this read-only resolved view.
 */
function ResolvedReposSection({
  data, loading, error, onRefresh,
}: {
  data: {
    resolved: Array<{ repo: string; source: 'rule' | 'wildcard'; wildcard?: string; pr_count: number }>
  } | null
  loading: boolean
  error: string | null
  onRefresh: () => void
}) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{
        display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
        marginBottom: 6,
      }}>
        <span style={{
          fontSize: 11, fontWeight: 700, color: 'var(--accent)',
          textTransform: 'uppercase', letterSpacing: 0.6,
        }}>Resolved repos</span>
        <button
          className="btn-action"
          onClick={onRefresh}
          disabled={loading}
          data-testid="settings-resolved-refresh"
          style={{ fontSize: 10 }}
        >{loading ? 'Loading...' : 'Refresh'}</button>
      </div>
      <Note>
        Live expansion of the rules above. Wildcards (`org/*`) are
        materialised from repos found in the local PR mirror, so the
        list reflects what the system has actually exercised.
      </Note>
      {error && <div style={{ color: 'var(--red)', fontSize: 11 }}>{error}</div>}
      {data && data.resolved.length === 0 && !error && (
        <div style={{ fontSize: 10, color: 'var(--text-dim)', fontStyle: 'italic' }}>
          No matching repos yet -- sync some PRs to see this populate.
        </div>
      )}
      {data && data.resolved.length > 0 && (
        <div data-testid="settings-resolved-list" style={{
          border: '1px solid var(--border)', borderRadius: 4,
          maxHeight: 200, overflowY: 'auto',
        }}>
          <div style={{
            display: 'grid',
            gridTemplateColumns: '1fr 110px 60px',
            gap: 6, padding: '4px 8px',
            background: 'var(--panel-bg)',
            fontSize: 9, fontWeight: 700,
            color: 'var(--text-dim)',
            textTransform: 'uppercase', letterSpacing: 0.5,
            borderBottom: '1px solid var(--border)',
            position: 'sticky', top: 0,
          }}>
            <span>Repo</span>
            <span>Source</span>
            <span style={{ textAlign: 'right' }}>PRs</span>
          </div>
          {data.resolved.map((r) => (
            <div
              key={r.repo}
              data-testid={`settings-resolved-row-${r.repo}`}
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 110px 60px',
                gap: 6, padding: '4px 8px', fontSize: 11,
                borderBottom: '1px solid var(--border)',
                alignItems: 'center',
              }}>
              <span style={{
                fontFamily: 'monospace', overflow: 'hidden',
                textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>{r.repo}</span>
              <span style={{
                fontSize: 9, color: r.source === 'rule' ? 'var(--accent)' : 'var(--text-dim)',
              }} title={r.wildcard ? `via ${r.wildcard}` : 'explicit rule'}>
                {r.source === 'wildcard' ? r.wildcard : 'rule'}
              </span>
              <span style={{
                textAlign: 'right', fontFamily: 'monospace',
                color: 'var(--text)',
              }}>{r.pr_count}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function AppearanceTab({ bag, save, saving }: {
  bag: SettingsBag
  save: (key: string, value: unknown) => Promise<void>
  saving: Record<string, 'pending' | 'ok' | 'error'>
}) {
  const {
    theme, toggle, setTheme,
    fontScale, setFontScale,
    density, setDensity,
    fontFamily, setFontFamily,
    brightness, setBrightness,
  } = useTheme()
  return (
    <>
      <Section title="Palette">
        <Note>
          Pick a colour palette. Dark / Light are the originals;
          Solarized / Nord / High-contrast offer alternatives that
          tune the accent and background ramps. Stored locally so a
          fresh install starts on Dark.
        </Note>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ width: 110, fontSize: 11, color: 'var(--text-dim)' }}>
            Active palette
          </span>
          <span style={{ fontSize: 11, fontWeight: 600 }}>{theme}</span>
          <button
            className="btn-action"
            data-testid="settings-theme-toggle"
            onClick={toggle}
            style={{ fontSize: 10, marginLeft: 8 }}
          >Toggle dark/light</button>
        </div>
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6,
          marginTop: 8,
        }}>
          {THEMES.map((t) => (
            <ThemeSwatch
              key={t}
              name={t}
              active={theme === t}
              onClick={() => setTheme(t)}
            />
          ))}
        </div>
      </Section>
      <Section title="Font scale">
        <Note>
          Multiplies the base font size everywhere. Useful on a 4K
          display or when sharing the screen. Saved to localStorage.
        </Note>
        <SegmentedPicker<FontScale>
          options={FONT_SCALES}
          value={fontScale}
          onChange={setFontScale}
          format={(s) => `${Math.round(s * 100)}%`}
          testIdPrefix="settings-font-scale"
        />
      </Section>
      <Section title="Density">
        <Note>
          Controls vertical spacing in lists, cards, and the sidebar.
          Compact fits more on screen; spacious is easier on the eyes.
        </Note>
        <SegmentedPicker<Density>
          options={DENSITIES}
          value={density}
          onChange={setDensity}
          testIdPrefix="settings-density"
        />
      </Section>
      <Section title="Font family">
        <Note>
          Sets the typeface used everywhere except code blocks (which
          stay monospace). All stacks have OS fallbacks so no install
          is required. Saved to localStorage.
        </Note>
        <SegmentedPicker<FontFamily>
          options={FONT_FAMILIES}
          value={fontFamily}
          onChange={setFontFamily}
          testIdPrefix="settings-font-family"
        />
      </Section>
      <Section title="Brightness">
        <Note>
          Applies a CSS `filter: brightness()` to the whole UI on top
          of the chosen palette. Useful for a brighter screen at night
          or a dimmer one in glare. 100% is the baseline.
        </Note>
        <SegmentedPicker<Brightness>
          options={BRIGHTNESSES}
          value={brightness}
          onChange={setBrightness}
          format={(b) => `${Math.round(b * 100)}%`}
          testIdPrefix="settings-brightness"
        />
      </Section>
    </>
  )
}


/** Layout: pane ratios + fetch horizons across the various pages. */
function LayoutTab({ bag, save, saving }: {
  bag: SettingsBag
  save: (key: string, value: unknown) => Promise<void>
  saving: Record<string, 'pending' | 'ok' | 'error'>
}) {
  return (
    <>
      <Section title="Reviews 3-pane ratios">
        <Note>
          Width percentages for the queue / card / detail panes on the
          Reviews page. Default 25 / 35 / 40. Out-of-range values fall
          back to the default; leave any field blank to reset.
        </Note>
        <RatiosEditor
          labels={['Queue', 'Card', 'Detail']}
          defaults={[25, 35, 40]}
          testIdSlug="reviews"
          value={asRatios(bag[KEYS.reviewsRatios], [25, 35, 40])}
          onSave={(v) => save(KEYS.reviewsRatios, v)}
          status={saving[KEYS.reviewsRatios]}
        />
      </Section>
      <Section title="Cron Jobs 2-pane ratios">
        <Note>
          Width percentages for the list / detail panes on the Cron
          Jobs page. Default 40 / 60.
        </Note>
        <RatiosEditor
          labels={['List', 'Detail']}
          defaults={[40, 60]}
          testIdSlug="cron-jobs"
          value={asRatios(bag[KEYS.cronJobsRatios], [40, 60])}
          onSave={(v) => save(KEYS.cronJobsRatios, v)}
          status={saving[KEYS.cronJobsRatios]}
        />
      </Section>
      <Section title="All PRs 3-pane ratios">
        <Note>
          Width percentages for the list / task / detail panes on the
          All PRs page (active when a PR with an associated task is
          selected). Default 25 / 40 / 35.
        </Note>
        <RatiosEditor
          labels={['List', 'Task', 'Detail']}
          defaults={[25, 40, 35]}
          testIdSlug="prs"
          value={asRatios(bag[KEYS.prsRatios], [25, 40, 35])}
          onSave={(v) => save(KEYS.prsRatios, v)}
          status={saving[KEYS.prsRatios]}
        />
      </Section>
      <Section title="All Sessions 3-pane ratios">
        <Note>
          Width percentages for the list / detail / task panes on the
          All Sessions page (active when a PR is selected). Default
          25 / 40 / 35.
        </Note>
        <RatiosEditor
          labels={['List', 'Detail', 'Task']}
          defaults={[25, 40, 35]}
          testIdSlug="sessions"
          value={asRatios(bag[KEYS.sessionsRatios], [25, 40, 35])}
          onSave={(v) => save(KEYS.sessionsRatios, v)}
          status={saving[KEYS.sessionsRatios]}
        />
      </Section>
      <Section title="Tickets 3-pane ratios">
        <Note>
          Width percentages for the queue / session / detail panes on
          the Tickets page (active when a ticket is selected). Default
          30 / 35 / 35.
        </Note>
        <RatiosEditor
          labels={['Queue', 'Session', 'Detail']}
          defaults={[30, 35, 35]}
          testIdSlug="tickets"
          value={asRatios(bag[KEYS.ticketsRatios], [30, 35, 35])}
          onSave={(v) => save(KEYS.ticketsRatios, v)}
          status={saving[KEYS.ticketsRatios]}
        />
      </Section>
      <Section title="WorkLog horizons">
        <Note>
          How many recent days to show in day mode (7 - 365, default
          60), and how many weeks of standup meetings to surface in
          standup mode (1 - 52, default 8). Out-of-range values fall
          back to the default.
        </Note>
        <BoundedNumberEditor
          settingKey={KEYS.worklogDays}
          label="Day-mode days"
          unit="days"
          value={bag[KEYS.worklogDays]}
          defaultValue={60}
          min={7}
          max={365}
          onSave={(v) => save(KEYS.worklogDays, v)}
          status={saving[KEYS.worklogDays]}
        />
        <BoundedNumberEditor
          settingKey={KEYS.worklogStandupWeeks}
          label="Standup-mode weeks"
          unit="weeks"
          value={bag[KEYS.worklogStandupWeeks]}
          defaultValue={8}
          min={1}
          max={52}
          onSave={(v) => save(KEYS.worklogStandupWeeks, v)}
          status={saving[KEYS.worklogStandupWeeks]}
        />
      </Section>
      <Section title="Cron Jobs history">
        <Note>
          How many recent runs to show in each JobCard's history strip
          (5 - 500, default 20). The route clamps to the same bounds.
        </Note>
        <BoundedNumberEditor
          settingKey={KEYS.cronJobsRunsHistoryLimit}
          label="Runs history limit"
          unit="runs"
          value={bag[KEYS.cronJobsRunsHistoryLimit]}
          defaultValue={20}
          min={5}
          max={500}
          onSave={(v) => save(KEYS.cronJobsRunsHistoryLimit, v)}
          status={saving[KEYS.cronJobsRunsHistoryLimit]}
        />
      </Section>
      <Section title="Tickets queue">
        <Note>
          How many cached tickets to load on the Tickets page queue
          pane (10 - 1000, default 100). Above 1000 the route clamps.
        </Note>
        <BoundedNumberEditor
          settingKey={KEYS.ticketsListLimit}
          label="Queue list limit"
          unit="tickets"
          value={bag[KEYS.ticketsListLimit]}
          defaultValue={100}
          min={10}
          max={1000}
          onSave={(v) => save(KEYS.ticketsListLimit, v)}
          status={saving[KEYS.ticketsListLimit]}
        />
      </Section>
      <Section title="Reviews sync horizon">
        <Note>
          Per-account `gh search prs --limit` used by the Reviews
          page sync (10 - 200, default 50). Lift this on accounts
          with very busy review queues; trim it on quieter ones.
        </Note>
        <BoundedNumberEditor
          settingKey={KEYS.reviewsSyncSearchLimit}
          label="Sync search limit"
          unit="PRs"
          value={bag[KEYS.reviewsSyncSearchLimit]}
          defaultValue={50}
          min={10}
          max={200}
          onSave={(v) => save(KEYS.reviewsSyncSearchLimit, v)}
          status={saving[KEYS.reviewsSyncSearchLimit]}
        />
      </Section>
    </>
  )
}


/** Generic bounded-integer setting editor. Reused for any single-int
 * scalar (WorkLog horizons today; future scalars can drop in here
 * instead of inventing yet another widget). The unit suffix is
 * decorative -- the saved value is always a clean integer. */
function BoundedNumberEditor({
  settingKey, label, unit, value, defaultValue, min, max, onSave, status,
}: {
  settingKey: string
  label: string
  unit: string
  value: unknown
  defaultValue: number
  min: number
  max: number
  onSave: (v: number) => Promise<void> | void
  status: 'pending' | 'ok' | 'error' | undefined
}) {
  const initial = (typeof value === 'number'
                   && Number.isInteger(value)
                   && value >= min && value <= max)
    ? String(value) : ''
  const [local, setLocal] = useState<string>(initial)
  useEffect(() => { setLocal(initial) }, [initial])
  const dirty = local !== initial
  const parsed = local.trim() === '' ? null : parseInt(local, 10)
  const valid = parsed === null
    || (Number.isInteger(parsed) && parsed >= min && parsed <= max)
  const slug = settingKey.replace(/[^a-z0-9]+/gi, '-')
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
      <span style={{ fontSize: 12, color: 'var(--text-dim)', minWidth: 160 }}>
        {label}
      </span>
      <input
        data-testid={`settings-bounded-${slug}`}
        type="number"
        min={min}
        max={max}
        placeholder={String(defaultValue)}
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        style={{
          width: 80, fontSize: 12, padding: '2px 6px',
          background: 'var(--input-bg)',
          border: `1px solid ${valid ? 'var(--border)' : 'var(--red)'}`,
          color: 'var(--text)', borderRadius: 4,
        }}
      />
      <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>{unit}</span>
      <button
        className="btn-action"
        disabled={!dirty || !valid || status === 'pending'}
        onClick={() => {
          if (!valid) return
          if (parsed === null) onSave(defaultValue)
          else onSave(parsed)
        }}
        style={{ fontSize: 11, padding: '2px 8px' }}
        data-testid={`settings-bounded-save-${slug}`}
      >
        {status === 'pending' ? 'Saving...' : 'Save'}
      </button>
      {status === 'ok' && (
        <span style={{ fontSize: 10, color: 'var(--green)' }}>saved</span>
      )}
      {status === 'error' && (
        <span style={{ fontSize: 10, color: 'var(--red)' }}>failed</span>
      )}
    </div>
  )
}

/** Validate a settings-bag entry as a positive-number ratio list of
 * length `defaults.length`, otherwise fall back to defaults. Mirrors
 * the backend `_get_layout_ratios` contract so what the user sees in
 * the editor is what the backend will actually use. */
function asRatios(v: unknown, defaults: number[]): number[] {
  if (Array.isArray(v) && v.length === defaults.length
      && v.every((x) => typeof x === 'number' && x > 0)) {
    return v.map((x) => x as number)
  }
  return [...defaults]
}

/** Generic N-pane ratio editor. Label count drives input count, so
 * the same component handles 2-pane (Cron Jobs), 3-pane (Reviews,
 * Benchmarks), and any future N-pane page without duplication. */
function RatiosEditor({
  value, onSave, status, labels, defaults, testIdSlug,
}: {
  value: number[]
  onSave: (v: number[]) => Promise<void> | void
  status: 'pending' | 'ok' | 'error' | undefined
  labels: string[]
  defaults: number[]
  testIdSlug: string
}) {
  const baseline = value.length === labels.length ? value : defaults
  const [local, setLocal] = useState<string[]>(baseline.map(String))
  // Reset local edits when the underlying value flips (e.g., after
  // another tab loads the bag).
  const baselineKey = baseline.join(',')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setLocal(baseline.map(String)) }, [baselineKey])
  const onChange = (i: number, raw: string) => {
    const next = [...local]
    next[i] = raw
    setLocal(next)
  }
  const dirty = local.some((s, i) => s !== String(baseline[i]))
  const handleSave = async () => {
    const parsed = local.map((s) => parseFloat(s))
    if (parsed.length !== labels.length
        || parsed.some((n) => !Number.isFinite(n) || n <= 0)) {
      return
    }
    await onSave(parsed)
  }
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      {labels.map((label, i) => (
        <label key={label} style={{ fontSize: 11, color: 'var(--text-dim)' }}>
          {label}
          <input
            type="number"
            min="1"
            value={local[i] ?? ''}
            data-testid={`settings-${testIdSlug}-ratio-${label.toLowerCase()}`}
            onChange={(e) => onChange(i, e.target.value)}
            style={{
              width: 60, marginLeft: 6, padding: '2px 6px',
              border: '1px solid var(--border)', borderRadius: 4,
              background: 'var(--input-bg)', color: 'var(--text)',
              fontSize: 11,
            }}
          />
        </label>
      ))}
      <button
        className="btn-action accent"
        data-testid={`settings-${testIdSlug}-ratios-save`}
        disabled={!dirty || status === 'pending'}
        onClick={handleSave}
        style={{ fontSize: 11 }}
      >
        {status === 'pending' ? 'Saving...' : 'Save'}
      </button>
      {status === 'ok' && (
        <span style={{ fontSize: 11, color: 'var(--green)' }}>v</span>
      )}
      {status === 'error' && (
        <span style={{ fontSize: 11, color: 'var(--red)' }}>err</span>
      )}
    </div>
  )
}


/** Mini swatch button: shows the palette's accent + bg colors so users
 * can preview without applying. Reads CSS vars per data-theme via an
 * inline style override so the swatch displays the target theme even
 * while a different one is active. */
function ThemeSwatch({
  name, active, onClick,
}: { name: Theme; active: boolean; onClick: () => void }) {
  return (
    <button
      data-testid={`settings-theme-${name}`}
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '6px 8px',
        background: 'var(--panel-bg)',
        border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
        borderRadius: 6, cursor: 'pointer',
        color: 'var(--text)', fontSize: 11,
        textAlign: 'left',
      }}
    >
      <ThemePreviewSwatches name={name} />
      <span style={{ flex: 1, textTransform: 'capitalize' }}>
        {name.replace(/-/g, ' ')}
      </span>
      {active && <span style={{ fontSize: 10, color: 'var(--accent)' }}>v</span>}
    </button>
  )
}

/** Renders 3 small colour squares pulled from the target palette so
 * users can compare side by side. We can't read another theme's CSS
 * vars without applying, so we hardcode a small reference table -- the
 * actual theme is the source of truth. */
function ThemePreviewSwatches({ name }: { name: Theme }) {
  const swatches: Record<Theme, [string, string, string]> = {
    'dark': ['#0f0f17', '#1e1e2e', '#6366f1'],
    'light': ['#f5f5f7', '#ffffff', '#6366f1'],
    'solarized-dark': ['#002b36', '#073642', '#2aa198'],
    'solarized-light': ['#fdf6e3', '#ffffff', '#2aa198'],
    'high-contrast': ['#000000', '#111111', '#00d4ff'],
    'nord': ['#2e3440', '#3b4252', '#88c0d0'],
    'rose-pine-moon': ['#232136', '#2a273f', '#c4a7e7'],
    'github-dimmed': ['#22272e', '#2d333b', '#539bf5'],
    'cobalt': ['#193549', '#1a3a52', '#ffc600'],
    'tokyo-night': ['#1a1b26', '#24283b', '#7aa2f7'],
    'catppuccin-mocha': ['#1e1e2e', '#313244', '#cba6f7'],
    'gruvbox-dark': ['#282828', '#3c3836', '#d79921'],
    'everforest-dark': ['#2d353b', '#374145', '#a7c080'],
    'dracula': ['#282a36', '#343746', '#bd93f9'],
    'crimson-dark': ['#0b0e13', '#1a1f2a', '#ff3621'],
    'slate-pro': ['#f4f6fa', '#ffffff', '#0e9488'],
    'midnight-violet': ['#0a0a14', '#161624', '#7c5cff'],
    'paper-warm': ['#faf6ef', '#ffffff', '#c2410c'],
  }
  const [bg, card, accent] = swatches[name]
  return (
    <span style={{ display: 'inline-flex', gap: 2, flexShrink: 0 }}>
      <span style={{ width: 12, height: 12, borderRadius: 2, background: bg, border: '1px solid #0004' }} />
      <span style={{ width: 12, height: 12, borderRadius: 2, background: card, border: '1px solid #0004' }} />
      <span style={{ width: 12, height: 12, borderRadius: 2, background: accent, border: '1px solid #0004' }} />
    </span>
  )
}

/** Reusable segmented control. Renders one button per option. */
function SegmentedPicker<T extends string | number>({
  options, value, onChange, format, testIdPrefix,
}: {
  options: readonly T[]
  value: T
  onChange: (v: T) => void
  format?: (v: T) => string
  testIdPrefix?: string
}) {
  return (
    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
      {options.map((o) => {
        const active = o === value
        return (
          <button
            key={String(o)}
            data-testid={testIdPrefix ? `${testIdPrefix}-${o}` : undefined}
            onClick={() => onChange(o)}
            className={active ? 'btn-action accent' : 'btn-action'}
            style={{ fontSize: 10, textTransform: 'capitalize' }}
          >{format ? format(o) : String(o)}</button>
        )
      })}
    </div>
  )
}

function PluginsTab({ bag, save, saving }: TabProps) {
  return (
    <>
      {/* Quick-toggle list at the top: enable/disable any plugin in
          one click without scrolling to its section. */}
      <Section title="Quick toggles">
        <Note>
          Master switches. Disabled plugins skip their work without
          affecting the rest of the app -- toggle to silence a noisy
          plugin or pause polling.
        </Note>
        {/* Only plugins with an enabledKey appear here; JIRA opts out
            because it isn't a poller, just on-demand sync. */}
        {PLUGINS.filter((p) => p.enabledKey).map((p) => (
          <BoolField
            key={p.id}
            label={p.label}
            // Default enabled when the key is absent -- matches backend
            // `is_plugin_enabled` semantics.
            value={asBoolDefault(bag[p.enabledKey], true)}
            onSave={(v) => save(p.enabledKey, v)}
            status={saving[p.enabledKey]}
          />
        ))}
      </Section>

      <PluginSection
        anchor="plugin-pr" title="PR stats"
        enabledKey={KEYS.enabledPr}
        bag={bag} save={save} saving={saving}
      >
        <Note>
          Sidebar plugin showing open-PR counts, fiscal-quarter trends,
          and contributor rank. Uses `gh` CLI -- disabling stops the
          render but doesn't pause the underlying GitHub poll (see
          GitHub poll section).
        </Note>
      </PluginSection>

      <PluginSection
        anchor="plugin-forkable" title="Forkable"
        enabledKey={KEYS.enabledForkable}
        bag={bag} save={save} saving={saving}
      >
        <SecretField
          label="Cookie"
          value={asString(bag[KEYS.forkableCookie])}
          onSave={(v) => save(KEYS.forkableCookie, v)}
          status={saving[KEYS.forkableCookie]}
        />
      </PluginSection>

      <PluginSection
        anchor="plugin-ubereats" title="UberEats"
        enabledKey={KEYS.enabledUbereats}
        bag={bag} save={save} saving={saving}
      >
        <SecretField label="DID" value={asString(bag[KEYS.ubereatsDid])}
          onSave={(v) => save(KEYS.ubereatsDid, v)} status={saving[KEYS.ubereatsDid]} />
        <SecretField label="JWT" value={asString(bag[KEYS.ubereatsJwt])}
          onSave={(v) => save(KEYS.ubereatsJwt, v)} status={saving[KEYS.ubereatsJwt]} />
        <SecretField label="SID" value={asString(bag[KEYS.ubereatsSid])}
          onSave={(v) => save(KEYS.ubereatsSid, v)} status={saving[KEYS.ubereatsSid]} />
        <NumberField label="Poll interval (s)"
          value={asNumber(bag[KEYS.intervalUbereats])}
          onSave={(v) => save(KEYS.intervalUbereats, v)}
          status={saving[KEYS.intervalUbereats]} />
      </PluginSection>

      <PluginSection
        anchor="plugin-boba" title="Boba"
        enabledKey={KEYS.enabledBoba}
        bag={bag} save={save} saving={saving}
      >
        <TextField label="Channel ID" value={asString(bag[KEYS.bobaChannel])}
          onSave={(v) => save(KEYS.bobaChannel, v)} status={saving[KEYS.bobaChannel]} />
        <TextField label="User filter" value={asString(bag[KEYS.bobaUserFilter])}
          onSave={(v) => save(KEYS.bobaUserFilter, v)} status={saving[KEYS.bobaUserFilter]} />
        <BoolField label="Test mode" value={asBool(bag[KEYS.bobaTestMode])}
          onSave={(v) => save(KEYS.bobaTestMode, v)} status={saving[KEYS.bobaTestMode]} />
      </PluginSection>

      <PluginSection
        anchor="plugin-slack" title="Slack monitor"
        enabledKey={KEYS.enabledSlack}
        bag={bag} save={save} saving={saving}
      >
        <ListField
          itemLabel="channel"
          value={asListOfDict(bag[KEYS.slackChannels])}
          fields={[
            { key: 'id', label: 'Channel ID' },
            { key: 'name', label: 'Name' },
          ]}
          onSave={(v) => save(KEYS.slackChannels, v)}
          status={saving[KEYS.slackChannels]}
        />
      </PluginSection>

      <PluginSection
        anchor="plugin-github" title="GitHub poll"
        enabledKey={KEYS.enabledGhPoll}
        bag={bag} save={save} saving={saving}
      >
        <Note>Notification poller for the configured repos.</Note>
        <NumberField label="Poll interval (s)"
          value={asNumber(bag[KEYS.intervalGhPoll])}
          onSave={(v) => save(KEYS.intervalGhPoll, v)}
          status={saving[KEYS.intervalGhPoll]} />
      </PluginSection>

      <PluginSection
        anchor="plugin-cert" title="Cert tracker"
        enabledKey={KEYS.enabledCert}
        bag={bag} save={save} saving={saving}
      >
        <Note>
          Cert providers are registered in code (extensions ship them
          via {'`<extension>/src/certs/`'}); DB-driven cert config is not
          wired yet.
        </Note>
      </PluginSection>

      <PluginSection
        anchor="plugin-jira" title="JIRA (Tickets)"
        enabledKey={KEYS.enabledJira}
        bag={bag} save={save} saving={saving}
      >
        <Note>
          Powers the <strong>Tickets</strong> page. Configure one or
          more JIRA instances (e.g. an OSS Apache server + a
          corporate Atlassian Cloud). Disabling here pauses the
          periodic background sync across every instance.
        </Note>
        <JiraInstancesEditor />
        <NumberField
          label="Sync interval (s)"
          value={asNumber(bag[KEYS.jiraSyncInterval])}
          onSave={(v) => save(KEYS.jiraSyncInterval, v)}
          status={saving[KEYS.jiraSyncInterval]}
        />
      </PluginSection>
    </>
  )
}


/**
 * List editor for multiple JIRA instances. Reads the current state
 * from `/api/tickets` (which returns instances with redacted
 * tokens), uses `/api/tickets/instances/{name}` PUT/DELETE for
 * mutations.
 *
 * `has_token` from the server tells us whether a stored token
 * exists; the UI never shows the actual token (security) but does
 * show a "(token saved)" hint so the user knows not to re-paste.
 */
function JiraInstancesEditor() {
  const [instances, setInstances] = useState<JiraInstance[]>([])
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const r = await api.listTickets()
      setInstances(r.instances || [])
      setLoaded(true)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'load failed')
      setLoaded(true)
    }
  }, [])
  useEffect(() => { refresh() }, [refresh])

  const onAdd = useCallback(() => {
    // Optimistic local stub; user fills in name + token before save.
    setInstances((prev) => [
      ...prev,
      {
        name: '',
        base_url: '',
        auth_type: 'basic',
        email: '',
        jql: '',
        has_token: false,
      },
    ])
  }, [])

  const onDelete = useCallback(async (name: string, idx: number) => {
    if (!name) {
      // Never persisted -- just drop the local stub.
      setInstances((prev) => prev.filter((_, i) => i !== idx))
      return
    }
    if (!window.confirm(`Delete JIRA instance '${name}'? Cached tickets for this instance are also removed.`)) {
      return
    }
    try {
      await api.deleteJiraInstance(name)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'delete failed')
    }
  }, [refresh])

  if (!loaded) {
    return <div style={{ color: 'var(--text-dim)', fontSize: 11 }}>Loading...</div>
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {error && (
        <div style={{ color: 'var(--red)', fontSize: 11 }}>{error}</div>
      )}
      {instances.length === 0 && (
        <div style={{ color: 'var(--text-dim)', fontSize: 11, fontStyle: 'italic' }}>
          No JIRA instances configured -- click "Add instance" to start.
        </div>
      )}
      {instances.map((inst, idx) => (
        <JiraInstanceCard
          key={inst.name || `__new-${idx}`}
          instance={inst}
          onSaved={refresh}
          onDelete={() => onDelete(inst.name, idx)}
        />
      ))}
      <button
        className="btn-action accent"
        onClick={onAdd}
        data-testid="settings-jira-add-instance"
        style={{ fontSize: 10, alignSelf: 'flex-start' }}
      >+ Add instance</button>
    </div>
  )
}


function JiraInstanceCard({
  instance, onSaved, onDelete,
}: {
  instance: JiraInstance
  onSaved: () => void
  onDelete: () => void
}) {
  const [draft, setDraft] = useState({
    name: instance.name,
    base_url: instance.base_url,
    auth_type: instance.auth_type,
    email: instance.email,
    api_token: '',  // never echoed back from server; user re-pastes only when changing
    jql: instance.jql,
  })
  const [saving, setSaving] = useState<'idle' | 'pending' | 'ok' | 'error'>('idle')
  const [errMsg, setErrMsg] = useState<string | null>(null)
  const update = <K extends keyof typeof draft>(k: K, v: typeof draft[K]) =>
    setDraft((d) => ({ ...d, [k]: v }))

  const save = useCallback(async () => {
    setSaving('pending')
    setErrMsg(null)
    try {
      // Validation: name + base_url + token are required by the
      // backend; surface the error inline so the user doesn't have
      // to read network panel.
      if (!draft.name.trim()) throw new Error('name is required')
      if (!draft.base_url.trim()) throw new Error('base_url is required')
      // If a token already exists and the user didn't paste a new
      // one, send the same name back without changing the token by
      // omitting it (the backend requires a token, so this means we
      // re-send a placeholder — we just send empty and instruct the
      // backend to keep the existing). To keep this simple we
      // require a re-paste on edit. UI hint below makes this clear.
      if (!draft.api_token.trim()) {
        throw new Error('api_token is required (re-paste to save changes)')
      }
      await api.upsertJiraInstance({
        name: draft.name.trim(),
        base_url: draft.base_url.trim(),
        auth_type: draft.auth_type,
        email: draft.email.trim(),
        api_token: draft.api_token,
        jql: draft.jql.trim(),
      })
      setSaving('ok')
      onSaved()
      // Clear the in-memory token so it doesn't sit in React state.
      setDraft((d) => ({ ...d, api_token: '' }))
      window.setTimeout(() => setSaving('idle'), 1200)
    } catch (e) {
      setSaving('error')
      setErrMsg(e instanceof Error ? e.message : 'save failed')
    }
  }, [draft, onSaved])

  return (
    <div
      data-testid={`settings-jira-instance-${instance.name || 'new'}`}
      style={{
        border: '1px solid var(--border)',
        borderRadius: 6,
        padding: 8,
        background: 'var(--panel-bg)',
        display: 'flex', flexDirection: 'column', gap: 4,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent)' }}>
          {instance.name || '(new instance)'}
        </span>
        {instance.has_token && (
          <span style={{ fontSize: 9, color: 'var(--green)' }}>(token saved)</span>
        )}
        <button
          className="btn-action"
          onClick={onDelete}
          data-testid={`settings-jira-delete-${instance.name || 'new'}`}
          style={{
            fontSize: 9, padding: '1px 6px', marginLeft: 'auto',
            color: 'var(--red)',
          }}
        >Delete</button>
      </div>
      <InlineField label="Name" value={draft.name}
        onChange={(v) => update('name', v)}
        placeholder="primary / my-company / ..." />
      <InlineField label="Base URL" value={draft.base_url}
        onChange={(v) => update('base_url', v)}
        placeholder="https://issues.example.org/jira" />
      <InlineSelect label="Auth" value={draft.auth_type}
        onChange={(v) => update('auth_type', v as 'basic' | 'bearer')}
        options={[
          { value: 'basic', label: 'Basic (email + API token, JIRA Cloud)' },
          { value: 'bearer', label: 'Bearer (PAT, server / Apache)' },
        ]} />
      {draft.auth_type === 'basic' && (
        <InlineField label="Email" value={draft.email}
          onChange={(v) => update('email', v)}
          placeholder="alice@example.com" />
      )}
      <InlineField label="API token" value={draft.api_token}
        onChange={(v) => update('api_token', v)}
        type="password"
        placeholder={instance.has_token ? '(re-paste to update)' : 'paste token here'}
      />
      <InlineField label="JQL" value={draft.jql}
        onChange={(v) => update('jql', v)}
        placeholder="assignee = currentUser() AND statusCategory != Done" />
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
        <button
          className="btn-action accent"
          onClick={save}
          disabled={saving === 'pending'}
          data-testid={`settings-jira-save-${instance.name || 'new'}`}
          style={{ fontSize: 10 }}
        >{saving === 'pending' ? 'Saving...' : 'Save'}</button>
        {saving === 'ok' && <span style={{ fontSize: 10, color: 'var(--green)' }}>saved</span>}
        {saving === 'error' && errMsg && (
          <span style={{ fontSize: 10, color: 'var(--red)' }}>{errMsg}</span>
        )}
      </div>
    </div>
  )
}


function InlineField({
  label, value, onChange, placeholder, type = 'text',
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  type?: 'text' | 'password'
}) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ width: 90, fontSize: 10, color: 'var(--text-dim)' }}>{label}</span>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        style={{
          flex: 1, padding: '3px 6px', fontSize: 11,
          background: 'var(--input-bg)', border: '1px solid var(--border)',
          borderRadius: 4, color: 'var(--text)', boxSizing: 'border-box',
        }}
      />
    </label>
  )
}


function InlineSelect({
  label, value, onChange, options,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  options: Array<{ value: string; label: string }>
}) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ width: 90, fontSize: 10, color: 'var(--text-dim)' }}>{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          flex: 1, padding: '3px 6px', fontSize: 11,
          background: 'var(--input-bg)', border: '1px solid var(--border)',
          borderRadius: 4, color: 'var(--text)',
        }}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </label>
  )
}


// ---- Layout primitives ----

// Search filter: a parent `SettingsModal` puts the current query in
// context; every `<Section>` self-hides when its title doesn't match.
// Empty query = show all, so the context is also the disable flag.
const SearchFilterContext = React.createContext<string>('')

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const filter = React.useContext(SearchFilterContext).trim().toLowerCase()
  const match = !filter || title.toLowerCase().includes(filter)
  if (!match) return null
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{
        fontSize: 11, fontWeight: 700, color: 'var(--accent)',
        textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 6,
      }}>{title}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {children}
      </div>
    </div>
  )
}

/** Plugin-section header with built-in enable toggle on the right. */
function PluginSection({
  anchor, title, enabledKey, bag, save, saving, children,
}: {
  anchor: string
  title: string
  enabledKey: string
  bag: SettingsBag
  save: (key: string, value: unknown) => void
  saving: Record<string, 'pending' | 'ok' | 'error'>
  children: React.ReactNode
}) {
  const enabled = asBoolDefault(bag[enabledKey], true)
  return (
    <div id={anchor} style={{ marginBottom: 18, scrollMarginTop: 8 }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: 6, paddingBottom: 4,
        borderBottom: '1px solid var(--border)',
      }}>
        <span style={{
          fontSize: 11, fontWeight: 700, color: 'var(--accent)',
          textTransform: 'uppercase', letterSpacing: 0.6,
        }}>{title}</span>
        <label style={{
          display: 'flex', alignItems: 'center', gap: 4, fontSize: 10,
          color: enabled ? 'var(--green)' : 'var(--text-dim)',
        }}>
          <input
            type="checkbox" checked={enabled}
            data-testid={`settings-toggle-${anchor}`}
            onChange={(e) => save(enabledKey, e.target.checked)}
          />
          {enabled ? 'enabled' : 'disabled'}
          <StatusDot status={saving[enabledKey]} />
        </label>
      </div>
      <div style={{
        display: 'flex', flexDirection: 'column', gap: 6,
        opacity: enabled ? 1 : 0.55,
      }}>
        {children}
      </div>
    </div>
  )
}

function Note({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: 10, color: 'var(--text-dim)', marginBottom: 4 }}>
      {children}
    </div>
  )
}

function StatusDot({ status }: { status?: 'pending' | 'ok' | 'error' }) {
  if (!status) return null
  const map: Record<string, string> = {
    pending: 'var(--text-dim)', ok: 'var(--green)', error: 'var(--red)',
  }
  return (
    <span style={{
      width: 6, height: 6, borderRadius: '50%',
      background: map[status], display: 'inline-block', marginLeft: 4,
    }} />
  )
}


// ---- Field components ----

function TextField({
  label, value, onSave, status, type = 'text',
}: {
  label: string; value: string; onSave: (v: string) => void
  status?: 'pending' | 'ok' | 'error'; type?: 'text' | 'password'
}) {
  const [draft, setDraft] = useState(value)
  useEffect(() => { setDraft(value) }, [value])
  const dirty = draft !== value
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ width: 110, fontSize: 11, color: 'var(--text-dim)' }}>{label}</span>
      <input
        type={type}
        data-testid={`settings-input-${label.toLowerCase().replace(/\s+/g, '-')}`}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        style={{
          flex: 1, padding: '4px 6px', fontSize: 11,
          background: 'var(--panel-bg)', border: '1px solid var(--border)',
          borderRadius: 4, color: 'var(--text)', boxSizing: 'border-box',
        }}
      />
      <button
        className="btn-action accent"
        disabled={!dirty || status === 'pending'}
        onClick={() => onSave(draft)}
        style={{ fontSize: 10 }}
      >Save</button>
      <StatusDot status={status} />
    </label>
  )
}

function SecretField(props: Omit<Parameters<typeof TextField>[0], 'type'>) {
  return <TextField {...props} type="password" />
}

function BoolField({
  label, value, onSave, status,
}: {
  label: string; value: boolean; onSave: (v: boolean) => void
  status?: 'pending' | 'ok' | 'error'
}) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <input
        type="checkbox" checked={value}
        data-testid={`settings-bool-${label.toLowerCase().replace(/\s+/g, '-')}`}
        onChange={(e) => onSave(e.target.checked)}
      />
      <span style={{ fontSize: 11, color: 'var(--text)' }}>{label}</span>
      <StatusDot status={status} />
    </label>
  )
}

function NumberField({
  label, value, onSave, status,
}: {
  label: string; value: number | null; onSave: (v: number | null) => void
  status?: 'pending' | 'ok' | 'error'
}) {
  const [draft, setDraft] = useState(value == null ? '' : String(value))
  useEffect(() => { setDraft(value == null ? '' : String(value)) }, [value])
  const parsed = draft.trim() === '' ? null : Number(draft)
  const valid = parsed === null || (!Number.isNaN(parsed) && parsed >= 0)
  const dirty = parsed !== value
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ width: 140, fontSize: 11, color: 'var(--text-dim)' }}>{label}</span>
      <input
        type="text" inputMode="numeric"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="(default)"
        style={{
          width: 100, padding: '4px 6px', fontSize: 11,
          background: 'var(--panel-bg)',
          border: `1px solid ${valid ? 'var(--border)' : 'var(--red)'}`,
          borderRadius: 4, color: 'var(--text)',
        }}
      />
      <button
        className="btn-action accent"
        disabled={!dirty || !valid || status === 'pending'}
        onClick={() => onSave(parsed)}
        style={{ fontSize: 10 }}
      >Save</button>
      <StatusDot status={status} />
    </label>
  )
}

function ListField({
  itemLabel, value, fields, onSave, status,
}: {
  itemLabel: string
  value: Array<Record<string, string> | string>
  fields: Array<{ key: string; label: string }>
  onSave: (v: unknown) => void
  status?: 'pending' | 'ok' | 'error'
}) {
  const [draft, setDraft] = useState(value)
  useEffect(() => { setDraft(value) }, [value])
  const isStringList = fields.length === 1 && fields[0].key === ''
  const update = (idx: number, key: string, v: string) => {
    setDraft((prev) => prev.map((item, i) => {
      if (i !== idx) return item
      if (isStringList) return v
      return { ...(item as Record<string, string>), [key]: v }
    }))
  }
  const remove = (idx: number) =>
    setDraft((prev) => prev.filter((_, i) => i !== idx))
  const add = () =>
    setDraft((prev) => [...prev, isStringList
      ? '' : Object.fromEntries(fields.map((f) => [f.key, '']))])
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {draft.map((item, i) => (
        <div key={i} style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
          {fields.map((f) => (
            <input
              key={f.key || 'value'}
              data-testid={`settings-list-input-${i}-${f.key || 'value'}`}
              value={isStringList ? (item as string) : ((item as Record<string, string>)[f.key] || '')}
              onChange={(e) => update(i, f.key, e.target.value)}
              placeholder={f.label}
              style={{
                flex: 1, padding: '3px 6px', fontSize: 11,
                background: 'var(--panel-bg)',
                border: '1px solid var(--border)',
                borderRadius: 4, color: 'var(--text)',
              }}
            />
          ))}
          <button
            className="btn-action"
            onClick={() => remove(i)}
            style={{ fontSize: 10, padding: '2px 6px' }}
            title={`Remove ${itemLabel}`}
          >x</button>
        </div>
      ))}
      <div style={{ display: 'flex', gap: 4, marginTop: 2 }}>
        <button className="btn-action" onClick={add} style={{ fontSize: 10 }}>+ Add {itemLabel}</button>
        <button
          className="btn-action accent"
          onClick={() => onSave(draft)}
          disabled={status === 'pending'}
          style={{ fontSize: 10, marginLeft: 'auto' }}
        >Save</button>
        <StatusDot status={status} />
      </div>
    </div>
  )
}

function DictField({
  value, keyLabel, valueLabel, onSave, status,
}: {
  value: Record<string, string>
  keyLabel: string; valueLabel: string
  onSave: (v: Record<string, string>) => void
  status?: 'pending' | 'ok' | 'error'
}) {
  const [draft, setDraft] = useState<Array<[string, string]>>(
    Object.entries(value),
  )
  useEffect(() => { setDraft(Object.entries(value)) }, [value])
  const update = (idx: number, side: 0 | 1, v: string) =>
    setDraft((prev) => prev.map((pair, i) =>
      i === idx ? (side === 0 ? [v, pair[1]] : [pair[0], v]) : pair))
  const remove = (idx: number) =>
    setDraft((prev) => prev.filter((_, i) => i !== idx))
  const add = () => setDraft((prev) => [...prev, ['', '']])
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {draft.map((pair, i) => (
        <div key={i} style={{ display: 'flex', gap: 4 }}>
          <input
            value={pair[0]} placeholder={keyLabel}
            onChange={(e) => update(i, 0, e.target.value)}
            style={{
              flex: 1, padding: '3px 6px', fontSize: 11,
              background: 'var(--panel-bg)',
              border: '1px solid var(--border)',
              borderRadius: 4, color: 'var(--text)',
            }}
          />
          <input
            value={pair[1]} placeholder={valueLabel}
            onChange={(e) => update(i, 1, e.target.value)}
            style={{
              flex: 1, padding: '3px 6px', fontSize: 11,
              background: 'var(--panel-bg)',
              border: '1px solid var(--border)',
              borderRadius: 4, color: 'var(--text)',
            }}
          />
          <button
            className="btn-action" onClick={() => remove(i)}
            style={{ fontSize: 10, padding: '2px 6px' }}
          >x</button>
        </div>
      ))}
      <div style={{ display: 'flex', gap: 4, marginTop: 2 }}>
        <button className="btn-action" onClick={add} style={{ fontSize: 10 }}>+ Add</button>
        <button
          className="btn-action accent"
          onClick={() => onSave(Object.fromEntries(draft.filter(([k]) => k.trim() !== '')))}
          disabled={status === 'pending'}
          style={{ fontSize: 10, marginLeft: 'auto' }}
        >Save</button>
        <StatusDot status={status} />
      </div>
    </div>
  )
}


// ---- Type-safe value coercers ----

function asString(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function asBool(v: unknown): boolean {
  return v === true
}

function asBoolDefault(v: unknown, fallback: boolean): boolean {
  if (v === true) return true
  if (v === false) return false
  return fallback
}

function asNumber(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function asListOfStr(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : []
}

function asListOfDict(v: unknown): Array<Record<string, string>> {
  if (!Array.isArray(v)) return []
  return v
    .filter((x) => x && typeof x === 'object' && !Array.isArray(x))
    .map((x) => {
      const out: Record<string, string> = {}
      for (const [k, val] of Object.entries(x as Record<string, unknown>)) {
        out[k] = typeof val === 'string' ? val : String(val ?? '')
      }
      return out
    })
}

function asDict(v: unknown): Record<string, string> {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return {}
  const out: Record<string, string> = {}
  for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
    out[k] = typeof val === 'string' ? val : String(val ?? '')
  }
  return out
}
