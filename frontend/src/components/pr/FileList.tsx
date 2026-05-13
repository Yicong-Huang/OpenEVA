import { useState, useCallback, useRef, useEffect } from 'react'
import type { PRDetail } from '../../types'
import { api } from '../../api'
import { highlightLine } from '../../utils/syntaxHighlight'

interface FileListProps {
  files: PRDetail['files']
  repo: string
  prNumber: number
  onComment?: (path: string, line: number, body: string) => void
  onAskAgent?: (context: string) => void
}

/** Parse a unified diff string into renderable lines with line numbers. */
function parseDiff(raw: string): Array<{
  type: 'header' | 'add' | 'remove' | 'context' | 'info'
  text: string
  oldLine: number | null
  newLine: number | null
}> {
  const lines: ReturnType<typeof parseDiff> = []
  let oldLine = 0
  let newLine = 0

  for (const line of raw.split('\n')) {
    if (line.startsWith('@@')) {
      // Parse hunk header: @@ -old,len +new,len @@
      const match = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)/)
      if (match) {
        oldLine = parseInt(match[1], 10)
        newLine = parseInt(match[2], 10)
      }
      lines.push({ type: 'info', text: line, oldLine: null, newLine: null })
    } else if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ')) {
      lines.push({ type: 'header', text: line, oldLine: null, newLine: null })
    } else if (line.startsWith('+')) {
      lines.push({ type: 'add', text: line, oldLine: null, newLine: newLine })
      newLine++
    } else if (line.startsWith('-')) {
      lines.push({ type: 'remove', text: line, oldLine: oldLine, newLine: null })
      oldLine++
    } else {
      lines.push({ type: 'context', text: line, oldLine: oldLine, newLine: newLine })
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

/** Single expandable file with diff */
function FileItem({ file, repo, prNumber, diffData, onLoadDiff, onComment, onAskAgent }: {
  file: PRDetail['files'][number]
  repo: string
  prNumber: number
  diffData: string | null
  onLoadDiff: () => void
  onComment?: (path: string, line: number, body: string) => void
  onAskAgent?: (context: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [popup, setPopup] = useState<{ x: number; y: number; text: string; lineFrom: number; lineTo: number } | null>(null)
  const diffRef = useRef<HTMLDivElement>(null)

  const handleToggle = () => {
    if (!expanded && !diffData) onLoadDiff()
    setExpanded(!expanded)
  }

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
            return (
              <div
                key={i}
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

export function FileList({ files, repo, prNumber, onComment, onAskAgent }: FileListProps) {
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

  if (!files || files.length === 0) return null

  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 4, fontWeight: 600 }}>
        Files ({files.length})
      </div>
      {files.map((f) => (
        <FileItem
          key={f.path}
          file={f}
          repo={repo}
          prNumber={prNumber}
          diffData={diffs[f.path] || null}
          onLoadDiff={loadAllDiffs}
          onComment={onComment}
          onAskAgent={onAskAgent}
        />
      ))}
    </div>
  )
}
