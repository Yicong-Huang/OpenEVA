import { useState, useCallback } from 'react'
import { Handle, Position } from '@xyflow/react'
import { NODE_W, NODE_H } from './graphShared'

export interface DraftTaskNodeData {
  /** 'draft' = user is filling the form; 'creating' = AI is running */
  mode: 'draft' | 'creating' | 'failed'
  // Phase 1 (draft): inputs the user is editing.
  context?: string
  manualId?: string
  manualDesc?: string
  // Phase 2 (creating): live log lines from the smart-create stream.
  log?: string[]
  /** Short label to show on the small creating-state node (typically
   *  the manual task id, or the first words of context). */
  label?: string
  // Phase 3 (failed): error message; user can retry or dismiss.
  errorMsg?: string
  // Callbacks. The node form is purely uncontrolled; it pushes the
  // final values back via these on Submit.
  onSubmit?: (input: { context: string; manualId?: string; manualDesc?: string }) => void
  onCancel?: () => void
  onDismiss?: () => void
  [key: string]: unknown
}


export const DRAFT_NODE_W = 320
export const DRAFT_NODE_H = 230


export function DraftTaskNode({ data }: { data: DraftTaskNodeData }) {
  const { mode, log, errorMsg, onSubmit, onCancel, onDismiss } = data
  // Local controlled state for the form. Hydrated from data on mount;
  // doesn't read from data on each render so the user can keep typing
  // while pendingCreates updates ripple in.
  const [context, setContext] = useState(data.context || '')
  const [manualId, setManualId] = useState(data.manualId || '')
  const [manualDesc, setManualDesc] = useState(data.manualDesc || '')
  const [showAdvanced, setShowAdvanced] = useState(false)

  const submit = useCallback(() => {
    if (!onSubmit) return
    if (!context.trim() && !manualId.trim()) return
    onSubmit({
      context: context.trim(),
      manualId: manualId.trim() || undefined,
      manualDesc: manualDesc.trim() || undefined,
    })
  }, [onSubmit, context, manualId, manualDesc])

  // Cmd/Ctrl + Enter submits.
  const onTextareaKey = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault()
      submit()
    }
  }, [submit])

  // Creating mode shrinks back to the normal task-node footprint
  // so it doesn't dominate the layout while smart-create runs in the
  // background. Form inputs (`nodrag` class) opt out of React Flow's
  // node-drag gesture so the user can still type / select; clicking
  // the surrounding chrome moves the node.
  const isCreating = mode === 'creating'

  if (isCreating) {
    return (
      <div
        data-testid="draft-task-node"
        data-mode="creating"
        className="draft-node-creating"
        style={{
          width: NODE_W,
          height: NODE_H,
          background: 'var(--card-bg)',
          border: '1.5px solid var(--accent)',
          borderLeft: '4px solid var(--accent)',
          borderRadius: 5,
          fontFamily: 'monospace',
          padding: '0 8px 0 4px',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          position: 'relative',
          boxSizing: 'border-box',
          // Subtle inset background tint via accent + low alpha so it
          // reads as "pending" without competing with task colors.
          boxShadow: 'inset 0 0 0 100px rgba(99,102,241,0.08)',
        }}
        title={(log && log[log.length - 1]) || 'Creating task...'}
      >
        <Handle type="target" position={Position.Left}
                style={{ width: 6, height: 6, background: 'var(--handle-bg)', left: -3 }} />
        <Handle type="source" position={Position.Right}
                style={{ width: 6, height: 6, background: 'var(--handle-bg)', right: -3 }} />
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10 }}>
          {/* Spinner: triple-bounce dots */}
          <span className="creating-spinner" aria-hidden="true">
            <span /><span /><span />
          </span>
          <span style={{
            color: 'var(--accent)', fontWeight: 600,
            textTransform: 'uppercase', letterSpacing: 0.4, fontSize: 8,
          }}>creating</span>
          <span style={{
            color: 'var(--text-dim)', fontSize: 9,
            whiteSpace: 'nowrap', overflow: 'hidden',
            textOverflow: 'ellipsis', flex: 1,
          }}>
            {data.label || data.manualId || (data.context ? data.context.slice(0, 30) : '')}
          </span>
          {onDismiss && (
            <button
              className="nodrag"
              onClick={onDismiss}
              style={{
                background: 'transparent', border: 'none',
                color: 'var(--text-dim)', cursor: 'pointer',
                fontSize: 10, padding: '0 2px',
              }}
              title="Cancel and dismiss"
            >x</button>
          )}
        </div>
        {/* Sweeping gradient stripe across the bottom border to
         * convey "in progress" without being intrusive. */}
        <span className="creating-shimmer" aria-hidden="true" />
      </div>
    )
  }

  return (
    <div
      data-testid="draft-task-node"
      data-mode={mode}
      style={{
        width: DRAFT_NODE_W,
        minHeight: DRAFT_NODE_H,
        background: 'var(--card-bg)',
        border: `1.5px dashed ${mode === 'failed' ? 'var(--red)' : 'var(--accent)'}`,
        borderRadius: 8,
        padding: 10,
        fontFamily: 'inherit',
        boxShadow: '0 4px 16px var(--shadow-color)',
        cursor: 'default',
      }}
      // No outer stopPropagation -- React Flow handles drag at the
      // node container level; form inputs opt out via `nodrag` class.
    >
      <Handle type="target" position={Position.Left}
              style={{ width: 6, height: 6, background: 'var(--handle-bg)' }} />
      <Handle type="source" position={Position.Right}
              style={{ width: 6, height: 6, background: 'var(--handle-bg)' }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
        <span style={{
          fontSize: 10, fontWeight: 600,
          color: mode === 'failed' ? 'var(--red)' : 'var(--accent)',
          textTransform: 'uppercase', letterSpacing: 0.4,
        }}>
          {mode === 'draft' ? 'New Task' : 'Create failed'}
        </span>
        {(onDismiss || onCancel) && (
          <button
            className="nodrag"
            onClick={mode === 'draft' ? onCancel : onDismiss}
            style={{
              marginLeft: 'auto', background: 'transparent',
              border: 'none', color: 'var(--text-dim)',
              cursor: 'pointer', fontSize: 11, padding: '0 4px',
            }}
            title={mode === 'draft' ? 'Cancel' : 'Dismiss'}
          >x</button>
        )}
      </div>

      {mode === 'draft' && (
        <>
          <textarea
            className="nodrag nopan"
            autoFocus
            placeholder="Describe what you need (AI will generate the task) -- Cmd+Enter to submit"
            value={context}
            onChange={(e) => setContext(e.target.value)}
            onKeyDown={onTextareaKey}
            style={{
              width: '100%', minHeight: 80, padding: '6px 8px',
              background: 'var(--panel-bg)', border: '1px solid var(--border)',
              borderRadius: 4, color: 'var(--text)',
              fontSize: 11, fontFamily: 'inherit',
              resize: 'vertical', boxSizing: 'border-box',
            }}
          />
          <div style={{ marginTop: 4 }}>
            <button
              className="nodrag"
              onClick={() => setShowAdvanced(v => !v)}
              style={{
                background: 'transparent', border: 'none',
                color: 'var(--text-dim)', cursor: 'pointer',
                fontSize: 9, padding: 0,
              }}
            >
              {showAdvanced ? 'v' : '>'} manual override
            </button>
          </div>
          {showAdvanced && (
            <div style={{ marginTop: 4 }}>
              <input
                className="nodrag nopan"
                placeholder="task-id"
                value={manualId}
                onChange={(e) => setManualId(e.target.value)}
                style={{
                  width: '100%', padding: '4px 6px', marginBottom: 4,
                  background: 'var(--panel-bg)', border: '1px solid var(--border)',
                  borderRadius: 4, color: 'var(--text)', fontSize: 10,
                  boxSizing: 'border-box',
                }}
              />
              <input
                className="nodrag nopan"
                placeholder="description"
                value={manualDesc}
                onChange={(e) => setManualDesc(e.target.value)}
                style={{
                  width: '100%', padding: '4px 6px',
                  background: 'var(--panel-bg)', border: '1px solid var(--border)',
                  borderRadius: 4, color: 'var(--text)', fontSize: 10,
                  boxSizing: 'border-box',
                }}
              />
            </div>
          )}
          <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end', marginTop: 8 }}>
            <button className="btn-action nodrag" style={{ fontSize: 10 }}
                    onClick={onCancel}>Cancel</button>
            <button className="btn-action accent nodrag" style={{ fontSize: 10 }}
                    disabled={!context.trim() && !manualId.trim()}
                    onClick={submit}>
              {context.trim() ? 'Create with AI' : 'Create'}
            </button>
          </div>
        </>
      )}

      {mode === 'failed' && (
        <>
          <div style={{
            background: 'rgba(239,68,68,0.1)', border: '1px solid var(--red)',
            borderRadius: 4, padding: 6, fontSize: 10, color: 'var(--red)',
            marginBottom: 6,
          }}>
            {errorMsg || 'Unknown error'}
          </div>
          {log && log.length > 0 && (
            <div style={{
              background: 'var(--panel-bg)', border: '1px solid var(--border)',
              borderRadius: 4, padding: 6, maxHeight: 100, overflowY: 'auto',
              fontSize: 9, fontFamily: 'monospace',
              color: 'var(--text-dim)', whiteSpace: 'pre-wrap',
            }}>
              {log.map((l, i) => <div key={i}>{l}</div>)}
            </div>
          )}
        </>
      )}
    </div>
  )
}
