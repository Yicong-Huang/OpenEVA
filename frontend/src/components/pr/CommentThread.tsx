import { useState } from 'react'
import type { PRDetail } from '../../types'
import { timeAgo, ghAvatar, renderMarkdown } from '../../utils'
import { api } from '../../api'
import { useAlert } from '../Alert'

// ============================================================
// Shared sub-components
// ============================================================

/** Render a diff hunk with red/green line coloring (GitHub style). */
function DiffHunk({ text }: { text: string }) {
  const lines = text.split('\n')
  return (
    <div style={{
      fontSize: 11, background: 'var(--panel-bg)', borderRadius: '6px 6px 0 0',
      border: '1px solid var(--border)', borderBottom: 'none',
      overflow: 'auto', maxHeight: 120, fontFamily: 'monospace', lineHeight: 1.6,
    }}>
      {lines.map((line, i) => {
        let color = 'var(--text-dim)'
        let bg = 'transparent'
        if (line.startsWith('+') && !line.startsWith('+++')) {
          color = 'var(--green)'; bg = 'rgba(46,160,67,0.15)'
        } else if (line.startsWith('-') && !line.startsWith('---')) {
          color = 'var(--red)'; bg = 'rgba(248,81,73,0.15)'
        } else if (line.startsWith('@@')) {
          color = 'var(--blue)'; bg = 'rgba(56,139,253,0.1)'
        }
        return (
          <div key={i} style={{ color, background: bg, padding: '0 10px', whiteSpace: 'pre' }}>
            {line}
          </div>
        )
      })}
    </div>
  )
}

/** GitHub-style reply/edit textarea. */
function ReplyEditor({ placeholder, initialValue, onSubmit, onCancel, submitLabel, saving }: {
  placeholder: string
  initialValue: string
  onSubmit: (text: string) => void
  onCancel: () => void
  submitLabel: string
  saving: boolean
}) {
  const [draft, setDraft] = useState(initialValue)
  return (
    <div style={{ padding: '8px 12px', background: 'var(--panel-bg)', borderRadius: 6, border: '1px solid var(--border)' }}>
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        disabled={saving}
        autoFocus
        placeholder={placeholder}
        style={{
          width: '100%', minHeight: 80, background: 'var(--input-bg)',
          border: '1px solid var(--border)', borderRadius: 6,
          color: 'var(--text)', padding: 10, fontSize: 12,
          fontFamily: 'Menlo, Monaco, "Courier New", monospace',
          resize: 'vertical',
        }}
      />
      <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end', marginTop: 8 }}>
        <button className="btn-action" style={{ fontSize: 10, padding: '4px 12px' }}
          onClick={onCancel} disabled={saving}>Cancel</button>
        <button className="btn-action accent" style={{ fontSize: 10, padding: '4px 12px' }}
          onClick={() => onSubmit(draft)} disabled={saving || !draft.trim()}>
          {saving ? '...' : submitLabel}
        </button>
      </div>
    </div>
  )
}

/** Single comment bubble (GitHub style: avatar left, content right). */
function CommentBubble({ user, avatar, createdAt, body, isOwn, indent, accentBorder, actions }: {
  user: string
  avatar?: string
  createdAt: string
  body: string
  isOwn: boolean
  indent?: number
  accentBorder?: boolean
  actions?: React.ReactNode
}) {
  return (
    <div style={{ display: 'flex', gap: 10, marginLeft: indent || 0, marginBottom: 2 }}>
      <img
        src={avatar || ghAvatar(user)}
        alt={user}
        style={{ width: 28, height: 28, borderRadius: '50%', flexShrink: 0, marginTop: 2 }}
      />
      <div style={{
        flex: 1, minWidth: 0,
        border: '1px solid var(--border)',
        borderRadius: 6,
        ...(accentBorder ? { borderLeftColor: 'var(--accent)', borderLeftWidth: 2 } : {}),
        ...(isOwn ? { borderColor: 'rgba(99,102,241,0.3)' } : {}),
      }}>
        {/* Header bar */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 6,
          padding: '6px 12px', fontSize: 11,
          background: isOwn ? 'rgba(99,102,241,0.06)' : 'var(--panel-bg)',
          borderBottom: '1px solid var(--border)',
          borderRadius: '6px 6px 0 0',
        }}>
          <span style={{ fontWeight: 600, color: 'var(--text)' }}>{user}</span>
          <span style={{ color: 'var(--text-dim)' }}>{timeAgo(createdAt)}</span>
          {actions && <span style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>{actions}</span>}
        </div>
        {/* Body */}
        <div
          className="md-body"
          style={{ padding: '8px 12px', fontSize: 12 }}
          dangerouslySetInnerHTML={{ __html: renderMarkdown(body) }}
        />
      </div>
    </div>
  )
}

/** 3-dot dropdown menu. */
function ActionMenu({ onQuoteReply, onEdit, canEdit }: {
  onQuoteReply: () => void; onEdit?: () => void; canEdit: boolean
}) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <button
        className="btn-action"
        style={{ padding: '0 5px', fontSize: 13, lineHeight: 1, opacity: 0.4 }}
        title="Actions"
        onClick={() => setOpen(!open)}
      >
        &#8943;
      </button>
      {open && (
        <>
          {/* click-away overlay */}
          <div style={{ position: 'fixed', inset: 0, zIndex: 99 }} onClick={() => setOpen(false)} />
          <div style={{
            position: 'absolute', right: 0, top: 22, zIndex: 100,
            background: 'var(--card-bg)', border: '1px solid var(--border)',
            borderRadius: 6, boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
            minWidth: 140, fontSize: 12, overflow: 'hidden',
          }}>
            <div
              className="menu-item"
              style={{ padding: '8px 12px', cursor: 'pointer' }}
              onClick={() => { onQuoteReply(); setOpen(false) }}
            >
              Quote reply
            </div>
            {canEdit && onEdit && (
              <div
                className="menu-item"
                style={{ padding: '8px 12px', cursor: 'pointer' }}
                onClick={() => { onEdit(); setOpen(false) }}
              >
                Edit
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

// ============================================================
// Inline code comments (review comments grouped by file)
// ============================================================

interface InlineCommentsProps {
  inlineComments: PRDetail['inlineComments']
  repo: string
  prNumber: number
  onRefresh: () => void
  myLogins?: string[]
  myLogin?: string
  onAskAgent?: (threadContext: string) => void
}

export function InlineComments({ inlineComments, repo, prNumber, onRefresh: _onRefresh, myLogins = [], myLogin = '', onAskAgent }: InlineCommentsProps) {
  const [editedBodies, setEditedBodies] = useState<Record<string, string>>({})
  const [localReplies, setLocalReplies] = useState<PRDetail['inlineComments']>([])
  const [resolvedOverrides, setResolvedOverrides] = useState<Record<string, boolean>>({})
  const [expandedResolved, setExpandedResolved] = useState<Record<string, boolean>>({})
  const [replyTarget, setReplyTarget] = useState<string | null>(null)
  const [editTarget, setEditTarget] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const { alert } = useAlert()

  const allComments = [...(inlineComments || []), ...localReplies]

  if (!allComments || allComments.length === 0) return null

  const byFile: Record<string, PRDetail['inlineComments']> = {}
  for (const ic of allComments) {
    const fp = ic.path || '(unknown)'
    if (!byFile[fp]) byFile[fp] = []
    byFile[fp].push(ic)
  }

  const handleReply = async (threadId: string, text: string, threadPath: string) => {
    setSaving(true)
    try {
      await api.replyToComment(repo, prNumber, Number(threadId), text, true)
      // Optimistic insert: add the reply locally
      const myUser = myLogin || myLogins[0] || 'me'
      setLocalReplies((prev) => [...prev, {
        id: 'local-' + Date.now(),
        user: myUser,
        avatar: '',
        path: threadPath,
        line: 0,
        body: text,
        createdAt: new Date().toISOString(),
        diffHunk: '',
        inReplyToId: threadId,
      }])
      setReplyTarget(null)
    } catch (e) {
      await alert({ title: 'Comment failed', message: e instanceof Error ? e.message : String(e), kind: 'error' })
    } finally {
      setSaving(false)
    }
  }

  const handleEdit = async (commentId: string, text: string) => {
    setSaving(true)
    try {
      await api.editComment(repo, Number(commentId), text, true)
      setEditedBodies((prev) => ({ ...prev, [commentId]: text }))
      setEditTarget(null)
    } catch (e) {
      await alert({ title: 'Comment failed', message: e instanceof Error ? e.message : String(e), kind: 'error' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 8, fontWeight: 600 }}>
        Code Comments ({allComments.length})
      </div>
      {Object.keys(byFile).map((fp) => {
        const fileComments = byFile[fp]
        const threads: Record<string, PRDetail['inlineComments']> = {}
        const topLevel: PRDetail['inlineComments'] = []
        for (const c of fileComments) {
          if (c.inReplyToId) {
            if (!threads[c.inReplyToId]) threads[c.inReplyToId] = []
            threads[c.inReplyToId].push(c)
          } else {
            topLevel.push(c)
            if (!threads[c.id]) threads[c.id] = []
          }
        }

        return (
          <div key={fp} style={{ marginBottom: 16, border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
            {/* File header */}
            <div style={{
              fontSize: 11, fontFamily: 'monospace', color: 'var(--text)',
              padding: '6px 12px', background: 'var(--panel-bg)',
              borderBottom: '1px solid var(--border)', fontWeight: 600,
            }}>
              {fp}
            </div>

            {topLevel.map((tc) => {
              const isOwn = myLogins.includes(tc.user)
              const threadResolved = resolvedOverrides[tc.id] ?? tc.isResolved ?? false
              const threadId = tc.threadId
              const isExpanded = expandedResolved[tc.id] ?? false

              // Resolved threads: collapsed by default (GitHub style)
              if (threadResolved && !isExpanded) {
                const replyCount = (threads[tc.id] || []).length
                return (
                  <div key={tc.id} style={{
                    borderBottom: '1px solid var(--border)',
                    padding: '6px 12px', display: 'flex', alignItems: 'center', gap: 8,
                  }}>
                    <span style={{ color: 'var(--green)', fontSize: 11 }}>{'\u2713'}</span>
                    <img src={tc.avatar || ghAvatar(tc.user)} alt={tc.user}
                      style={{ width: 18, height: 18, borderRadius: '50%' }} />
                    <span style={{ fontSize: 11, color: 'var(--text-dim)', flex: 1 }}>
                      <strong style={{ color: 'var(--text)' }}>{tc.user}</strong>
                      {' '}{tc.body.length > 60 ? tc.body.substring(0, 60) + '...' : tc.body}
                      {replyCount > 0 && <span style={{ marginLeft: 6 }}>({replyCount} {replyCount === 1 ? 'reply' : 'replies'})</span>}
                    </span>
                    <button className="btn-action" style={{ fontSize: 9, padding: '2px 8px', flexShrink: 0 }}
                      onClick={() => setExpandedResolved((prev) => ({ ...prev, [tc.id]: true }))}>
                      Show resolved
                    </button>
                  </div>
                )
              }

              return (
                <div key={tc.id} style={{ borderBottom: '1px solid var(--border)' }}>
                  {/* Resolved header with hide/unresolve */}
                  {threadResolved && (
                    <div style={{
                      padding: '4px 12px', fontSize: 10, color: 'var(--text-dim)',
                      display: 'flex', alignItems: 'center', gap: 6,
                      background: 'rgba(46,160,67,0.06)', borderBottom: '1px solid var(--border)',
                    }}>
                      <span style={{ color: 'var(--green)' }}>{'\u2713'}</span>
                      <span style={{ flex: 1 }}>This conversation was marked as resolved.</span>
                      <button className="btn-action" style={{ fontSize: 9, padding: '1px 6px' }}
                        onClick={() => setExpandedResolved((prev) => ({ ...prev, [tc.id]: false }))}>
                        Hide resolved
                      </button>
                      {threadId && (
                        <button className="btn-action" style={{ fontSize: 9, padding: '1px 6px' }}
                          onClick={async () => {
                            await api.resolveThread(threadId, false, repo)
                            setResolvedOverrides((prev) => ({ ...prev, [tc.id]: false }))
                          }}>Unresolve</button>
                      )}
                    </div>
                  )}
                  {/* Diff context */}
                  {tc.diffHunk && <DiffHunk text={tc.diffHunk} />}

                  {/* Thread container */}
                  <div style={{ padding: '10px 12px' }}>
                    {/* Top-level comment */}
                    {editTarget === tc.id ? (
                      <ReplyEditor
                        placeholder="Edit comment..."
                        initialValue={editedBodies[tc.id] ?? tc.body}
                        submitLabel="Save"
                        saving={saving}
                        onSubmit={(text) => handleEdit(tc.id, text)}
                        onCancel={() => setEditTarget(null)}
                      />
                    ) : (
                      <CommentBubble
                        user={tc.user} avatar={tc.avatar || ghAvatar(tc.user)}
                        createdAt={tc.createdAt} body={editedBodies[tc.id] ?? tc.body}
                        isOwn={isOwn} accentBorder
                        actions={
                          <ActionMenu
                            canEdit={isOwn}
                            onQuoteReply={() => {
                              const q = (editedBodies[tc.id] ?? tc.body).split('\n').map((l: string) => '> ' + l).join('\n')
                              setReplyTarget(tc.id)
                              // Pre-fill handled by ReplyEditor initialValue
                              setTimeout(() => {
                                const ta = document.querySelector<HTMLTextAreaElement>(`[data-reply-thread="${tc.id}"] textarea`)
                                if (ta) { ta.value = q + '\n\n'; ta.dispatchEvent(new Event('input', { bubbles: true })) }
                              }, 50)
                            }}
                            onEdit={() => setEditTarget(tc.id)}
                          />
                        }
                      />
                    )}

                    {/* Replies */}
                    {(threads[tc.id] || []).map((rc) => {
                      const rcOwn = myLogins.includes(rc.user)
                      return editTarget === rc.id ? (
                        <div key={rc.id} style={{ marginTop: 6, marginLeft: 38 }}>
                          <ReplyEditor
                            placeholder="Edit reply..."
                            initialValue={editedBodies[rc.id] ?? rc.body}
                            submitLabel="Save"
                            saving={saving}
                            onSubmit={(text) => handleEdit(rc.id, text)}
                            onCancel={() => setEditTarget(null)}
                          />
                        </div>
                      ) : (
                        <div key={rc.id} style={{ marginTop: 6 }}>
                          <CommentBubble
                            user={rc.user} avatar={rc.avatar || ghAvatar(rc.user)}
                            createdAt={rc.createdAt} body={editedBodies[rc.id] ?? rc.body}
                            isOwn={rcOwn} indent={38}
                            actions={
                              <ActionMenu
                                canEdit={rcOwn}
                                onQuoteReply={() => setReplyTarget(tc.id)}
                                onEdit={() => setEditTarget(rc.id)}
                              />
                            }
                          />
                        </div>
                      )
                    })}

                    {/* Reply box */}
                    {replyTarget === tc.id ? (
                      <div style={{ display: 'flex', gap: 10, marginTop: 8, marginLeft: 0 }} data-reply-thread={tc.id}>
                        <img
                          src={myLogin ? ghAvatar(myLogin) : ''}
                          alt=""
                          style={{ width: 28, height: 28, borderRadius: '50%', flexShrink: 0, marginTop: 2 }}
                        />
                        <div style={{ flex: 1 }}>
                          <ReplyEditor
                            placeholder="Write a reply..."
                            initialValue=""
                            submitLabel="Reply"
                            saving={saving}
                            onSubmit={(text) => handleReply(tc.id, text, fp)}
                            onCancel={() => setReplyTarget(null)}
                          />
                        </div>
                      </div>
                    ) : (
                      <div
                        style={{
                          display: 'flex', gap: 10, marginTop: 8, alignItems: 'center',
                        }}
                      >
                        <img
                          src={myLogin ? ghAvatar(myLogin) : ''}
                          alt=""
                          style={{ width: 28, height: 28, borderRadius: '50%', flexShrink: 0 }}
                        />
                        <div
                          style={{
                            flex: 1, padding: '6px 12px', fontSize: 12,
                            color: 'var(--text-dim)', background: 'var(--input-bg)',
                            border: '1px solid var(--border)', borderRadius: 6,
                            cursor: 'text',
                          }}
                          onClick={() => setReplyTarget(tc.id)}
                        >
                          Reply...
                        </div>
                        {onAskAgent && (
                          <button
                            className="btn-action accent"
                            title="Ask Agent to draft replies"
                            style={{ flexShrink: 0, padding: '4px 8px', fontSize: 10 }}
                            onClick={(e) => {
                              e.stopPropagation()
                              onAskAgent(`https://github.com/${repo}/pull/${prNumber}#discussion_r${tc.id}`)
                            }}
                          >
                            <img src="/static/claude-favicon.ico" width={12} height={12}
                              style={{ verticalAlign: 'middle', marginRight: 3 }} alt="" />
                            Draft
                          </button>
                        )}
                        {threadId && !threadResolved && (
                          <button
                            className="btn-action"
                            title="Resolve conversation"
                            style={{ flexShrink: 0, padding: '4px 8px', fontSize: 10 }}
                            onClick={async (e) => {
                              e.stopPropagation()
                              await api.resolveThread(threadId, true, repo)
                              setResolvedOverrides((prev) => ({ ...prev, [tc.id]: true }))
                            }}
                          >
                            {'\u2713'} Resolve
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}

// ============================================================
// Review summaries (the body text on `/reviews` rows)
// ============================================================
//
// GitHub's "Review changes" dialog lets the reviewer type a top-level
// summary in addition to per-line inline notes. That summary lives on
// the review row's `body` (gh's `pr view --json reviews`) -- not on
// any comment thread -- so the previous PRDetail render quietly
// dropped it. Users only saw the avatar dots from `ReviewSection`
// and never the actual prose. This component renders one bubble per
// review whose body is non-empty.

interface ReviewSummariesProps {
  reviews: PRDetail['reviews']
  myLogins?: string[]
}

export function ReviewSummaries({ reviews, myLogins = [] }: ReviewSummariesProps) {
  const withBody = (reviews || []).filter((r) => (r.body || '').trim().length > 0)
  if (withBody.length === 0) return null
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 8, fontWeight: 600 }}>
        Review Summaries ({withBody.length})
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {withBody.map((r, i) => {
          const user = r.author?.login || 'unknown'
          const isOwn = myLogins.includes(user)
          // State pill colour mirrors GitHub: green for APPROVED,
          // red for CHANGES_REQUESTED, neutral for COMMENTED.
          const stateColor = r.state === 'APPROVED'
            ? 'var(--green)'
            : r.state === 'CHANGES_REQUESTED'
              ? 'var(--red)'
              : 'var(--text-dim)'
          return (
            <CommentBubble
              key={r.id ?? `${user}-${r.submittedAt ?? i}`}
              user={user}
              avatar={ghAvatar(user)}
              createdAt={r.submittedAt || ''}
              body={r.body || ''}
              isOwn={isOwn}
              accentBorder
              actions={
                <span
                  style={{
                    fontSize: 9, fontWeight: 700, letterSpacing: 0.4,
                    padding: '1px 6px', borderRadius: 3,
                    color: stateColor,
                    border: `1px solid ${stateColor}`,
                  }}
                >
                  {r.state}
                </span>
              }
            />
          )
        })}
      </div>
    </div>
  )
}


// ============================================================
// General comments (issue comments)
// ============================================================

interface GeneralCommentsProps {
  comments: PRDetail['comments']
  repo: string
  prNumber: number
  onRefresh: () => void
  myLogins?: string[]
  onAskAgent?: (threadContext: string) => void
}

export function GeneralComments({ comments, repo, prNumber: _prNumber, onRefresh: _onRefresh, myLogins = [] }: GeneralCommentsProps) {
  const [editedBodies, setEditedBodies] = useState<Record<string, string>>({})
  const [editTarget, setEditTarget] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const { alert } = useAlert()

  const handleEdit = async (commentId: string, text: string) => {
    setSaving(true)
    try {
      await api.editComment(repo, Number(commentId), text, false)
      setEditedBodies((prev) => ({ ...prev, [commentId]: text }))
      setEditTarget(null)
    } catch (e) {
      await alert({ title: 'Comment failed', message: e instanceof Error ? e.message : String(e), kind: 'error' })
    } finally {
      setSaving(false)
    }
  }

  const handleQuoteReply = (body: string) => {
    // Scroll to the bottom reply editor and pre-fill with quote
    const editor = document.querySelector<HTMLTextAreaElement>('[data-testid="comment-input"]')
    if (editor) {
      const quoted = body.split('\n').map((l: string) => '> ' + l).join('\n') + '\n\n'
      editor.value = quoted
      editor.dispatchEvent(new Event('input', { bubbles: true }))
      // Also trigger React's onChange
      const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set
      nativeInputValueSetter?.call(editor, quoted)
      editor.dispatchEvent(new Event('input', { bubbles: true }))
      editor.focus()
      editor.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 8, fontWeight: 600 }}>
        Comments ({(comments || []).length})
      </div>
      {(comments || []).map((cm, i) => {
        const cmLogin = cm.author?.login || ''
        const cid = cm.id ?? i
        const bodyKey = String(cid)
        const isOwn = myLogins.includes(cmLogin)

        if (editTarget === bodyKey) {
          return (
            <div key={cid} style={{ marginBottom: 10 }}>
              <ReplyEditor
                placeholder="Edit comment..."
                initialValue={editedBodies[bodyKey] ?? cm.body}
                submitLabel="Save"
                saving={saving}
                onSubmit={(text) => handleEdit(bodyKey, text)}
                onCancel={() => setEditTarget(null)}
              />
            </div>
          )
        }

        return (
          <div key={cid} style={{ marginBottom: 10 }}>
            <CommentBubble
              user={cmLogin}
              createdAt={cm.createdAt}
              body={editedBodies[bodyKey] ?? cm.body}
              isOwn={isOwn}
              actions={cm.id != null ? (
                <ActionMenu
                  canEdit={isOwn}
                  onQuoteReply={() => handleQuoteReply(editedBodies[bodyKey] ?? cm.body)}
                  onEdit={() => setEditTarget(bodyKey)}
                />
              ) : undefined}
            />
          </div>
        )
      })}
    </div>
  )
}
