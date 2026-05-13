/**
 * "Auto Position" smart layout for the task graph.
 *
 * Strategy:
 *   1. Run dagre's LR layered layout on the live edges. Dagre uses
 *      barycenter ordering to minimise edge crossings within each
 *      rank, so siblings end up in an order that keeps the
 *      dependency wiring untangled even if the user's manual drags
 *      had introduced crossings.
 *   2. Pull degree-0 done nodes ("floating completed singletons")
 *      out of the dagre canvas and stack them in a left-side
 *      parking lot. Done tasks that are still wired to anything
 *      stay in the main graph; only fully orphan dones get parked.
 *   3. Grid-snap the result so coordinates feel tidy.
 *
 * Why give up the "preserve user positions" promise:
 *   - The user wanted edges to not zig-zag and the canvas to feel
 *     compact. Both goals require re-ordering siblings within
 *     ranks. Sticking to "minimal touch" let too much manual drag
 *     noise survive into the rendered graph.
 *   - Dagre is deterministic, so running Auto Position twice in a
 *     row produces identical output (idempotent).
 *
 * Pure function. No DOM, no React.
 */

import dagre from 'dagre'

export interface XY { x: number; y: number }
export interface Size { w: number; h: number }
export interface NudgeEdge { from: string; to: string }

export interface NudgeOptions {
  /** Node ids treated as "done / closed". Required for the parking
   *  lot logic; without it the function reduces to a pure dagre
   *  re-layout. */
  doneIds?: Set<string> | null
  /** Vertical gap between two stacked nodes in the parking lot. */
  donePadY?: number
  /** Horizontal gap between the parking lot column and the main
   *  graph's leftmost node. */
  parkingGap?: number
  /** Dagre `nodesep`: vertical gap between nodes in the same rank. */
  nodesep?: number
  /** Dagre `ranksep`: horizontal gap between adjacent ranks. */
  ranksep?: number
  /** Final coordinate snapping. 0 disables. */
  gridSize?: number
}

const DEFAULTS: Required<NudgeOptions> = {
  doneIds: null,
  donePadY: 8,
  parkingGap: 80,
  nodesep: 14,
  ranksep: 50,
  gridSize: 8,
}

export function nudgeLayout(
  positions: Record<string, XY>,
  edges: NudgeEdge[],
  sizes: Record<string, Size>,
  opts: NudgeOptions = {},
): Record<string, XY> {
  const cfg = { ...DEFAULTS, ...opts }
  const ids = Object.keys(positions)
  if (ids.length === 0) return positions

  const doneIds = cfg.doneIds ?? new Set<string>()
  const liveEdges = edges.filter(e => positions[e.from] && positions[e.to])

  // Per-node neighbour set so the isolated-done filter is O(deg).
  const neighbours: Record<string, Set<string>> = {}
  for (const id of ids) neighbours[id] = new Set<string>()
  for (const e of liveEdges) {
    neighbours[e.from].add(e.to)
    neighbours[e.to].add(e.from)
  }
  const isolatedDone = new Set<string>()
  for (const id of ids) {
    if (doneIds.has(id) && neighbours[id].size === 0) {
      isolatedDone.add(id)
    }
  }

  // ---- 1. Dagre layout for the main graph -------------------------------
  // Excludes isolated-done nodes; they don't participate in the layered
  // layout because they have no edges to inform their placement.
  const mainIds = ids.filter(id => !isolatedDone.has(id))
  const out: Record<string, XY> = {}

  if (mainIds.length > 0) {
    const g = new dagre.graphlib.Graph()
    g.setDefaultEdgeLabel(() => ({}))
    g.setGraph({
      rankdir: 'LR',
      nodesep: cfg.nodesep,
      ranksep: cfg.ranksep,
    })
    for (const id of mainIds) {
      const s = sizes[id] ?? { w: 0, h: 0 }
      g.setNode(id, { width: s.w, height: s.h })
    }
    for (const e of liveEdges) {
      if (!isolatedDone.has(e.from) && !isolatedDone.has(e.to)) {
        g.setEdge(e.from, e.to)
      }
    }
    dagre.layout(g)
    for (const id of mainIds) {
      const pos = g.node(id)
      const s = sizes[id] ?? { w: 0, h: 0 }
      // dagre returns (cx, cy); React Flow expects top-left.
      out[id] = { x: pos.x - s.w / 2, y: pos.y - s.h / 2 }
    }
  }

  // ---- 2. Parking lot for isolated done nodes ---------------------------
  if (isolatedDone.size > 0) {
    // Anchor: leftmost / topmost main-graph corner. If everything is
    // an isolated done, fall back to the input bounding box.
    let mainMinX = Infinity
    let mainMinY = Infinity
    let parkedNodeWidth = 0
    for (const id of mainIds) {
      if (out[id].x < mainMinX) mainMinX = out[id].x
      if (out[id].y < mainMinY) mainMinY = out[id].y
    }
    for (const id of isolatedDone) {
      const w = sizes[id]?.w ?? 0
      if (w > parkedNodeWidth) parkedNodeWidth = w
    }
    if (!Number.isFinite(mainMinX)) {
      mainMinX = 0
      for (const id of ids) {
        if (positions[id].x < mainMinX) mainMinX = positions[id].x
      }
    }
    if (!Number.isFinite(mainMinY)) {
      mainMinY = 0
      for (const id of ids) {
        if (positions[id].y < mainMinY) mainMinY = positions[id].y
      }
    }
    const parkingX = mainMinX - cfg.parkingGap - parkedNodeWidth

    // Sort by current y so the user's existing top-to-bottom order
    // among the parked items is preserved. Tie-break by id for
    // determinism across reloads (idempotent).
    const parkedSorted = Array.from(isolatedDone).sort((a, b) => {
      const dy = positions[a].y - positions[b].y
      if (dy !== 0) return dy
      return a < b ? -1 : a > b ? 1 : 0
    })
    let cursorY = mainMinY
    for (const id of parkedSorted) {
      const s = sizes[id]
      out[id] = { x: parkingX, y: cursorY }
      cursorY += (s ? s.h : 0) + cfg.donePadY
    }
  }

  // ---- 3. Grid snap -----------------------------------------------------
  if (cfg.gridSize > 0) {
    const g = cfg.gridSize
    for (const id of ids) {
      out[id].x = Math.round(out[id].x / g) * g
      out[id].y = Math.round(out[id].y / g) * g
    }
  }

  return out
}
