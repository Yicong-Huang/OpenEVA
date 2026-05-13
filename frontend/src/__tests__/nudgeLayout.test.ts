import { describe, it, expect } from 'vitest'
import { nudgeLayout } from '../utils/nudgeLayout'

const SIZE = { w: 240, h: 88 }
const MINI = { w: 240, h: 22 }

describe('nudgeLayout (dagre + parking lot)', () => {
  it('returns the same dictionary when no nodes are present', () => {
    const out = nudgeLayout({}, [], {})
    expect(out).toEqual({})
  })

  it('lays a chain so depender x > dependee x', () => {
    // a -> b -> c. Even with the user's input mis-ordered, dagre
    // restores left-to-right DAG order.
    const positions = {
      a: { x: 999, y: 0 },
      b: { x: 0,   y: 0 },
      c: { x: 500, y: 0 },
    }
    const out = nudgeLayout(
      positions,
      [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }],
      { a: SIZE, b: SIZE, c: SIZE },
    )
    expect(out.a.x).toBeLessThan(out.b.x)
    expect(out.b.x).toBeLessThan(out.c.x)
  })

  it('places sibling depender nodes in the same rank column', () => {
    // a -> b, a -> c. b and c siblings: same x, different y.
    const positions = {
      a: { x: 0, y: 0 },
      b: { x: 0, y: 0 },
      c: { x: 0, y: 0 },
    }
    const out = nudgeLayout(
      positions,
      [{ from: 'a', to: 'b' }, { from: 'a', to: 'c' }],
      { a: SIZE, b: SIZE, c: SIZE },
    )
    expect(out.b.x).toBe(out.c.x)
    expect(out.b.y).not.toBe(out.c.y)
  })

  it('parks an isolated done node to the left of the main graph', () => {
    // `parked` has no edges -> isolated done. Should land left of
    // the main graph's leftmost node after dagre.
    const positions = {
      a: { x: 0, y: 0 },
      b: { x: 0, y: 0 },
      parked: { x: 0, y: 0 },
    }
    const out = nudgeLayout(
      positions,
      [{ from: 'a', to: 'b' }],
      { a: SIZE, b: SIZE, parked: MINI },
      { doneIds: new Set(['parked']) },
    )
    // Main graph nodes are dagre-positioned; parked is left of them.
    const mainMinX = Math.min(out.a.x, out.b.x)
    expect(out.parked.x).toBeLessThan(mainMinX)
  })

  it('does NOT park a done node that has any neighbour', () => {
    // dep -> done. Both done. Even without active neighbours, the
    // edge between them keeps both in the dagre-laid main graph.
    const positions = {
      dep: { x: 0, y: 0 },
      done: { x: 0, y: 0 },
      active: { x: 0, y: 0 },
    }
    const out = nudgeLayout(
      positions,
      [{ from: 'dep', to: 'done' }, { from: 'done', to: 'active' }],
      { dep: MINI, done: MINI, active: SIZE },
      { doneIds: new Set(['dep', 'done']) },
    )
    // dep < done < active in x (dagre LR ordering).
    expect(out.dep.x).toBeLessThan(out.done.x)
    expect(out.done.x).toBeLessThan(out.active.x)
  })

  it('stacks multiple parked done nodes vertically', () => {
    const positions: Record<string, { x: number; y: number }> = {
      active: { x: 0, y: 0 },
    }
    const sizes: Record<string, { w: number; h: number }> = { active: SIZE }
    for (let i = 0; i < 4; i++) {
      positions[`d${i}`] = { x: 0, y: 1000 - i * 100 }
      sizes[`d${i}`] = MINI
    }
    const out = nudgeLayout(
      positions, [], sizes,
      { doneIds: new Set(['d0', 'd1', 'd2', 'd3']) },
    )
    // All four parked at the same x (parking-lot column).
    const xs = ['d0', 'd1', 'd2', 'd3'].map(id => out[id].x)
    expect(new Set(xs).size).toBe(1)
    // d3 was input topmost (y=700) -- stays topmost in the parked
    // stack. d0 was bottom-most (y=1000) -- stays bottom-most.
    expect(out.d3.y).toBeLessThan(out.d2.y)
    expect(out.d2.y).toBeLessThan(out.d1.y)
    expect(out.d1.y).toBeLessThan(out.d0.y)
  })

  it('is idempotent: a second pass produces identical output', () => {
    const positions = {
      a: { x: 100, y: 0 },
      b: { x: 500, y: 100 },
      c: { x: 800, y: 200 },
      parked: { x: 1200, y: 0 },
    }
    const sizes = { a: SIZE, b: SIZE, c: SIZE, parked: MINI }
    const opts = { doneIds: new Set(['parked']) }
    const edges = [
      { from: 'a', to: 'b' },
      { from: 'b', to: 'c' },
    ]
    const pass1 = nudgeLayout(positions, edges, sizes, opts)
    const pass2 = nudgeLayout(pass1, edges, sizes, opts)
    for (const id of Object.keys(pass1)) {
      expect(pass2[id]).toEqual(pass1[id])
    }
  })

  it('does not mutate input positions object', () => {
    const positions = {
      a: { x: 100, y: 100 },
      b: { x: 500, y: 200 },
    }
    const snapshot = JSON.parse(JSON.stringify(positions))
    nudgeLayout(
      positions,
      [{ from: 'a', to: 'b' }],
      { a: SIZE, b: SIZE },
    )
    expect(positions).toEqual(snapshot)
  })

  it('gracefully handles edges referencing missing nodes', () => {
    const positions = { a: { x: 0, y: 0 } }
    const out = nudgeLayout(
      positions,
      [{ from: 'a', to: 'gone' }, { from: 'gone', to: 'a' }],
      { a: SIZE },
    )
    expect(out.a).toBeDefined()
  })

  it('snaps coordinates to the grid by default', () => {
    const positions = {
      a: { x: 0, y: 0 },
      b: { x: 0, y: 0 },
    }
    const out = nudgeLayout(
      positions, [{ from: 'a', to: 'b' }],
      { a: SIZE, b: SIZE },
    )
    // % on negatives can be -0; absolute zero is what we care about.
    expect(Math.abs(out.a.x % 8)).toBe(0)
    expect(Math.abs(out.a.y % 8)).toBe(0)
    expect(Math.abs(out.b.x % 8)).toBe(0)
    expect(Math.abs(out.b.y % 8)).toBe(0)
  })

  it('reduces edge crossings on the diamond + cross-link case', () => {
    // a -> b, a -> c, b -> d, c -> d, b -> e, c -> e where e and d
    // are the deeper rank's two "leaves". With a poor initial
    // ordering an extra cross can appear (b->e and c->d crossing);
    // dagre's barycenter pass should route them so b ends up on
    // the same side as d, c on the same side as e (or vice versa).
    // We assert a structural property: within rank 1, b and c are
    // assigned distinct y; within rank 2, d and e are assigned
    // distinct y. (Hard to assert "no crossing" precisely without
    // re-implementing the geometry; this guards against regressions
    // where dagre would have collapsed siblings to the same y.)
    const positions = {
      a: { x: 0, y: 0 },
      b: { x: 0, y: 0 },
      c: { x: 0, y: 0 },
      d: { x: 0, y: 0 },
      e: { x: 0, y: 0 },
    }
    const out = nudgeLayout(
      positions,
      [
        { from: 'a', to: 'b' }, { from: 'a', to: 'c' },
        { from: 'b', to: 'd' }, { from: 'c', to: 'd' },
        { from: 'b', to: 'e' }, { from: 'c', to: 'e' },
      ],
      { a: SIZE, b: SIZE, c: SIZE, d: SIZE, e: SIZE },
    )
    expect(out.b.y).not.toBe(out.c.y)
    expect(out.d.y).not.toBe(out.e.y)
  })
})
