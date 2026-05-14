import React, { useState, useEffect, useCallback, useRef } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  useReactFlow,
  Handle,
  Position,
  type Edge,
  type Node,
  type Connection,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from 'dagre'

import type { Project, GraphData, Task } from '../types'
import { api } from '../api'
import { StatusDot } from './StatusDot'
import { SessionDot } from './SessionDot'
import { useAlert } from './Alert'
import { useSessionState } from '../hooks/SessionStatusProvider'
import { isKnownSessionState } from '../utils/sessionState'
import { isTaskBlocked, isTerminalTaskStatus } from '../utils/taskHelpers'
import { timeAgo } from '../utils'
import { nudgeLayout } from '../utils/nudgeLayout'
import { useGraphLayout } from '../hooks/useGraphLayout'
import { usePendingCreates } from '../hooks/usePendingCreates'
import { startCreate, dismissCreate, setPendingPosition } from '../services/pendingCreates'
import { DraftTaskNode, DRAFT_NODE_W, DRAFT_NODE_H } from './DraftTaskNode'
import {
  STATUS_COLORS, NODE_W, NODE_H, MINI_H,
  getThemeOpacity,
  latestPrCiStatus, ciPillStyle,
  type TaskNodeData,
} from './graphShared'

interface ContextMenu {
  x: number
  y: number
  taskId?: string   // set when right-clicking a node
  hasTicket?: boolean
  /** Set when right-clicking a dependency edge. (from -> to) means
   * "to depends on from". Both fields go together; presence of these
   * is what tells the renderer to show the "Remove dependency" item. */
  edgeFrom?: string
  edgeTo?: string
}

interface GraphViewProps {
  project: Project
  onSelectTask: (taskId: string | null) => void
  selectedTask: string | null
}

// Live session-status dot. The 3-tier urgency palette is defined
// once in `utils/sessionState.ts` (sessionDotColor / sessionDotAnim
// / sessionDotHalo). Every renderer of session status -- this graph,
// the TaskNodeChip footer, SessionCard, LiveSessionChip, ProjectSessionCard,
// CronJobsPage rows -- imports those helpers (or the wrapping
// `<SessionDot>` component) so colors stay in sync everywhere.


export function TaskNode({ data }: { data: TaskNodeData }) {
  const {
    taskId, status, type, updatedAt, latestHistory,
    isMini, isExpanded, isSelected,
    ticketId, prCount, hasTickets, hasSession,
    latestPrCiStatus: ciStatus,
    highlighted, isNewlyCreated, isDropTarget,
    onSelect, onToggleExpand,
  } = data
  // Live session state -- subscribe to the global snapshot so the
  // bottom-right dot + the red `task-node-attention` halo react
  // instantly to agent hooks instead of waiting for the next
  // /api/projects/{pid} refetch. Falls back to the prop-passed
  // value (set at GraphView data-construction time) when the
  // snapshot doesn't have this row yet.
  const liveRow = useSessionState(taskId)
  const sessionStatus = liveRow?.state ?? data.sessionStatus
  const color = STATUS_COLORS[status] || 'var(--text-faint)'
  // PR pill background reflects the most-recent PR's CI: green ok,
  // red failure, yellow in-flight. Falls back to the neutral badge
  // palette when the task has no PRs (or ci_status is empty).
  const ciPalette = ciPillStyle(ciStatus || '')

  const dimNodeOpacity = getThemeOpacity('--graph-dim-node', 0.15)
  const opacity = highlighted === null
    ? (isMini ? 0.5 : 1)
    : (highlighted ? 1 : dimNodeOpacity)

  // Fixed width across all nodes -- visual rhythm beats not-clipping
  // a long id. Long ids ellipsis-truncate inside row 1 instead of
  // pushing the card wider; full id still available via the title
  // attribute on hover. Same width used by `getLayoutedElements` so
  // dagre + render agree.
  const w = NODE_W
  const h = isMini ? MINI_H : NODE_H

  const handleClick = useCallback(() => {
    const isDoneOrClosed = isTerminalTaskStatus(status)
    if (isDoneOrClosed && !isExpanded) {
      onToggleExpand(taskId)
    } else {
      onSelect(taskId)
    }
  }, [onSelect, onToggleExpand, taskId, status, isExpanded])

  // Copy ticket id to clipboard instead of jumping to JIRA. The full
  // TaskCard panel (right side of project page) keeps the external
  // link for users who actually want it.
  const copyTicket = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    if (!ticketId) return
    try { navigator.clipboard?.writeText(ticketId) } catch { /* ignore */ }
  }, [ticketId])

  // Border color stays tied to status -- selection is conveyed by the
  // halo shadow (selectedShadow below) so the status hue never drifts
  // when the user clicks a node.
  const borderColor = isDropTarget ? 'var(--accent)' : color

  // Both mini and full show the full task id verbatim; the card
  // grows horizontally to fit (computeWidth above).
  const label = taskId

  const prBadge = prCount > 0 ? `${prCount} PR${prCount > 1 ? 's' : ''}` : ''

  // Active-status nodes get a subtle "breathing" backdrop so the
  // graph reads dynamically. Terminal / blocked stay calm.
  const isActive = status === 'in_progress' || status === 'in_review'
  // Whole-card red halo whenever a session is blocking on the user.
  // The right-bottom dot is too small to spot in a wall of cards;
  // a red glow on the card itself is impossible to miss. We don't
  // gate this on isMini -- the urgency signal must work everywhere
  // the same task is rendered.
  // Red halo only for truly urgent states -- needs_permission (agent
  // can't proceed without a yes/no) or crashed (resume me). idle is
  // "soft attention" (yellow dot, no halo) so a fleet of N idle
  // sessions doesn't drown the page in red glow.
  const needsAttention = sessionStatus === 'needs_permission'
    || sessionStatus === 'crashed'
  const taskNodeClass = [
    isDropTarget ? 'task-node-drop-target' : '',
    isActive && !isMini ? 'task-node-active' : '',
    needsAttention ? 'task-node-attention' : '',
  ].filter(Boolean).join(' ') || undefined

  // Modern shadow + selection appearance: layered shadows for depth +
  // an accent halo when selected, so the node visually "lifts" off
  // the canvas. Drop-target overrides shadow entirely (its own pulse
  // animation is the primary affordance).
  const baseShadow = isMini
    ? '0 1px 2px var(--shadow-color)'
    : '0 1px 3px var(--shadow-color), 0 2px 8px var(--shadow-color)'
  const selectedShadow = (
    `0 0 0 3px color-mix(in srgb, var(--accent) 28%, transparent), ` +
    `0 4px 14px var(--shadow-color)`
  )
  const finalShadow = isDropTarget
    ? undefined
    : (isSelected ? selectedShadow : baseShadow)

  return (
    <div
      data-testid={`graph-node-${taskId}`}
      data-drop-target={isDropTarget ? 'true' : undefined}
      data-status={status}
      onClick={handleClick}
      className={taskNodeClass}
      style={{
        width: w,
        height: h,
        opacity,
        cursor: 'pointer',
        borderRadius: isMini ? 6 : 10,
        // Solid card background -- no gradient / alpha so colours stay
        // crisp regardless of theme mode.
        background: 'var(--card-bg)',
        border: `${isMini ? (isDropTarget ? 2 : 1) : (isDropTarget ? 2 : 1)}px solid ${borderColor}`,
        borderLeft: `${isMini ? 3 : 4}px solid ${borderColor}`,
        boxShadow: finalShadow,
        display: 'flex',
        flexDirection: 'column',
        padding: isMini ? '0 8px' : '5px 8px',
        fontFamily: 'inherit',
        position: 'relative',
        boxSizing: 'border-box',
        transition: 'box-shadow 0.22s cubic-bezier(0.2,0.8,0.2,1), transform 0.22s cubic-bezier(0.2,0.8,0.2,1)',
      }}
    >
      {!data.hideHandles && (
        <>
          <Handle type="target" position={Position.Left} style={{ width: 6, height: 6, background: 'var(--handle-bg)', border: '1px solid var(--handle-border)', left: -3 }} />
          <Handle type="source" position={Position.Right} style={{ width: 6, height: 6, background: 'var(--handle-bg)', border: '1px solid var(--handle-border)', right: -3 }} />
        </>
      )}

      {/* Top-edge "thinking" progress strip. Replaces the blue dot in
          the footer for the most common in-flight state -- a moving
          line at the top of the card reads as "actively working" much
          faster than a static colored dot. Sits inside the rounded
          corners via overflow:hidden on its track. */}
      {sessionStatus === 'thinking' && (
        <div
          data-testid={`session-thinking-strip-${taskId}`}
          aria-label="Session thinking"
          style={{
            position: 'absolute',
            top: 0, left: 0, right: 0,
            height: 2,
            borderTopLeftRadius: 'inherit',
            borderTopRightRadius: 'inherit',
            overflow: 'hidden',
            pointerEvents: 'none',
          }}
        >
          <div className="task-node-thinking-strip-fill" />
        </div>
      )}

      {/* Row 1 (header) -- IDENTICAL between mini and full. Expanding
          a mini node feels like the other rows sliding in beneath this
          row, not switching to a different card. The colored
          border-left already conveys status, so we don't repeat the
          StatusDot here -- it lives on the footer for the full card. */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6,
        height: isMini ? '100%' : 15,
        flexShrink: 0,
      }}>
        <span style={{
          color: 'var(--node-text)', fontSize: 11, fontWeight: 700,
          whiteSpace: 'nowrap', flex: 1,
          // minWidth:0 lets the flex slot shrink below the natural
          // text size; overflow:hidden + ellipsis is the only way to
          // get the visual truncation we want without the text
          // bleeding past the column and crashing into the PR pill.
          minWidth: 0,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          lineHeight: 1.2,
        }} title={taskId}>{label}</span>
        {prBadge && (
          <span
            data-testid={`task-pr-pill-${taskId}`}
            data-ci-status={ciStatus || 'unknown'}
            title={
              ciStatus
                ? `${prCount} PR${prCount > 1 ? 's' : ''} - latest CI: ${ciStatus}`
                : `${prCount} pull request${prCount > 1 ? 's' : ''}`
            }
            style={{
              fontSize: 8, fontWeight: 700,
              color: ciPalette ? ciPalette.fg : 'var(--node-badge-text)',
              background: ciPalette ? ciPalette.bg : 'var(--node-badge-bg)',
              // Pill shape (max radius). Modern token-style.
              borderRadius: 999, padding: '1px 6px',
              letterSpacing: 0.3,
              border: '1px solid ' + (
                ciPalette
                  ? ciPalette.border
                  : 'color-mix(in srgb, var(--accent) 25%, transparent)'
              ),
              flexShrink: 0,
              transition: 'background 0.25s, color 0.25s, border-color 0.25s',
            }}
          >{prBadge}</span>
        )}
      </div>

      {!isMini && (
        <>
          {/* Recent history -- wraps to 2 lines max. Falls back to a
              short "updated X ago" hint when the task has no history. */}
          <div style={{ flex: 1, overflow: 'hidden', minHeight: 0, marginTop: 2 }}>
            {latestHistory ? (
              <div style={{
                color: 'var(--text-dim)', fontSize: 9.5, lineHeight: 1.25,
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden',
                wordBreak: 'break-word',
              }} title={latestHistory.text}>{latestHistory.text}</div>
            ) : updatedAt ? (
              <div style={{
                color: 'var(--text-faint)', fontSize: 9, fontStyle: 'italic',
              }}>no history yet</div>
            ) : null}
          </div>

          {/* Meta row: relative timestamp on the left, type pill on the
              right. Type lives here (not in row 1) so the header stays
              identical to the mini layout. */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 5, marginTop: 2,
            height: 11,
          }}>
            <span style={{
              color: 'var(--text-faint)', fontSize: 8.5,
              fontFamily: 'monospace', flex: 1,
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }} title={latestHistory?.ts || updatedAt || ''}>
              {latestHistory ? timeAgo(latestHistory.ts) : (updatedAt ? timeAgo(updatedAt) : '')}
            </span>
            {type && (
              <span style={{
                fontSize: 8, fontWeight: 700,
                color: color, background: `${color}1a`,
                padding: '1px 6px', borderRadius: 999,
                textTransform: 'uppercase', letterSpacing: 0.5,
                border: `1px solid ${color}33`,
                flexShrink: 0,
              }}>{type}</span>
            )}
          </div>

          {/* Footer: status + ticket + session indicator */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 5, marginTop: 2,
            height: 13,
          }}>
            <StatusDot status={status} style={{ flexShrink: 0 }} />
            <span style={{
              fontSize: 8, color: 'var(--text-dim)',
              textTransform: 'uppercase', letterSpacing: 0.5, fontWeight: 600,
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }}>{status.replace('_', ' ')}</span>
            {ticketId ? (
              <span
                onClick={copyTicket}
                title="Click to copy ticket id"
                className="nodrag"
                style={{
                  marginLeft: 'auto', cursor: 'copy',
                  fontSize: 8, color: 'var(--accent)',
                  background: 'var(--node-badge-bg)',
                  padding: '1px 6px', borderRadius: 999,
                  border: '1px solid color-mix(in srgb, var(--accent) 25%, transparent)',
                  fontWeight: 600, flexShrink: 0,
                }}
              >{ticketId}</span>
            ) : hasTickets ? (
              <span style={{
                marginLeft: 'auto', fontSize: 8,
                color: 'var(--text-subtle)', fontStyle: 'italic',
              }}>no ticket</span>
            ) : null}
            {hasSession && (
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: 3,
                marginLeft: ticketId || hasTickets ? 0 : 'auto', flexShrink: 0,
              }}>
                {isKnownSessionState(sessionStatus) && (
                  <SessionDot
                    state={sessionStatus}
                    size={6}
                    testid={`session-status-dot-${taskId}`}
                  />
                )}
                <img
                  src="/static/claude-favicon.ico"
                  width={10} height={10}
                  style={{ opacity: 0.75 }}
                  alt="session"
                  title="Has Agent session"
                />
              </span>
            )}
          </div>
        </>
      )}
      {isNewlyCreated && (
        <span
          data-testid={`new-badge-${taskId}`}
          title="Newly created"
          style={{
            position: 'absolute',
            top: -8, right: -8,
            background: 'var(--accent)',
            color: 'white',
            fontSize: 8, fontWeight: 700,
            padding: '2px 6px',
            borderRadius: 10,
            letterSpacing: 0.5,
            boxShadow: '0 2px 6px var(--shadow-color)',
          }}
        >NEW</span>
      )}
    </div>
  )
}

export const nodeTypes = { taskNode: TaskNode, draftTaskNode: DraftTaskNode }

// Padding (in flow coords) between two nodes after collision
// resolution. Keeps a small visual gap so cards don't kiss.
const COLLISION_PADDING = 10

interface BBox { x: number; y: number; w: number; h: number }

function _intersects(a: BBox, b: BBox, pad: number): boolean {
  return (
    a.x < b.x + b.w + pad &&
    a.x + a.w + pad > b.x &&
    a.y < b.y + b.h + pad &&
    a.y + a.h + pad > b.y
  )
}

/**
 * Find the nearest non-overlapping position for a dragged node.
 *
 * Spiral search: tries the original spot first, then expands in
 * progressively wider rings around it. Guarantees we land at the
 * closest free position so the snap-after-release feels minimal.
 *
 * Exported for unit tests.
 */
export function resolveCollision(
  pos: { x: number; y: number },
  w: number,
  h: number,
  others: BBox[],
  pad: number = COLLISION_PADDING,
): { x: number; y: number } {
  const candidate: BBox = { x: pos.x, y: pos.y, w, h }
  if (!others.some((o) => _intersects(candidate, o, pad))) return pos
  const angles = 16
  for (let r = 8; r <= 800; r += 8) {
    for (let i = 0; i < angles; i++) {
      const a = (i * 2 * Math.PI) / angles
      const x = pos.x + Math.cos(a) * r
      const y = pos.y + Math.sin(a) * r
      const c: BBox = { x, y, w, h }
      if (!others.some((o) => _intersects(c, o, pad))) {
        return { x, y }
      }
    }
  }
  return pos
}

export function getLayoutedElements(
  graphData: GraphData,
  selectedTask: string | null,
  tasks: Record<string, { dependencies?: string[]; session?: { name: string } | null }>,
  expandedNodes: Record<string, boolean>,
  storedPositions: Record<string, { x: number; y: number }> = {},
) {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', nodesep: 14, ranksep: 50 })

  // Determine highlighted set
  let highlightedSet: Record<string, boolean> | null = null
  if (selectedTask && tasks[selectedTask]) {
    highlightedSet = { [selectedTask]: true }
    const walkUp = (tid: string) => {
      const t = tasks[tid]
      if (!t) return
      for (const d of t.dependencies || []) {
        if (!highlightedSet![d]) {
          highlightedSet![d] = true
          walkUp(d)
        }
      }
    }
    // Walk downstream: find tasks that depend on tid
    const walkDown = (tid: string) => {
      for (const [otherId, otherTask] of Object.entries(tasks)) {
        if ((otherTask.dependencies || []).includes(tid) && !highlightedSet![otherId]) {
          highlightedSet![otherId] = true
          walkDown(otherId)
        }
      }
    }
    walkUp(selectedTask)
    walkDown(selectedTask)
  }

  // Fixed width across all nodes -- the visual rhythm of a uniform
  // grid beats not-clipping a long id. Long ids ellipsis-truncate
  // in TaskNode row 1; the title attribute carries the full string
  // for hover.
  const computeWidth = (_id: string): number => NODE_W

  // Add nodes to dagre
  for (const node of graphData.nodes) {
    const isDoneOrClosed = isTerminalTaskStatus(node.status)
    const isMini = isDoneOrClosed && !expandedNodes[node.id]
    const w = computeWidth(node.id)
    const h = isMini ? MINI_H : NODE_H
    g.setNode(node.id, { width: w, height: h })
  }

  // Add edges to dagre
  for (const edge of graphData.edges) {
    g.setEdge(edge.from, edge.to)
  }

  dagre.layout(g)

  // Convert to React Flow nodes
  // Use live task data from project.tasks when available (for real-time
  // status updates). `blocked` is recomputed locally because `liveTask`
  // carries the *stored* status -- without the override here a
  // blocked-by-deps task would render with its stored colour
  // (`not_started`/`in_progress`) even though the API's
  // `effective_status` would have flagged it blocked.
  const rfNodes = graphData.nodes.map((node) => {
    const pos = g.node(node.id)
    const liveTask = tasks[node.id] as Record<string, unknown> | undefined
    const stored = (liveTask?.status as string) || node.status
    // Blocked override: ANY non-terminal task with unclosed deps shows
    // as blocked. Mirrors `core.tasks.get_task` effective_status logic.
    const isTerminal = stored === 'done' || stored === 'closed'
    const blocked = !isTerminal && isTaskBlocked(node.id, tasks as Record<string, Task>)
    const status = blocked ? 'blocked' : stored
    const ticketId = (liveTask?.ticket_id as string | null) ?? node.ticket_id
    const ticketUrl = (liveTask?.ticket_url as string | null) ?? node.ticket_url
    const prs = (liveTask?.prs as unknown[]) || node.prs || []
    const isDoneOrClosed = isTerminalTaskStatus(status)
    const isMini = isDoneOrClosed && !expandedNodes[node.id]
    const w = computeWidth(node.id)
    const h = isMini ? MINI_H : NODE_H

    // User-pinned position wins over dagre. Initial render seeds LS
    // with dagre output so subsequent edge / status changes can't
    // shake un-dragged nodes.
    const dagrePos = { x: pos.x - w / 2, y: pos.y - h / 2 }
    const position = storedPositions[node.id] ?? dagrePos

    return {
      id: node.id,
      type: 'taskNode' as const,
      position,
      data: {
        taskId: node.id,
        status,
        type: (liveTask?.type as string | null) ?? (node as { type?: string | null }).type ?? null,
        updatedAt: (liveTask?.updated_at as string | null) ?? (node as { updated_at?: string | null }).updated_at ?? null,
        latestHistory: (() => {
          const hist = liveTask?.history as Array<{ ts: string; text: string }> | undefined
          // Backend returns history newest-first; defensive against
          // mixed orderings by picking the latest by ts when present.
          if (!hist || !hist.length) return null
          return hist[0] ?? null
        })(),
        sessionStatus: (() => {
          const sess = liveTask?.session as { status?: string; running?: boolean } | undefined
          if (!sess) return ''
          return sess.running === false ? 'stopped' : (sess.status || '')
        })(),
        isMini,
        isExpanded: !!expandedNodes[node.id],
        isSelected: selectedTask === node.id,
        ticketId,
        ticketUrl,
        prCount: prs.length,
        latestPrCiStatus: latestPrCiStatus(
          prs as Array<{ ci_status?: string; last_updated?: string }>),
        hasTickets: graphData.has_tickets !== false,
        hasSession: !!(tasks[node.id]?.session),
        highlighted: highlightedSet ? (!!highlightedSet[node.id]) : null,
        onSelect: () => {},
        onToggleExpand: () => {},
      } satisfies TaskNodeData,
      style: { padding: 0 },
    }
  })

  // Convert to React Flow edges
  const rfEdges = graphData.edges.map((edge, i) => {
    const fromDone = graphData.nodes.find((n) => n.id === edge.from)
    const toDone = graphData.nodes.find((n) => n.id === edge.to)
    const bothDone = !!fromDone && !!toDone
      && isTerminalTaskStatus(fromDone.status)
      && isTerminalTaskStatus(toDone.status)

    const edgeHighlighted = highlightedSet
      ? (!!highlightedSet[edge.from] && !!highlightedSet[edge.to])
      : true

    return {
      id: `e-${i}-${edge.from}-${edge.to}`,
      source: edge.from,
      target: edge.to,
      type: 'smoothstep' as const,
      animated: !bothDone && edgeHighlighted,
      style: {
        // `--graph-done-edge` is a dedicated mid-gray that has decent
        // contrast in BOTH themes. Earlier we used `--node-badge-bg`,
        // which is near-white in light mode (#e4e4e7) -- 0.4 opacity on
        // top of white made done-task edges effectively invisible.
        // Bumped opacity floor 0.4 -> 0.7 too; even with the new color
        // 0.4 was on the edge of perceptibility.
        stroke: highlightedSet && edgeHighlighted ? 'var(--blue)' : (bothDone ? 'var(--graph-done-edge)' : 'var(--text-faint)'),
        strokeWidth: highlightedSet && edgeHighlighted ? 2 : 1.5,
        opacity: highlightedSet ? (edgeHighlighted ? 1 : getThemeOpacity('--graph-dim-edge', 0.08)) : (bothDone ? 0.7 : 1),
      },
    }
  })

  return { nodes: rfNodes, edges: rfEdges }
}

export const GraphView = React.memo(function GraphView(props: GraphViewProps) {
  // ReactFlowProvider lets `useReactFlow().screenToFlowPosition` work
  // in `GraphViewInner` -- needed to place inline draft nodes at the
  // user's right-click point in flow coordinates (not screen coords).
  return (
    <ReactFlowProvider>
      <GraphViewInner {...props} />
    </ReactFlowProvider>
  )
})


const GraphViewInner = React.memo(function GraphViewInner({ project, onSelectTask, selectedTask }: GraphViewProps) {
  const rfInstance = useReactFlow()
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [contextMenu, setContextMenu] = useState<ContextMenu | null>(null)
  const [expandedNodes, setExpandedNodes] = useState<Record<string, boolean>>({})
  const { confirm, confirmAt } = useAlert()
  // Per-project node-position persistence: free-drag survives reloads
  // and prevents edge / status changes from shaking the layout.
  const { positions, setPosition, setPositionsBulk, seedPositions, clearLayout } = useGraphLayout(project.id)
  // Inline draft nodes (forms the user is filling but hasn't submitted).
  // Local-only -- once submitted, control transfers to the
  // pendingCreates module-scoped service so the in-flight fetch
  // survives navigation away from this view.
  interface LocalDraft {
    draftId: string
    position: { x: number; y: number }
  }
  const [localDrafts, setLocalDrafts] = useState<LocalDraft[]>([])
  const pendingList = usePendingCreates(project.id)

  // Fetch graph data from API.
  // Re-fetch only when task count changes (add/delete) -- not on every status update.
  // Status/dep changes are handled by the layout useEffect below which reads project.tasks directly.
  const taskCount = Object.keys(project.tasks).length
  useEffect(() => {
    let cancelled = false
    api.getGraph(project.id).then((data) => {
      if (!cancelled) setGraphData(data)
    }).catch(() => {
      if (!cancelled) setError('Failed to load graph data.')
    })
    return () => { cancelled = true }
  }, [project.id, taskCount])

  // Handle edge click: delete dependency (only if not both tasks are done)
  // Right-click on a dependency edge -> show context menu with
  // "Remove dependency". Left-click no longer deletes (was easy to
  // hit by accident); the menu makes the destructive action explicit.
  const onEdgeContextMenu = useCallback((event: React.MouseEvent, edge: Edge) => {
    event.preventDefault()
    setContextMenu({
      x: event.clientX, y: event.clientY,
      edgeFrom: edge.source, edgeTo: edge.target,
    })
  }, [])

  // Actual remove-dep logic, invoked from the context menu item.
  // `anchor` carries the click position so the confirmation pops as a
  // bubble at the pointer instead of a centred backdrop modal --
  // matches the rest of the lightweight destructive flows (Unpin etc).
  const removeDepEdge = useCallback(async (
    source: string, target: string, anchor?: { x: number; y: number },
  ) => {
    const sourceTask = project.tasks[source]
    const targetTask = project.tasks[target]
    const sourceDone = !!sourceTask && isTerminalTaskStatus(sourceTask.status)
    const targetDone = !!targetTask && isTerminalTaskStatus(targetTask.status)

    if (sourceDone && targetDone) return  // both done, dep is "frozen"

    const opts = {
      title: 'Remove dependency?',
      message: `"${target}" depends on "${source}"`,
      confirmLabel: 'Remove',
      danger: true,
    }
    const ok = anchor ? await confirmAt(opts, anchor) : await confirm(opts)
    if (!ok) return

    // Optimistic: remove from graphData
    setGraphData((prev) => {
      if (!prev) return prev
      return { ...prev, edges: prev.edges.filter((e) => !(e.from === source && e.to === target)) }
    })

    // Persist to backend
    api.removeDep(project.id, target, source).catch(() => {
      setGraphData((prev) => {
        if (!prev) return prev
        return { ...prev, edges: [...prev.edges, { from: source, to: target }] }
      })
    })
  }, [project.id, project.tasks, confirm, confirmAt])

  // Handle new connection: drag from source (A) to target (B) = B depends on A
  const onConnect = useCallback((connection: Connection) => {
    const source = connection.source  // upstream task
    const target = connection.target  // downstream task (depends on source)
    if (!source || !target || source === target) return

    // Optimistic: add edge to both graphData and rendered edges
    setGraphData((prev) => {
      if (!prev) return prev
      return { ...prev, edges: [...prev.edges, { from: source, to: target }] }
    })

    // Persist to backend
    api.addDep(project.id, target, source).catch(() => {
      // Revert on failure
      setGraphData((prev) => {
        if (!prev) return prev
        return { ...prev, edges: prev.edges.filter((e) => !(e.from === source && e.to === target)) }
      })
    })
  }, [project.id])

  // Wrap React Flow's `onNodesChange` so we can:
  //   1. Resolve overlaps after a drag-end (mini and full nodes have
  //      collision volume; on release we snap to the nearest free
  //      position so cards never overlap).
  //   2. Persist the (possibly resolved) drag-end position to the
  //      per-project LS layout.
  // Other change types (selection, dimensions, removal) pass through
  // untouched.
  // Use a ref to read the latest `nodes` without re-creating
  // handleNodesChange on every node update -- the dep churn was
  // observable in tests as flaky timer behavior on adjacent
  // hover-to-link assertions.
  const nodesRef = useRef<Node[]>([])
  useEffect(() => { nodesRef.current = nodes }, [nodes])

  const handleNodesChange = useCallback((changes: Parameters<typeof onNodesChange>[0]) => {
    const currentNodes = nodesRef.current
    const adjusted = changes.map((change) => {
      if (
        change.type === 'position' &&
        change.position &&
        change.dragging === false
      ) {
        const dragged = currentNodes.find((n) => n.id === change.id)
        if (!dragged) return change
        const dw = (dragged as { width?: number }).width
          ?? (dragged.measured?.width ?? NODE_W)
        const dh = (dragged as { height?: number }).height
          ?? (dragged.measured?.height ?? NODE_H)
        const others = currentNodes
          .filter((n) => n.id !== change.id)
          .map((n) => ({
            x: n.position.x,
            y: n.position.y,
            w: (n as { width?: number }).width
              ?? (n.measured?.width ?? NODE_W),
            h: (n as { height?: number }).height
              ?? (n.measured?.height ?? NODE_H),
          }))
        const resolved = resolveCollision(change.position, dw, dh, others)
        if (
          resolved.x !== change.position.x ||
          resolved.y !== change.position.y
        ) {
          return { ...change, position: resolved }
        }
      }
      return change
    })
    for (const change of adjusted) {
      if (
        change.type === 'position' &&
        change.position &&
        change.dragging === false
      ) {
        // Draft + pending nodes live in their own stores (component
        // state for drafts, the module-scoped pendingCreates service
        // for in-flight smart-creates). Their positions still need
        // to survive the next render -- otherwise the rebuild
        // useEffect would reset the drag to the original drop point.
        if (change.id.startsWith('draft-')) {
          const draftId = change.id.slice('draft-'.length)
          const pos = { x: change.position.x, y: change.position.y }
          setLocalDrafts(prev => prev.map(d =>
            d.draftId === draftId ? { ...d, position: pos } : d
          ))
        } else if (change.id.startsWith('pending-')) {
          const draftId = change.id.slice('pending-'.length)
          setPendingPosition(draftId, {
            x: change.position.x, y: change.position.y,
          })
        } else {
          setPosition(change.id, { x: change.position.x, y: change.position.y })
        }
      }
    }
    onNodesChange(adjusted)
  }, [onNodesChange, setPosition])

  // Hover-to-link gesture: drag node A and hold over node B for
  // HOVER_LINK_DELAY_MS -> create the dependency `B depends on A`
  // (i.e., directed edge A -> B). The drop target pulses while
  // armed; releasing or moving away cancels.
  const HOVER_LINK_DELAY_MS = 2000
  const [dropTargetId, setDropTargetId] = useState<string | null>(null)
  // Track which node is currently being dragged so we can elevate its
  // zIndex above all others -- React Flow's default doesn't always
  // win against the drop-target's pulsing border + glow shadow which
  // visually intrude on the dragged node's bbox.
  const [draggingNodeId, setDraggingNodeId] = useState<string | null>(null)
  const hoverArmRef = useRef<{
    targetId: string | null
    timer: number | null
  }>({ targetId: null, timer: null })

  const cancelHoverArm = useCallback(() => {
    if (hoverArmRef.current.timer != null) {
      window.clearTimeout(hoverArmRef.current.timer)
    }
    hoverArmRef.current = { targetId: null, timer: null }
    setDropTargetId(null)
  }, [])

  const onNodeDrag = useCallback((
    _event: React.MouseEvent,
    draggedNode: Node,
  ) => {
    setDraggingNodeId(draggedNode.id)
    // Compute dragged node's center in flow coords.
    const draggedW = (draggedNode as { width?: number }).width
      ?? (draggedNode.measured?.width ?? NODE_W)
    const draggedH = (draggedNode as { height?: number }).height
      ?? (draggedNode.measured?.height ?? NODE_H)
    const cx = draggedNode.position.x + draggedW / 2
    const cy = draggedNode.position.y + draggedH / 2

    // Find a task node (not draft / not the dragged one) whose
    // bounding box contains that center point.
    let target: Node | null = null
    for (const n of nodes) {
      if (n.id === draggedNode.id) continue
      if (n.type !== 'taskNode') continue
      const nw = (n as { width?: number }).width
        ?? (n.measured?.width ?? NODE_W)
      const nh = (n as { height?: number }).height
        ?? (n.measured?.height ?? NODE_H)
      if (cx >= n.position.x && cx <= n.position.x + nw &&
          cy >= n.position.y && cy <= n.position.y + nh) {
        target = n
        break
      }
    }

    const arm = hoverArmRef.current
    const targetId = target?.id || null

    if (targetId === arm.targetId) return // unchanged

    // Target switched (or cleared) -- cancel any pending arm.
    if (arm.timer != null) window.clearTimeout(arm.timer)

    if (!targetId || !target) {
      hoverArmRef.current = { targetId: null, timer: null }
      setDropTargetId(null)
      return
    }

    // Drag direction is "drag downstream onto upstream": dragging A
    // onto B means A depends on B. Edge in graph: from B to A.
    // Skip if A already depends on B -- no point arming a no-op.
    const draggedDeps = project.tasks[draggedNode.id]?.dependencies || []
    if (draggedDeps.includes(targetId)) {
      hoverArmRef.current = { targetId: null, timer: null }
      setDropTargetId(null)
      return
    }

    setDropTargetId(targetId)
    const timer = window.setTimeout(() => {
      // Re-check at fire time: arm might have been cleared by drag-stop.
      if (hoverArmRef.current.targetId !== targetId) return
      // Optimistic edge add; persist via api.addDep.
      // addDep(project, taskId, dependsOn): "taskId depends on dependsOn"
      // Drag A->B = A depends on B = addDep(project, A, B), edge from B to A.
      setGraphData(prev => {
        if (!prev) return prev
        const exists = prev.edges.some(e => e.from === targetId && e.to === draggedNode.id)
        if (exists) return prev
        return { ...prev, edges: [...prev.edges, { from: targetId, to: draggedNode.id }] }
      })
      api.addDep(project.id, draggedNode.id, targetId).catch(() => {
        // Revert optimistic add on backend failure.
        setGraphData(prev => prev
          ? { ...prev, edges: prev.edges.filter(e => !(e.from === targetId && e.to === draggedNode.id)) }
          : prev)
      })
      // Clear arm so a continued hover doesn't re-fire.
      hoverArmRef.current = { targetId: null, timer: null }
      setDropTargetId(null)
    }, HOVER_LINK_DELAY_MS)

    hoverArmRef.current = { targetId, timer }
  }, [nodes, project.id, project.tasks])

  const onNodeDragStop = useCallback(() => {
    cancelHoverArm()
    setDraggingNodeId(null)
  }, [cancelHoverArm])

  // "Auto Position": gentle local relaxation that respects the
  // user's current layout. Pulls live positions from React Flow
  // (so manual drags committed to LS show up), runs `nudgeLayout`
  // to remove node/node and node/edge collisions while keeping DAG
  // ordering, then commits the result back atomically. Idempotent:
  // running it on an already-clean layout is a no-op.
  const handleAutoPosition = useCallback(() => {
    if (!graphData) return
    const sizes: Record<string, { w: number; h: number }> = {}
    const livePositions: Record<string, { x: number; y: number }> = {}
    const doneIds = new Set<string>()
    for (const n of graphData.nodes) {
      const id = n.id
      // Same isMini logic getLayoutedElements uses, so the layout
      // sees the actual rendered box for each node (mini terminal
      // tasks are 22px tall, full ones 88px).
      const liveTask = project.tasks?.[id]
      const status = (liveTask?.status as string | undefined) || n.status
      const isDoneOrClosed = isTerminalTaskStatus(status)
      const isMini = isDoneOrClosed && !expandedNodes[id]
      sizes[id] = { w: NODE_W, h: isMini ? MINI_H : NODE_H }
      // Done / closed tasks: tidy layout stacks them tightly at the
      // top of each rank column so finished work clusters on the
      // upper-left and the active work occupies the lower-right.
      if (isDoneOrClosed) doneIds.add(id)
      // Prefer the live React Flow position over LS/dagre seed:
      // the user may have just dragged a node and the LS write
      // hasn't round-tripped to React state yet.
      const live = nodes.find((node) => node.id === id)
      if (live) {
        livePositions[id] = { x: live.position.x, y: live.position.y }
      } else if (positions[id]) {
        livePositions[id] = positions[id]
      }
    }
    const nudged = nudgeLayout(
      livePositions,
      graphData.edges.map((e) => ({ from: e.from, to: e.to })),
      sizes,
      { doneIds },
    )
    setPositionsBulk(nudged)
    // Push the new positions into React Flow's node state immediately
    // so the canvas reflects the change this render. The
    // setPositionsBulk -> useEffect chain that rebuilds `nodes` from
    // `layoutRef` doesn't include `positions` in its deps, so without
    // this direct update the user clicks Auto Position and the LS
    // gets written but the canvas appears frozen.
    setNodes((prev) => prev.map((n) => (
      nudged[n.id]
        ? { ...n, position: { x: nudged[n.id].x, y: nudged[n.id].y } }
        : n
    )))
  }, [graphData, project.tasks, expandedNodes, nodes, positions, setPositionsBulk, setNodes])

  // Local draft: user clicks Create -> hand off to pendingCreates so
  // the fetch survives navigation. Drop the local draft (its slot is
  // taken over by the pending entry, rendered by the same node type
  // in 'creating' mode).
  const submitLocalDraft = useCallback((local: LocalDraft, input: {
    context: string; manualId?: string; manualDesc?: string
  }) => {
    startCreate({
      projectId: project.id,
      context: input.context,
      manualId: input.manualId,
      manualDesc: input.manualDesc,
      position: local.position,
    })
    setLocalDrafts(prev => prev.filter(d => d.draftId !== local.draftId))
  }, [project.id])

  const cancelLocalDraft = useCallback((draftId: string) => {
    setLocalDrafts(prev => prev.filter(d => d.draftId !== draftId))
  }, [])

  // Stable callback ref for onSelectTask
  const onSelectRef = useCallback((taskId: string) => {
    onSelectTask(taskId)
  }, [onSelectTask])

  // Toggle expand for done/closed mini nodes
  const onToggleExpandRef = useCallback((taskId: string) => {
    setExpandedNodes((prev) => {
      const next = { ...prev }
      if (next[taskId]) {
        delete next[taskId]
      } else {
        next[taskId] = true
      }
      return next
    })
  }, [])

  // Click on blank pane area -> reset selection
  const onPaneClick = useCallback(() => {
    onSelectTask(null)
    setExpandedNodes({})
    setContextMenu(null)
  }, [onSelectTask])

  // Right-click on pane -> show context menu (new task)
  // ReactFlow passes either a native MouseEvent (DOM) or a synthetic React event.
  const onPaneContextMenu = useCallback((event: MouseEvent | React.MouseEvent) => {
    event.preventDefault()
    setContextMenu({ x: event.clientX, y: event.clientY })
  }, [])

  // Right-click on node -> show node context menu (delete)
  const onNodeContextMenu = useCallback((event: MouseEvent | React.MouseEvent, node: Node) => {
    event.preventDefault()
    const data = node.data as TaskNodeData
    const task = project.tasks[data.taskId]
    setContextMenu({
      x: event.clientX, y: event.clientY,
      taskId: data.taskId,
      hasTicket: !!(task?.ticket_id),
    })
  }, [project.tasks])

  // (Inline draft / create flow lives in `pendingCreates` service +
  // `submitLocalDraft` callback above. The old in-component
  // `handleCreateTask` / `aiResponse` / `createDialog` state was
  // removed when we replaced the modal with on-graph nodes.)

  // Build highlight set from selectedTask
  const buildHighlightSet = useCallback((selected: string | null) => {
    if (!selected) return null
    const hSet: Record<string, boolean> = { [selected]: true }
    const walkUp = (tid: string) => {
      const t = (project.tasks || {})[tid]
      if (!t) return
      for (const d of t.dependencies || []) { if (!hSet[d]) { hSet[d] = true; walkUp(d) } }
    }
    const walkDown = (tid: string) => {
      for (const [oid, ot] of Object.entries(project.tasks || {})) {
        if ((ot.dependencies || []).includes(tid) && !hSet[oid]) { hSet[oid] = true; walkDown(oid) }
      }
    }
    walkUp(selected)
    walkDown(selected)
    return hSet
  }, [project.tasks])

  // Compute layout when graph structure changes (dagre is expensive, skip on selection change)
  const layoutRef = useRef<ReturnType<typeof getLayoutedElements> | null>(null)

  useEffect(() => {
    if (!graphData) return
    layoutRef.current = getLayoutedElements(
      graphData, null, project.tasks || {}, expandedNodes, positions,
    )
    // Seed any node we just dagre-positioned but isn't pinned in LS
    // yet so subsequent edge / status changes can't shake it. After
    // first render every node has an LS-anchored position, and from
    // there only manual drags move things.
    const seed: Record<string, { x: number; y: number }> = {}
    for (const n of layoutRef.current.nodes) {
      if (!positions[n.id]) {
        seed[n.id] = { x: n.position.x, y: n.position.y }
      }
    }
    if (Object.keys(seed).length) seedPositions(seed)
  }, [graphData, expandedNodes, project.tasks, positions, seedPositions])

  // When a pending smart-create resolves to a real task, seed the
  // project's layout position with whatever the user dragged the temp
  // node to. Without this the new node would land wherever dagre's
  // auto-layout puts it -- defeating the point of dropping the temp
  // node where you want the task to live.
  useEffect(() => {
    for (const p of pendingList) {
      if (p.taskId && p.position && !positions[p.taskId]) {
        setPosition(p.taskId, p.position)
      }
    }
  }, [pendingList, positions, setPosition])

  // Apply highlighting whenever layout, selectedTask, or tasks change.
  // Also append draft (local) and pending (in-flight smart-create)
  // nodes so the user can fill / watch them inline.
  useEffect(() => {
    if (!layoutRef.current) return
    const layout = layoutRef.current
    const hSet = buildHighlightSet(selectedTask)

    // Tasks already in the project shouldn't render a pending overlay
    // (the real TaskNode owns them); we just decorate them with NEW.
    const realTaskIds = new Set(layout.nodes.map(n => (n.data as TaskNodeData).taskId))
    // For each real task, find any pending entry whose taskId matches
    // -- these become the NEW-badged tasks. Map taskId -> pending.
    const pendingByTaskId: Record<string, typeof pendingList[number]> = {}
    for (const p of pendingList) {
      if (p.taskId && realTaskIds.has(p.taskId)) {
        pendingByTaskId[p.taskId] = p
      }
    }

    const taskNodes = layout.nodes.map((n) => {
      const data = n.data as TaskNodeData
      const highlighted = hSet ? !!hSet[data.taskId] : null
      const pending = pendingByTaskId[data.taskId]
      const isNewlyCreated = !!pending && pending.state === 'done'
      const isDropTarget = dropTargetId === data.taskId
      const isDragging = draggingNodeId === data.taskId
      return {
        ...n,
        // Force the dragging node to render above everything else,
        // including the drop target's glow shadow. React Flow's
        // default drag elevation isn't reliable against our custom
        // shadows.
        zIndex: isDragging ? 1000 : (isDropTarget ? 5 : 1),
        data: {
          ...data,
          // Selection updates per-render: getLayoutedElements only
          // computes `isSelected` once (with null), but the page's
          // selectedTask can change without re-running the layout.
          // Recomputing here keeps the modern halo in sync.
          isSelected: data.taskId === selectedTask,
          highlighted,
          isNewlyCreated,
          isDropTarget,
          onSelect: onSelectRef,
          onToggleExpand: onToggleExpandRef,
        },
        // Force full opacity while dragging -- the highlight-dim
        // logic would otherwise let the target node bleed through.
        style: { ...n.style, opacity: isDragging ? 1 : (hSet ? (highlighted ? 1 : 0.4) : 1) },
      }
    })

    // Local drafts (still being filled in by the user)
    const draftNodes = localDrafts.map(d => ({
      id: `draft-${d.draftId}`,
      type: 'draftTaskNode' as const,
      position: d.position,
      draggable: true,
      data: {
        mode: 'draft' as const,
        onSubmit: (input: { context: string; manualId?: string; manualDesc?: string }) =>
          submitLocalDraft(d, input),
        onCancel: () => cancelLocalDraft(d.draftId),
      },
      style: { padding: 0 },
    }))

    // Pending creates (smart-create in flight or recently failed).
    // Hide entries whose real task is now in project.tasks (handled
    // below by `realTaskIds.has`). Also hide entries that have
    // already completed successfully -- the brief gap before the
    // SSE-driven refetch lands the real task is fine; better than
    // showing a stuck "creating" spinner past completion.
    const pendingNodes = pendingList
      .filter(p => p.state === 'creating' || p.state === 'failed')
      .filter(p => !p.taskId || !realTaskIds.has(p.taskId))
      .map(p => ({
        id: `pending-${p.draftId}`,
        type: 'draftTaskNode' as const,
        position: p.position || { x: 0, y: 0 },
        draggable: true,
        data: {
          mode: p.state === 'failed' ? 'failed' as const : 'creating' as const,
          log: p.log,
          errorMsg: p.errorMsg,
          // Show the resolved task id once smart-create surfaces it,
          // else fall back to manual id, else first 30 chars of context.
          label: p.taskId || p.manualId ||
                 (p.context ? p.context.slice(0, 30) : ''),
          context: p.context,
          manualId: p.manualId,
          onDismiss: () => dismissCreate(p.draftId),
        },
        style: { padding: 0 },
      }))

    setNodes([...taskNodes, ...draftNodes, ...pendingNodes])
    setEdges(layout.edges.map((e) => {
      if (!hSet) return e
      const edgeHighlighted = !!(hSet[e.source] && hSet[e.target])
      return {
        ...e,
        style: { ...e.style, opacity: edgeHighlighted ? 1 : getThemeOpacity('--graph-dim-edge', 0.08), stroke: edgeHighlighted ? 'var(--blue)' : undefined },
        animated: edgeHighlighted,
      }
    }))
  }, [graphData, expandedNodes, selectedTask, onSelectRef, onToggleExpandRef,
      setNodes, setEdges, project.tasks, buildHighlightSet,
      localDrafts, pendingList, submitLocalDraft, cancelLocalDraft,
      dropTargetId, draggingNodeId])

  const minimapNodeColor = useCallback((node: Node) => {
    const data = node.data as TaskNodeData | undefined
    if (!data) return 'var(--text-faint)'
    return STATUS_COLORS[data.status] || 'var(--text-faint)'
  }, [])

  if (error) {
    return <p style={{ color: 'var(--text-dim)' }}>{error}</p>
  }

  if (!graphData) {
    return <p style={{ color: 'var(--text-dim)' }}>Loading graph...</p>
  }

  if (graphData.nodes.length === 0) {
    return <p style={{ color: 'var(--text-dim)' }}>No tasks to graph.</p>
  }

  return (
    <div data-testid="graph-view" style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      {/* Legend bar */}
      <div className="legend" style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span><span className="dot dot-done" /> Done</span>
        <span><span className="dot dot-needs_follow_up" /> Needs Follow-up</span>
        <span><span className="dot dot-in_review" /> In Review</span>
        <span><span className="dot dot-in_progress" /> In Progress</span>
        <span><span className="dot dot-not_started" /> Not Started</span>
        <span><span className="dot dot-blocked" /> Blocked</span>
        <span><span className="dot dot-closed" /> Closed</span>
        <button
          className="btn-action"
          style={{ marginLeft: 'auto', fontSize: 10 }}
          data-testid="graph-auto-position"
          title="Nudge nodes apart to remove overlaps while keeping your manual layout (DAG order preserved)"
          onClick={handleAutoPosition}
        >Auto Position</button>
        <button
          className="btn-action"
          style={{ fontSize: 10 }}
          title="Discard manual node positions and re-run dagre auto-layout"
          onClick={async () => {
            const ok = await confirm({
              title: 'Reset graph layout?',
              message: 'Discard your manual node positions for this project and re-run auto-layout.',
              confirmLabel: 'Reset',
            })
            if (ok) clearLayout()
          }}
        >Reset Layout</button>
      </div>

      {/* React Flow graph */}
      <div style={{ flex: 1, minHeight: 0 }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={handleNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeDrag={onNodeDrag}
          onNodeDragStop={onNodeDragStop}
          onConnect={onConnect}
          onEdgeContextMenu={onEdgeContextMenu}
          onPaneClick={onPaneClick}
          onPaneContextMenu={onPaneContextMenu}
          onNodeContextMenu={onNodeContextMenu}
          nodeTypes={nodeTypes}
          nodesDraggable={true}
          nodesConnectable={true}
          elementsSelectable={true}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          minZoom={0.05}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
          style={{ background: 'var(--sidebar-bg)' }}
        >
          <Background color="var(--node-badge-bg)" gap={20} />
          <Controls />
          <MiniMap
            nodeColor={minimapNodeColor}
            maskColor="rgba(0,0,0,0.7)"
            // Drag to pan + scroll to zoom on the minimap itself.
            // Without these flags React Flow renders the minimap as
            // a static overview only -- the user has to scroll the
            // main canvas to navigate, which is awkward on big graphs.
            pannable
            zoomable
            style={{ background: 'var(--sidebar-bg)' }}
          />
        </ReactFlow>

        {/* Context menu */}
        {contextMenu && (
          <div
            style={{
              position: 'fixed', left: contextMenu.x, top: contextMenu.y,
              background: 'var(--card-bg)', border: '1px solid var(--border)',
              borderRadius: 6, padding: 4, zIndex: 1000,
              boxShadow: '0 4px 12px var(--shadow-color)',
            }}
            onClick={() => setContextMenu(null)}
          >
            {!contextMenu.taskId && !contextMenu.edgeFrom && (
              <div
                className="menu-item"
                style={{ padding: '6px 16px', fontSize: 12, cursor: 'pointer', borderRadius: 4 }}
                onClick={() => {
                  // Place the draft node at the right-click point in
                  // *flow* coordinates (not screen). Without
                  // screenToFlowPosition the node would land somewhere
                  // unrelated when the canvas is panned/zoomed.
                  const flowPos = rfInstance.screenToFlowPosition({
                    x: contextMenu.x, y: contextMenu.y,
                  })
                  const draftId = (typeof crypto !== 'undefined' && crypto.randomUUID)
                    ? crypto.randomUUID()
                    : `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
                  setLocalDrafts(prev => [...prev, {
                    draftId,
                    position: { x: flowPos.x - DRAFT_NODE_W / 2, y: flowPos.y - DRAFT_NODE_H / 2 },
                  }])
                  setContextMenu(null)
                }}
              >
                + New Task
              </div>
            )}
            {contextMenu.edgeFrom && contextMenu.edgeTo && (
              <div
                className="menu-item"
                style={{ padding: '6px 16px', fontSize: 12, cursor: 'pointer', borderRadius: 4, color: 'var(--red)' }}
                onClick={() => {
                  const f = contextMenu.edgeFrom!
                  const t = contextMenu.edgeTo!
                  // Use the context-menu position as the popconfirm
                  // anchor so the confirmation bubble appears right
                  // where the user just clicked.
                  const anchor = { x: contextMenu.x, y: contextMenu.y }
                  setContextMenu(null)
                  removeDepEdge(f, t, anchor)
                }}
              >
                Remove dependency
                <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 2 }}>
                  {contextMenu.edgeTo} <span style={{ opacity: 0.5 }}>depends on</span> {contextMenu.edgeFrom}
                </div>
              </div>
            )}
            {contextMenu.taskId && !contextMenu.hasTicket && (
              <div
                className="menu-item"
                style={{ padding: '6px 16px', fontSize: 12, cursor: 'pointer', borderRadius: 4, color: 'var(--red)' }}
                onClick={async () => {
                  const tid = contextMenu.taskId!
                  setContextMenu(null)
                  const ok = await confirm({
                    title: `Delete task "${tid}"?`,
                    message: 'This cannot be undone.',
                    confirmLabel: 'Delete',
                    danger: true,
                  })
                  if (!ok) return
                  try {
                    await fetch(`/api/projects/${encodeURIComponent(project.id)}/tasks/${encodeURIComponent(tid)}`, { method: 'DELETE' })
                  } catch { /* ignore */ }
                }}
              >
                Delete Task
              </div>
            )}
            {contextMenu.taskId && contextMenu.hasTicket && (
              <div
                style={{ padding: '6px 16px', fontSize: 11, color: 'var(--text-faint)', cursor: 'not-allowed' }}
                title="Tasks with tickets cannot be deleted"
              >
                Delete (has ticket)
              </div>
            )}
          </div>
        )}

      </div>
    </div>
  )
})
