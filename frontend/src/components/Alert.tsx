import { useState, useCallback, useRef, useEffect } from 'react'
import type { ReactNode } from 'react'
import {
  AlertContext,
  type AlertContextValue,
  type AlertKind,
  type ConfirmOptions,
  type AlertOptions,
  type PromptOptions,
  type PromptCheckboxOption,
  type PromptCheckboxResult,
  type PopConfirmAnchor,
} from './alertContext'

export { useAlert } from './alertContext'

type Resolver = (v: unknown) => void

type Dialog =
  | ({ mode: 'confirm'; resolve: (v: boolean) => void } & ConfirmOptions)
  | ({ mode: 'popconfirm'; resolve: (v: boolean) => void; anchor: PopConfirmAnchor } & ConfirmOptions)
  | ({ mode: 'alert'; resolve: () => void } & AlertOptions)
  | ({ mode: 'prompt'; resolve: (v: string | null) => void } & PromptOptions)
  | ({ mode: 'promptCheckbox'; resolve: (v: PromptCheckboxResult | null) => void; checkbox: PromptCheckboxOption } & PromptOptions)

const KIND_COLOR: Record<AlertKind, string> = {
  info: 'var(--blue, #3b82f6)',
  warning: 'var(--yellow, #f59e0b)',
  error: 'var(--red, #ef4444)',
  success: 'var(--green, #22c55e)',
}

const KIND_GLYPH: Record<AlertKind, string> = {
  info: 'ⓘ',       // circled i
  warning: '⚠',    // warning sign
  error: '✖',      // heavy x
  success: '✔',    // heavy check
}

function BaseButton({
  onClick,
  children,
  variant = 'default',
  testId,
  autoFocus,
}: {
  onClick: () => void
  children: ReactNode
  variant?: 'default' | 'primary' | 'danger'
  testId?: string
  autoFocus?: boolean
}) {
  const bg = variant === 'danger' ? 'var(--red, #ef4444)'
           : variant === 'primary' ? 'var(--accent, #6366f1)'
           : 'transparent'
  const color = variant === 'default' ? 'var(--text)' : '#fff'
  const border = variant === 'default' ? '1px solid var(--border)' : '1px solid transparent'
  return (
    <button
      data-testid={testId}
      autoFocus={autoFocus}
      onClick={onClick}
      style={{
        padding: '6px 14px', borderRadius: 5, border, background: bg,
        color, fontSize: 12, fontFamily: 'inherit', cursor: 'pointer',
        fontWeight: 500, minWidth: 76,
      }}
    >
      {children}
    </button>
  )
}

function DialogFrame({
  kind = 'info',
  title,
  message,
  children,
  onEscape,
}: {
  kind?: AlertKind
  title: string
  message?: string
  children: ReactNode
  onEscape: () => void
}) {
  return (
    <div
      data-testid="alert-backdrop"
      onClick={onEscape}
      style={{
        position: 'fixed', inset: 0, zIndex: 10000,
        background: 'rgba(0,0,0,0.45)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        animation: 'alertFadeIn 120ms ease-out',
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        data-testid="alert-dialog"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--card-bg)',
          border: '1px solid var(--border)',
          borderRadius: 8,
          minWidth: 340, maxWidth: 480,
          boxShadow: '0 12px 40px rgba(0,0,0,0.5)',
          padding: 18,
          color: 'var(--text)',
          fontFamily: 'inherit',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
          <span
            aria-hidden="true"
            style={{
              flexShrink: 0, fontSize: 20, lineHeight: '20px',
              color: KIND_COLOR[kind], marginTop: 1,
            }}
          >
            {KIND_GLYPH[kind]}
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: message ? 6 : 12 }}>
              {title}
            </div>
            {message && (
              <div style={{
                fontSize: 12, color: 'var(--text-dim)',
                marginBottom: 12, lineHeight: 1.45,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>
                {message}
              </div>
            )}
            {children}
          </div>
        </div>
      </div>
    </div>
  )
}

function ConfirmBody({
  dlg, onClose,
}: { dlg: Extract<Dialog, { mode: 'confirm' }>; onClose: (v: boolean) => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onClose(false) }
      else if (e.key === 'Enter') { e.preventDefault(); onClose(true) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <DialogFrame kind={dlg.kind ?? (dlg.danger ? 'warning' : 'info')}
                 title={dlg.title} message={dlg.message}
                 onEscape={() => onClose(false)}>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <BaseButton testId="alert-cancel" onClick={() => onClose(false)}>
          {dlg.cancelLabel ?? 'Cancel'}
        </BaseButton>
        <BaseButton
          testId="alert-confirm"
          autoFocus
          variant={dlg.danger ? 'danger' : 'primary'}
          onClick={() => onClose(true)}
        >
          {dlg.confirmLabel ?? 'Confirm'}
        </BaseButton>
      </div>
    </DialogFrame>
  )
}

function PopConfirmBody({
  dlg, onClose,
}: { dlg: Extract<Dialog, { mode: 'popconfirm' }>; onClose: (v: boolean) => void }) {
  const popRef = useRef<HTMLDivElement | null>(null)
  // Fallback dimensions used for the very first render (before the
  // ref has measured the actual node). Keeps the bubble inside the
  // viewport even on its initial paint.
  const W = 240
  const H = 92

  // Tail geometry: declared up front so placement maths and the SVG
  // render share the same constants.
  const TAIL_W = 18
  const TAIL_H = 10
  const TAIL_INSET = 18           // gap from bubble corner to tail base
  const TAIL_TIP_X = TAIL_INSET + TAIL_W / 2  // tail tip's x-offset within the bubble
  const TAIL_REVEAL = TAIL_H - 1  // tail height minus the 1px overlap that hides the base seam
  const TAIL_GAP = 6              // pixels between tail tip and the anchor (cursor)

  // Compute placement so the TAIL TIP lands `TAIL_GAP` pixels above
  // (or below, when flipped) the cursor -- not the bubble corner.
  // That makes the popover visually connected to the thing the user
  // just clicked: the triangle literally points at it.
  const padding = 8
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1024
  const vh = typeof window !== 'undefined' ? window.innerHeight : 768
  // Default: bubble above the cursor, tail at bottom-left, tip down.
  // tail tip at (bubble.left + TAIL_TIP_X, bubble.bottom + TAIL_REVEAL)
  // so we want bubble.left = anchor.x - TAIL_TIP_X and
  // bubble.bottom + TAIL_REVEAL = anchor.y - TAIL_GAP.
  let left = dlg.anchor.x - TAIL_TIP_X
  let top = dlg.anchor.y - TAIL_GAP - TAIL_REVEAL - H
  let flippedH = false
  let flippedV = false
  if (left + W + padding > vw) {
    // Flip the tail to bottom-right: bubble shifts left so that the
    // tail (at the right side of the bubble now) still tips at anchor.x.
    left = dlg.anchor.x - W + TAIL_TIP_X
    flippedH = true
  }
  if (top < padding) {
    // Flip vertically: bubble sits below the cursor, tail at top.
    top = dlg.anchor.y + TAIL_GAP + TAIL_REVEAL
    flippedV = true
  }
  // Hard-clamp to viewport in case both flips still leave us spilling
  // (tiny viewports / cursor near a corner). Worst case the tip drifts
  // off the cursor by a few pixels, which is still better than the
  // bubble being half off-screen.
  left = Math.max(padding, Math.min(left, vw - W - padding))
  top = Math.max(padding, Math.min(top, vh - H - padding))

  // Tail (chat-bubble pointer). A real comic-style triangle drawn as
  // SVG: filled with the bubble's background, stroked on its TWO
  // sloped edges only (no stroke on the side that meets the bubble),
  // so it visually fuses with the bubble's border. `drop-shadow` on
  // the SVG makes the tail's shadow continuous with the bubble's
  // own box-shadow, so the whole thing reads as one shape.
  const tailVert: 'bottom' | 'top' = flippedV ? 'top' : 'bottom'
  const tailHoriz: 'left' | 'right' = flippedH ? 'right' : 'left'
  // Path is a triangle WITHOUT closing the base, so `stroke` only
  // draws the two visible (sloped) sides. `fill` still closes the
  // shape implicitly to fill the interior with the bubble bg.
  const tailPathD = tailVert === 'bottom'
    ? `M 0 0 L ${TAIL_W / 2} ${TAIL_H} L ${TAIL_W} 0`
    : `M 0 ${TAIL_H} L ${TAIL_W / 2} 0 L ${TAIL_W} ${TAIL_H}`
  const tailStyle: React.CSSProperties = {
    position: 'absolute',
    overflow: 'visible',
    pointerEvents: 'none',
    filter: 'drop-shadow(0 3px 4px rgba(0,0,0,0.20))',
    // Sit flush against the bubble border -- 1px overlap so the
    // tail's open base meets (and is hidden by) the bubble's
    // bottom/top border with no visible seam.
    ...(tailVert === 'bottom' ? { bottom: -TAIL_H + 1 } : { top: -TAIL_H + 1 }),
    ...(tailHoriz === 'left' ? { left: 18 } : { right: 18 }),
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onClose(false) }
      else if (e.key === 'Enter') { e.preventDefault(); onClose(true) }
    }
    const onDown = (e: MouseEvent) => {
      // Click outside the popover dismisses (cancel). Defer one tick
      // so the click that opened the popover doesn't count as
      // "outside".
      if (popRef.current && !popRef.current.contains(e.target as Node)) {
        onClose(false)
      }
    }
    window.addEventListener('keydown', onKey)
    // capture: true so we beat any handler that might stopPropagation.
    const id = setTimeout(() => document.addEventListener('mousedown', onDown, true), 0)
    return () => {
      window.removeEventListener('keydown', onKey)
      clearTimeout(id)
      document.removeEventListener('mousedown', onDown, true)
    }
  }, [onClose])

  const danger = dlg.danger
  return (
    <div
      ref={popRef}
      role="dialog"
      data-testid="popconfirm"
      data-kind={dlg.kind ?? (danger ? 'warning' : 'info')}
      style={{
        position: 'fixed',
        left, top,
        width: W,
        zIndex: 10000,
        background: 'var(--card-bg)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        boxShadow: '0 6px 22px rgba(0,0,0,0.35)',
        padding: 10,
        color: 'var(--text)',
        fontFamily: 'inherit',
        animation: 'alertFadeIn 110ms ease-out',
      }}
    >
      <svg
        data-testid="popconfirm-tail"
        width={TAIL_W} height={TAIL_H}
        viewBox={`0 0 ${TAIL_W} ${TAIL_H}`}
        style={tailStyle}
      >
        <path
          d={tailPathD}
          fill="var(--card-bg)"
          stroke="var(--border)"
          strokeWidth={1}
          strokeLinejoin="round"
        />
      </svg>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: dlg.message ? 4 : 8 }}>
        {dlg.title}
      </div>
      {dlg.message && (
        <div style={{
          fontSize: 11, color: 'var(--text-dim)',
          marginBottom: 8, lineHeight: 1.4,
          whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        }}>
          {dlg.message}
        </div>
      )}
      <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
        <button
          data-testid="popconfirm-cancel"
          onClick={() => onClose(false)}
          style={{
            padding: '3px 10px', borderRadius: 4,
            border: '1px solid var(--border)',
            background: 'transparent', color: 'var(--text)',
            fontSize: 11, fontFamily: 'inherit', cursor: 'pointer',
          }}
        >
          {dlg.cancelLabel ?? 'Cancel'}
        </button>
        <button
          data-testid="popconfirm-confirm"
          autoFocus
          onClick={() => onClose(true)}
          style={{
            padding: '3px 10px', borderRadius: 4,
            border: '1px solid transparent',
            background: danger ? 'var(--red, #ef4444)' : 'var(--accent, #6366f1)',
            color: '#fff',
            fontSize: 11, fontWeight: 600, fontFamily: 'inherit', cursor: 'pointer',
          }}
        >
          {dlg.confirmLabel ?? 'Confirm'}
        </button>
      </div>
    </div>
  )
}

function AlertBody({
  dlg, onClose,
}: { dlg: Extract<Dialog, { mode: 'alert' }>; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || e.key === 'Enter') {
        e.preventDefault(); onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <DialogFrame kind={dlg.kind ?? 'info'} title={dlg.title} message={dlg.message}
                 onEscape={onClose}>
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <BaseButton testId="alert-ok" variant="primary" autoFocus onClick={onClose}>
          {dlg.okLabel ?? 'OK'}
        </BaseButton>
      </div>
    </DialogFrame>
  )
}

function PromptBody({
  dlg, onClose,
}: { dlg: Extract<Dialog, { mode: 'prompt' }>; onClose: (v: string | null) => void }) {
  const [value, setValue] = useState(dlg.defaultValue ?? '')
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null)

  useEffect(() => {
    // Focus + select on mount.
    const el = inputRef.current
    if (el) { el.focus(); el.select?.() }
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onClose(null) }
      else if (e.key === 'Enter' && !dlg.multiline) {
        e.preventDefault(); onClose(value)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [value, dlg.multiline, onClose])

  const inputStyle = {
    width: '100%',
    background: 'var(--panel-bg, var(--bg))',
    color: 'var(--text)',
    border: '1px solid var(--border)',
    borderRadius: 5,
    padding: '6px 8px',
    fontSize: 12,
    fontFamily: 'inherit',
    outline: 'none',
    boxSizing: 'border-box' as const,
    marginBottom: 12,
  }

  return (
    <DialogFrame kind="info" title={dlg.title} message={dlg.message}
                 onEscape={() => onClose(null)}>
      {dlg.multiline ? (
        <textarea
          ref={(el) => { inputRef.current = el }}
          data-testid="alert-prompt-input"
          value={value}
          placeholder={dlg.placeholder}
          onChange={(e) => setValue(e.target.value)}
          style={{ ...inputStyle, minHeight: 80, resize: 'vertical' as const }}
        />
      ) : (
        <input
          ref={(el) => { inputRef.current = el }}
          data-testid="alert-prompt-input"
          type="text"
          value={value}
          placeholder={dlg.placeholder}
          onChange={(e) => setValue(e.target.value)}
          style={inputStyle}
        />
      )}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <BaseButton testId="alert-cancel" onClick={() => onClose(null)}>
          {dlg.cancelLabel ?? 'Cancel'}
        </BaseButton>
        <BaseButton testId="alert-confirm" variant="primary" onClick={() => onClose(value)}>
          {dlg.confirmLabel ?? 'OK'}
        </BaseButton>
      </div>
    </DialogFrame>
  )
}

// PromptBody + a single checkbox row. Used for flows that need both a
// reason text and a yes/no choice in one dialog (e.g. close task with
// optional `Close linked ticket`). Kept as a separate component so
// the plain-prompt path stays minimal.
function PromptCheckboxBody({
  dlg, onClose,
}: { dlg: Extract<Dialog, { mode: 'promptCheckbox' }>; onClose: (v: PromptCheckboxResult | null) => void }) {
  const [value, setValue] = useState(dlg.defaultValue ?? '')
  const [checked, setChecked] = useState(!!dlg.checkbox.defaultChecked)
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null)

  useEffect(() => {
    const el = inputRef.current
    if (el) { el.focus(); el.select?.() }
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onClose(null) }
      else if (e.key === 'Enter' && !dlg.multiline) {
        e.preventDefault(); onClose({ value, checked })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [value, checked, dlg.multiline, onClose])

  const inputStyle = {
    width: '100%',
    background: 'var(--panel-bg, var(--bg))',
    color: 'var(--text)',
    border: '1px solid var(--border)',
    borderRadius: 5,
    padding: '6px 8px',
    fontSize: 12,
    fontFamily: 'inherit',
    outline: 'none',
    boxSizing: 'border-box' as const,
    marginBottom: 8,
  }

  return (
    <DialogFrame kind="info" title={dlg.title} message={dlg.message}
                 onEscape={() => onClose(null)}>
      {dlg.multiline ? (
        <textarea
          ref={(el) => { inputRef.current = el }}
          data-testid="alert-prompt-input"
          value={value}
          placeholder={dlg.placeholder}
          onChange={(e) => setValue(e.target.value)}
          style={{ ...inputStyle, minHeight: 80, resize: 'vertical' as const }}
        />
      ) : (
        <input
          ref={(el) => { inputRef.current = el }}
          data-testid="alert-prompt-input"
          type="text"
          value={value}
          placeholder={dlg.placeholder}
          onChange={(e) => setValue(e.target.value)}
          style={inputStyle}
        />
      )}
      <label
        data-testid="alert-prompt-checkbox-row"
        style={{
          display: 'flex', alignItems: 'center', gap: 6,
          fontSize: 12, color: 'var(--text)',
          marginBottom: 12, cursor: 'pointer', userSelect: 'none',
        }}
      >
        <input
          type="checkbox"
          data-testid="alert-prompt-checkbox"
          checked={checked}
          onChange={(e) => setChecked(e.target.checked)}
        />
        {dlg.checkbox.label}
      </label>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <BaseButton testId="alert-cancel" onClick={() => onClose(null)}>
          {dlg.cancelLabel ?? 'Cancel'}
        </BaseButton>
        <BaseButton testId="alert-confirm" variant="primary"
                    onClick={() => onClose({ value, checked })}>
          {dlg.confirmLabel ?? 'OK'}
        </BaseButton>
      </div>
    </DialogFrame>
  )
}

export function AlertProvider({ children }: { children: ReactNode }) {
  const [queue, setQueue] = useState<Dialog[]>([])

  const push = useCallback(<D extends Dialog>(d: D) => {
    setQueue((q) => [...q, d])
  }, [])

  const resolveTop = useCallback((value: unknown) => {
    setQueue((q) => {
      if (!q.length) return q
      const [top, ...rest] = q
      ;(top.resolve as Resolver)(value)
      return rest
    })
  }, [])

  const confirm = useCallback((opts: ConfirmOptions) => new Promise<boolean>((resolve) => {
    push({ mode: 'confirm', resolve, ...opts })
  }), [push])

  const confirmAt = useCallback(
    (opts: ConfirmOptions, anchor: PopConfirmAnchor) => new Promise<boolean>((resolve) => {
      push({ mode: 'popconfirm', resolve, anchor, ...opts })
    }),
    [push],
  )

  const alert = useCallback((opts: AlertOptions) => new Promise<void>((resolve) => {
    push({ mode: 'alert', resolve, ...opts })
  }), [push])

  const prompt = useCallback((opts: PromptOptions) => new Promise<string | null>((resolve) => {
    push({ mode: 'prompt', resolve, ...opts })
  }), [push])

  const promptWithCheckbox = useCallback(
    (opts: PromptOptions & { checkbox: PromptCheckboxOption }) =>
      new Promise<PromptCheckboxResult | null>((resolve) => {
        push({ mode: 'promptCheckbox', resolve, ...opts })
      }),
    [push],
  )

  const value: AlertContextValue = {
    confirm, confirmAt, alert, prompt, promptWithCheckbox,
  }
  const top = queue[0]

  return (
    <AlertContext.Provider value={value}>
      {children}
      {top && top.mode === 'confirm' && (
        <ConfirmBody dlg={top} onClose={(v) => resolveTop(v)} />
      )}
      {top && top.mode === 'popconfirm' && (
        <PopConfirmBody dlg={top} onClose={(v) => resolveTop(v)} />
      )}
      {top && top.mode === 'alert' && (
        <AlertBody dlg={top} onClose={() => resolveTop(undefined)} />
      )}
      {top && top.mode === 'prompt' && (
        <PromptBody dlg={top} onClose={(v) => resolveTop(v)} />
      )}
      {top && top.mode === 'promptCheckbox' && (
        <PromptCheckboxBody dlg={top} onClose={(v) => resolveTop(v)} />
      )}
    </AlertContext.Provider>
  )
}
