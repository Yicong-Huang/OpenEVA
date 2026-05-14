import { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import type { PRDetail, PendingReviewComment } from '../../types'
import { api } from '../../api'
import { highlightLine } from '../../utils/syntaxHighlight'

interface FileListProps {
  files: PRDetail['files']
  repo: string
  prNumber: number
  onComment?: (path: string, line: number, body: string) => void
  onAskAgent?: (context: string) => void
  /** Pending-review comments on this PR. The file row displays an icon
   * + count when any pending comment matches the file's path so the
   * user can see at a glance which files still have unsubmitted drafts
   * without expanding every diff. */
  pendingComments?: PendingReviewComment[]
  /** Called when the user hits the remove button on an inline pending
   *  comment. The parent owns the API call + state refresh. */
  onDeletePending?: (commentId: number) => void
  /** Save a new body for an existing pending comment. */
  onEditPending?: (commentId: number, body: string) => Promise<void>
  /** Forward a pending comment to the review agent session (one-off
   *  "refine this" prompt). Only wired in review mode where the
   *  agent session is the review session itself. */
  onAskAgentPending?: (pc: PendingReviewComment) => void
}

/** Parse a unified diff string into renderable lines with line numbers
 *  AND the GitHub-style `position` for each row. `position` is the
 *  1-based offset from the first `@@` header (everything below it
 *  counts, including subsequent hunk headers) -- this is the key
 *  GitHub uses to anchor draft review comments while `line` is still
 *  null on the draft. */
function parseDiff(raw: string): Array<{
  type: 'header' | 'add' | 'remove' | 'context' | 'info'
  text: string
  oldLine: number | null
  newLine: number | null
  position: number | null
}> {
  const lines: ReturnType<typeof parseDiff> = []
  let oldLine = 0
  let newLine = 0
  let position: number | null = null  // null until first @@ seen

  for (const line of raw.split('\n')) {
    if (line.startsWith('@@')) {
      // Parse hunk header: @@ -old,len +new,len @@
      const match = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)/)
      if (match) {
        oldLine = parseInt(match[1], 10)
        newLine = parseInt(match[2], 10)
      }
      // First @@ -> position 0 (the header itself is position 0 per
      // GitHub's convention); subsequent @@ headers are part of the
      // running diff body and bump the counter normally.
      position = position === null ? 0 : position + 1
      lines.push({ type: 'info', text: line, oldLine: null, newLine: null, position })
    } else if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ')) {
      // File header rows precede the first @@ -- they don't take a position.
      lines.push({ type: 'header', text: line, oldLine: null, newLine: null, position: null })
    } else if (line.startsWith('+')) {
      position = position === null ? null : position + 1
      lines.push({ type: 'add', text: line, oldLine: null, newLine: newLine, position })
      newLine++
    } else if (line.startsWith('-')) {
      position = position === null ? null : position + 1
      lines.push({ type: 'remove', text: line, oldLine: oldLine, newLine: null, position })
      oldLine++
    } else {
      position = position === null ? null : position + 1
      lines.push({ type: 'context', text: line, oldLine: oldLine, newLine: newLine, position })
      oldLine++
      newLine++
    }
  }
  return lines
}

const LINE_STYLES: Record<string, { color: string; bg: string; borderLeft?: string }> = {
  add: { color: 'var(--text)', bg: 'rgba(34,197,94,0.12)', borderLeft: '3px solid var(--green)' },
  remove: { color: 'var(--text)', bg: 'rgba(239,68,68,0.12)', borderLeft: '3px solid var(--red)' },
  info: { color: 'var(--blue)', bg: 'rgba(59,130,246,0.06)' },
  header: { color: 'var(--text-dim)', bg: 'transparent' },
  context: { color: 'var(--text)', bg: 'transparent' },
}

/** Floating bubble with input box + Comment / Ask Agent buttons */
function SelectionBubble({ x, y, containerRef, codeRef, onComment, onAskAgent, onClose }: {
  x: number; y: number
  containerRef: React.RefObject<HTMLDivElement | null>
  codeRef: string  // selected code snippet for display
  onComment: (text: string) => void
  onAskAgent?: (text: string) => void
  onClose: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const [text, setText] = useState('')

  // Focus input on mount
  useEffect(() => { inputRef.current?.focus() }, [])

  // Click outside to close
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  // Position: clamp inside container bounds
  const bubbleWidth = 300
  const bubbleHeight = 140
  let left = x
  let top = y + 4

  if (containerRef.current) {
    const cr = containerRef.current.getBoundingClientRect()
    // Clamp right edge
    if (left + bubbleWidth > cr.right - 8) left = cr.right - bubbleWidth - 8
    // Clamp left edge
    if (left < cr.left + 8) left = cr.left + 8
    // If would overflow bottom, show above selection
    if (top + bubbleHeight > cr.bottom - 8) top = y - bubbleHeight - 4
    // Clamp top
    if (top < cr.top + 8) top = cr.top + 8
  }

  return (
    <div
      ref={ref}
      style={{
        position: 'fixed', left, top, width: bubbleWidth, zIndex: 1000,
        background: 'var(--card-bg)', border: '1px solid var(--border)',
        borderRadius: 8, boxShadow: '0 4px 16px var(--shadow-color)',
        padding: 10, fontSize: 11,
      }}
    >
      {/* Code preview */}
      <div style={{
        fontSize: 9, fontFamily: 'monospace', color: 'var(--text-dim)',
        background: 'var(--panel-bg)', borderRadius: 4, padding: '4px 6px',
        maxHeight: 36, overflow: 'hidden', marginBottom: 6,
        whiteSpace: 'pre', textOverflow: 'ellipsis',
      }}>
        {codeRef.split('\n').slice(0, 2).join('\n')}{codeRef.split('\n').length > 2 ? '...' : ''}
      </div>

      {/* Input */}
      <textarea
        ref={inputRef}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Comment or question about this code..."
        onKeyDown={(e) => {
          if (e.key === 'Escape') onClose()
          if (e.key === 'Enter' && !e.shiftKey && text.trim()) {
            e.preventDefault()
            if (onAskAgent) { onAskAgent(text.trim()) } else { onComment(text.trim()) }
            onClose()
          }
        }}
        style={{
          width: '100%', minHeight: 40, padding: '6px 8px',
          background: 'var(--panel-bg)', border: '1px solid var(--border)',
          borderRadius: 4, color: 'var(--text)', fontSize: 11,
          fontFamily: 'inherit', resize: 'none',
        }}
      />

      {/* Buttons */}
      <div style={{ display: 'flex', gap: 6, marginTop: 6, justifyContent: 'flex-end' }}>
        <button
          className="btn-action"
          style={{ fontSize: 10, padding: '3px 10px' }}
          disabled={!text.trim()}
          onClick={() => { onComment(text.trim()); onClose() }}
        >
          Comment
        </button>
        {onAskAgent && (
          <button
            className="btn-action accent"
            style={{ fontSize: 10, padding: '3px 10px' }}
            disabled={!text.trim()}
            onClick={() => { onAskAgent(text.trim()); onClose() }}
          >
            <img src="/static/claude-favicon.ico" width={10} height={10}
              style={{ verticalAlign: 'middle', marginRight: 4 }} alt="" />
            Ask Agent
          </button>
        )}
      </div>
    </div>
  )
}

/**
 * One pending (draft) review comment, rendered inline under its diff row.
 * Three actions in the header: Ask Agent (forwards the body to the
 * review agent session as a refine-prompt), Edit (in-place textarea
 * toggle), Remove (delete the draft line on GitHub).
 *
 * The remove + ask-agent buttons are pure pass-throughs; edit owns its
 * own draft + saving state so the parent doesn't have to track per-row
 * UI mode.
 */
function PendingCommentRow({ pc, onDelete, onEdit, onAskAgent }: {
  pc: PendingReviewComment
  onDelete?: (commentId: number) => void
  onEdit?: (commentId: number, body: string) => Promise<void>
  onAskAgent?: (pc: PendingReviewComment) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(pc.body)
  const [saving, setSaving] = useState(false)

  const startEdit = useCallback(() => {
    // Re-seed from the latest server body whenever we open the editor
    // so stale local drafts can't shadow an external edit.
    setDraft(pc.body)
    setEditing(true)
  }, [pc.body])

  const save = useCallback(async () => {
    if (!onEdit) return
    if (draft.trim() === pc.body.trim()) {
      setEditing(false)
      return
    }
    setSaving(true)
    try {
      await onEdit(pc.id, draft.trim())
      setEditing(false)
    } finally {
      setSaving(false)
    }
  }, [onEdit, pc.id, pc.body, draft])

  return (
    <div
      data-testid={`pending-inline-${pc.id}`}
      style={{
        // Indent under the diff row's code column so the comment
        // visually anchors to the line above without overflowing
        // the line-number gutter.
        margin: '4px 8px 6px 64px',
        padding: '6px 8px',
        background: 'var(--card-bg)',
        border: '1px solid rgba(251, 191, 36, 0.55)',
        borderRadius: 4,
        // Comment text is regular UI font, not monospace, so prose
        // renders normally instead of inheriting the diff's tight
        // code styling.
        fontFamily: 'inherit', fontSize: 11, lineHeight: 1.4,
        color: 'var(--text)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <span style={{
          fontSize: 9, fontWeight: 800, letterSpacing: 0.5,
          padding: '1px 5px', borderRadius: 3,
          background: 'rgba(251, 191, 36, 0.22)', color: '#f59e0b',
        }}>PENDING</span>
        <span style={{ flex: 1 }} />
        {!editing && onAskAgent && (
          <button
            className="btn-action accent"
            data-testid={`pending-comment-ask-${pc.id}`}
            title="Forward this draft to the review agent (refine / question)"
            onClick={(e) => { e.stopPropagation(); onAskAgent(pc) }}
            style={{ fontSize: 9, padding: '1px 6px' }}
          >
            <img src="/static/claude-favicon.ico" width={9} height={9}
              style={{ verticalAlign: 'middle', marginRight: 3 }} alt="" />
            ask
          </button>
        )}
        {!editing && onEdit && (
          <button
            className="btn-action"
            data-testid={`pending-comment-edit-${pc.id}`}
            title="Edit this pending comment"
            onClick={(e) => { e.stopPropagation(); startEdit() }}
            style={{ fontSize: 9, padding: '1px 6px' }}
          >
            edit
          </button>
        )}
        {!editing && onDelete && (
          <button
            className="btn-action"
            data-testid={`pending-comment-delete-${pc.id}`}
            title="Delete this pending comment"
            onClick={(e) => { e.stopPropagation(); onDelete(pc.id) }}
            style={{ fontSize: 9, padding: '1px 6px', color: 'var(--red)' }}
          >
            remove
          </button>
        )}
      </div>
      {editing ? (
        <>
          <textarea
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={saving}
            // Cmd/Ctrl+Enter to save, Esc to cancel -- standard editor
            // keymaps so the user doesn't have to reach for the mouse.
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                e.preventDefault()
                save()
              } else if (e.key === 'Escape') {
                setEditing(false)
              }
            }}
            style={{
              width: '100%', minHeight: 60, padding: '4px 6px',
              background: 'var(--panel-bg)', border: '1px solid var(--border)',
              borderRadius: 4, color: 'var(--text)',
              fontSize: 11, fontFamily: 'inherit',
              resize: 'vertical', boxSizing: 'border-box',
            }}
          />
          <div style={{ display: 'flex', gap: 4, justifyContent: 'flex-end', marginTop: 4 }}>
            <button
              className="btn-action"
              onClick={(e) => { e.stopPropagation(); setEditing(false) }}
              disabled={saving}
              style={{ fontSize: 9, padding: '1px 6px' }}
            >cancel</button>
            <button
              className="btn-action accent"
              data-testid={`pending-comment-save-${pc.id}`}
              onClick={(e) => { e.stopPropagation(); save() }}
              disabled={saving || !draft.trim()}
              style={{ fontSize: 9, padding: '1px 6px' }}
            >{saving ? 'saving...' : 'save'}</button>
          </div>
        </>
      ) : (
        <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
          {pc.body}
        </div>
      )}
    </div>
  )
}


/** Single expandable file with diff */
function FileItem({ file, repo, prNumber, diffData, onLoadDiff, onComment, onAskAgent, pendingCount, pendingForFile, onDeletePending, onEditPending, onAskAgentPending }: {
  file: PRDetail['files'][number]
  repo: string
  prNumber: number
  diffData: string | null
  onLoadDiff: () => void
  onComment?: (path: string, line: number, body: string) => void
  onAskAgent?: (context: string) => void
  pendingCount?: number
  /** Pending review comments anchored to this file (already filtered
   *  by path upstream). Rendered inline under the matching diff row. */
  pendingForFile?: PendingReviewComment[]
  onDeletePending?: (commentId: number) => void
  /** Save a new body for an existing pending comment. Returns a promise
   *  so the row can stay in 'saving' state until it resolves. */
  onEditPending?: (commentId: number, body: string) => Promise<void>
  /** Hand the pending comment off to the agent session as a one-off
   *  prompt -- typically "refine this draft" -- without losing the
   *  draft itself. Wired by PRCard when an agent surface exists. */
  onAskAgentPending?: (pc: PendingReviewComment) => void
}) {
  // Auto-expand files that carry a draft so the user lands on their
  // pending comments without an extra click. The state is initialized
  // once -- toggling collapse/expand still works normally afterward.
  const hasPending = (pendingForFile?.length ?? 0) > 0
  const [expanded, setExpanded] = useState(hasPending)
  const [popup, setPopup] = useState<{ x: number; y: number; text: string; lineFrom: number; lineTo: number } | null>(null)
  const diffRef = useRef<HTMLDivElement>(null)

  // Auto-load the diff when this file mounts already-expanded (pending
  // present); otherwise the user sees an empty pane with no spinner.
  useEffect(() => {
    if (expanded && !diffData) onLoadDiff()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleToggle = () => {
    if (!expanded && !diffData) onLoadDiff()
    setExpanded(!expanded)
  }

  // Group pending comments by GitHub diff `position` so each diff row
  // can render its attached comments in one O(1) lookup instead of
  // scanning the array on every line.
  const pendingByPosition = useMemo(() => {
    const m: Record<number, PendingReviewComment[]> = {}
    for (const c of pendingForFile || []) {
      const p = c.position
      if (p == null) continue
      if (!m[p]) m[p] = []
      m[p].push(c)
    }
    return m
  }, [pendingForFile])

  const handleMouseUp = useCallback(() => {
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed || !diffRef.current) return
    const text = sel.toString().trim()
    if (!text) return

    // Find start line from anchor node
    const findLineNum = (node: Node | null): number => {
      let el = node instanceof HTMLElement ? node : node?.parentElement
      while (el && !el.dataset.lineIdx && el !== diffRef.current) el = el.parentElement
      return el?.dataset.newLine ? parseInt(el.dataset.newLine, 10) : 0
    }
    const startLine = findLineNum(sel.anchorNode)
    const endLine = findLineNum(sel.focusNode)
    const lineFrom = Math.min(startLine, endLine) || startLine
    const lineTo = Math.max(startLine, endLine) || startLine

    const range = sel.getRangeAt(0)
    const rect = range.getBoundingClientRect()
    setPopup({ x: rect.right + 4, y: rect.top, text, lineFrom, lineTo })
  }, [])

  const parsedLines = expanded && diffData ? parseDiff(diffData) : []

  return (
    <div style={{ marginBottom: 1, border: expanded ? '1px solid var(--border)' : 'none', borderRadius: 4, overflow: 'hidden' }}>
      {/* File header - clickable */}
      <div
        onClick={handleToggle}
        style={{
          fontSize: 10, fontFamily: 'monospace', display: 'flex', gap: 6,
          padding: expanded ? '6px 8px' : '2px 0',
          background: expanded ? 'var(--panel-bg)' : 'transparent',
          cursor: 'pointer', alignItems: 'center',
        }}
      >
        <span style={{
          fontSize: 8, transition: 'transform 0.15s',
          transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
          display: 'inline-block', color: 'var(--text-dim)',
        }}>{'\u25B6'}</span>
        <span style={{ color: 'var(--green)', width: 30, textAlign: 'right', flexShrink: 0 }}>+{file.additions || 0}</span>
        <span style={{ color: 'var(--red)', width: 30, textAlign: 'right', flexShrink: 0 }}>-{file.deletions || 0}</span>
        <span style={{ color: 'var(--text)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{file.path}</span>
        {pendingCount && pendingCount > 0 ? (
          <span
            data-testid={`file-pending-badge-${file.path}`}
            title={`${pendingCount} pending review comment${pendingCount > 1 ? 's' : ''} on this file`}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 3,
              fontSize: 10, padding: '1px 6px', borderRadius: 10,
              color: '#f59e0b',
              background: 'rgba(251, 191, 36, 0.12)',
              border: '1px solid rgba(251, 191, 36, 0.4)',
              flexShrink: 0,
            }}
          >
            {'\u{1F4AC}'}
            {pendingCount}
          </span>
        ) : null}
      </div>

      {/* Diff content */}
      {expanded && (
        <div
          ref={diffRef}
          onMouseUp={handleMouseUp}
          style={{
            fontSize: 9, fontFamily: 'Menlo, Monaco, "Courier New", monospace',
            lineHeight: 1.5, overflow: 'auto', maxHeight: 500,
            background: 'var(--panel-bg)',
            // The PR Card column is narrow (~35% viewport); a 100-col
            // line + line-number gutters trigger horizontal scroll
            // every time. `tabSize: 2` keeps indented Java/Scala lines
            // half-width, so most code now fits without scrolling.
            tabSize: 2,
          }}
        >
          {!diffData && (
            <div style={{ padding: 10, color: 'var(--text-dim)', fontSize: 11 }}>Loading diff...</div>
          )}
          {parsedLines.map((line, i) => {
            const style = LINE_STYLES[line.type] || LINE_STYLES.context
            // Strip leading +/- from code lines so only background indicates add/remove
            let displayText = line.text
            if (line.type === 'add' && displayText.startsWith('+')) displayText = ' ' + displayText.slice(1)
            if (line.type === 'remove' && displayText.startsWith('-')) displayText = ' ' + displayText.slice(1)
            const inlinePending = line.position != null ? pendingByPosition[line.position] : undefined
            return (
              <div key={i}>
                <div
                  data-line-idx={i}
                  data-new-line={line.newLine ?? ''}
                  style={{
                    display: 'flex', color: style.color, background: style.bg,
                    borderLeft: style.borderLeft || '3px solid transparent',
                    // pre-wrap (was 'pre') so very long lines wrap
                    // inside the column instead of forcing a horizontal
                    // scrollbar on the whole diff. line-number gutters
                    // are flexShrink:0 so they stay aligned.
                    whiteSpace: 'pre-wrap', minHeight: 18,
                  }}
                >
                  <span style={{ width: 28, textAlign: 'right', color: 'var(--text-faint)', fontSize: 9, flexShrink: 0, userSelect: 'none', paddingRight: 3 }}>
                    {line.oldLine ?? ''}
                  </span>
                  <span style={{ width: 28, textAlign: 'right', color: 'var(--text-faint)', fontSize: 9, flexShrink: 0, userSelect: 'none', paddingRight: 6, borderRight: '1px solid var(--border)' }}>
                    {line.newLine ?? ''}
                  </span>
                  <span style={{ paddingLeft: 6, flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>
                    {(line.type === 'add' || line.type === 'remove' || line.type === 'context')
                      ? highlightLine(displayText).map((span, si) =>
                          span.color
                            ? <span key={si} style={{ color: span.color }}>{span.text}</span>
                            : <span key={si}>{span.text}</span>
                        )
                      : displayText
                    }
                  </span>
                </div>
                {inlinePending && inlinePending.map((pc) => (
                  <PendingCommentRow
                    key={pc.id}
                    pc={pc}
                    onDelete={onDeletePending}
                    onEdit={onEditPending}
                    onAskAgent={onAskAgentPending}
                  />
                ))}
              </div>
            )
          })}
        </div>
      )}

      {/* Selection bubble with input */}
      {popup && (
        <SelectionBubble
          x={popup.x}
          y={popup.y}
          containerRef={diffRef}
          codeRef={popup.text}
          onComment={(body) => {
            onComment?.(file.path, popup.lineFrom, body)
          }}
          onAskAgent={onAskAgent ? (message) => {
            const lineRef = popup.lineFrom === popup.lineTo
              ? `L${popup.lineFrom}`
              : `L${popup.lineFrom}-L${popup.lineTo}`
            onAskAgent(
              `${message}\n\nCode: ${file.path} (${lineRef}), PR ${repo}#${prNumber}:\n` +
              `\`\`\`\n${popup.text}\n\`\`\``
            )
          } : undefined}
          onClose={() => setPopup(null)}
        />
      )}
    </div>
  )
}

export function FileList({ files, repo, prNumber, onComment, onAskAgent, pendingComments, onDeletePending, onEditPending, onAskAgentPending }: FileListProps) {
  // Hooks must run unconditionally on every render -- the empty-list early
  // return below must come AFTER all hook declarations.
  const [diffs, setDiffs] = useState<Record<string, string>>({})
  const [loadingDiff, setLoadingDiff] = useState(false)

  const loadAllDiffs = useCallback(async () => {
    if (loadingDiff || Object.keys(diffs).length > 0) return
    setLoadingDiff(true)
    try {
      const result = await api.getPRDiff(repo, prNumber)
      setDiffs(result.files || {})
    } catch {
      // Silently fail -- diff just won't show
    } finally {
      setLoadingDiff(false)
    }
  }, [repo, prNumber, loadingDiff, diffs])

  // One pass over pendingComments per render rather than O(files * pending)
  // inside the map -- groups comments by file so each FileItem only
  // sees its own slice (and the badge can derive .length from it).
  const pendingByPath = useMemo(() => {
    const m: Record<string, PendingReviewComment[]> = {}
    for (const c of pendingComments || []) {
      if (!m[c.path]) m[c.path] = []
      m[c.path].push(c)
    }
    return m
  }, [pendingComments])

  if (!files || files.length === 0) return null

  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 4, fontWeight: 600 }}>
        Files ({files.length})
      </div>
      {files.map((f) => {
        const forFile = pendingByPath[f.path]
        return (
          <FileItem
            key={f.path}
            file={f}
            repo={repo}
            prNumber={prNumber}
            diffData={diffs[f.path] || null}
            onLoadDiff={loadAllDiffs}
            onComment={onComment}
            onAskAgent={onAskAgent}
            pendingCount={forFile?.length}
            pendingForFile={forFile}
            onDeletePending={onDeletePending}
            onEditPending={onEditPending}
            onAskAgentPending={onAskAgentPending}
          />
        )
      })}
    </div>
  )
}
