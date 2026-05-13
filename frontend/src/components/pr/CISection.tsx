import type { PRDetail } from '../../types'
import { ciResult, ciName, isNonBlocking, isSuccess, isFailed } from './ciHelpers'

interface CIRingProps {
  passed: number
  total: number
  blockingFailed: number
  size?: number
}

function CIRing({ passed, total, blockingFailed, size = 18 }: CIRingProps) {
  if (total === 0) return null
  const r = (size - 2) / 2
  const cx = size / 2
  const cy = size / 2
  const circumference = 2 * Math.PI * r

  const passLen = circumference * (passed / total)
  const failLen = circumference * (blockingFailed / total)

  return (
    <svg width={size} height={size} style={{ verticalAlign: 'middle', flexShrink: 0 }}>
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--ci-ring-bg)" strokeWidth={2} />
      {passLen > 0 && (
        <circle
          cx={cx} cy={cy} r={r} fill="none" stroke="var(--green)" strokeWidth={2}
          strokeDasharray={`${passLen.toFixed(1)} ${(circumference - passLen).toFixed(1)}`}
          transform={`rotate(-90 ${cx} ${cy})`}
        />
      )}
      {failLen > 0 && (
        <circle
          cx={cx} cy={cy} r={r} fill="none" stroke="var(--red)" strokeWidth={2}
          strokeDasharray={`${failLen.toFixed(1)} ${(circumference - failLen).toFixed(1)}`}
          strokeDashoffset={`-${passLen.toFixed(1)}`}
          transform={`rotate(-90 ${cx} ${cy})`}
        />
      )}
    </svg>
  )
}

interface CISectionProps {
  checks: PRDetail['statusCheckRollup']
  ciExpanded: boolean
  onToggleExpand: () => void
}

export function CISection({ checks, ciExpanded, onToggleExpand }: CISectionProps) {
  if (checks.length === 0) return null

  const ciPassed = checks.filter((c) => isSuccess(ciResult(c))).length
  const ciBlockingFailed = checks.filter((c) => isFailed(ciResult(c)) && !isNonBlocking(c)).length
  const ciNonBlockingFailed = checks.filter((c) => isFailed(ciResult(c)) && isNonBlocking(c)).length
  const ciFailed = ciBlockingFailed + ciNonBlockingFailed
  const ciPending = checks.length - ciPassed - ciFailed
  const ciColor = ciBlockingFailed > 0
    ? 'var(--red)'
    : ciPending > 0
      ? 'var(--yellow)'
      : 'var(--green)'

  const sortedChecks = checks.slice().sort((a, b) => {
    const order: Record<string, number> = {
      FAILURE: 0, CANCELLED: 0, TIMED_OUT: 0, ACTION_REQUIRED: 0, STARTUP_FAILURE: 0,
      PENDING: 1, IN_PROGRESS: 1, QUEUED: 1,
      SUCCESS: 2, NEUTRAL: 2, SKIPPED: 2,
    }
    return (order[ciResult(a)] ?? 1) - (order[ciResult(b)] ?? 1)
  })

  return (
    <>
      <div
        data-testid="ci-section"
        style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}
        onClick={onToggleExpand}
      >
        <CIRing passed={ciPassed} total={checks.length} blockingFailed={ciBlockingFailed} />
        <span style={{ color: ciColor, fontWeight: 600 }}>CI {ciPassed}/{checks.length}</span>
        {ciFailed > 0 && <span data-testid="ci-failed-count" style={{ color: 'var(--red)', fontSize: 10 }}>{ciFailed} failed</span>}
        <span style={{ fontSize: 8, color: 'var(--text-dim)' }}>{ciExpanded ? '\u25B2' : '\u25BC'}</span>
      </div>

      {ciExpanded && (
        <div
          data-testid="ci-detail"
          style={{
            marginBottom: 12, padding: '8px 12px', background: 'var(--card-bg)',
            border: '1px solid var(--border)', borderRadius: 6, maxHeight: 200, overflowY: 'auto',
            position: 'absolute', left: 12, right: 12, top: '100%', marginTop: 4, zIndex: 10,
          }}
        >
          {sortedChecks.map((ck, i) => {
            const ckName = ciName(ck) || '(unnamed)'
            const ckRes = ciResult(ck)
            const ckFail = isFailed(ckRes)
            const ckNB = isNonBlocking(ck)
            const ckPass = isSuccess(ckRes)

            let icon: React.ReactNode
            let itemStyle: React.CSSProperties = {}
            if (ckPass) {
              icon = <span style={{ color: 'var(--green)' }}>{'\u2713'}</span>
            } else if (ckFail && ckNB) {
              icon = <span data-testid="nb-fail-icon" style={{ color: 'var(--text-subtle)' }}>{'\u2717'}</span>
              itemStyle = { color: 'var(--text-subtle)' }
            } else if (ckFail) {
              icon = <span style={{ color: 'var(--red)' }}>{'\u2717'}</span>
            } else {
              icon = <span style={{ color: 'var(--yellow)' }}>{'\u2022'}</span>
            }

            return (
              <div
                key={i}
                data-testid="ci-check-item"
                style={{ fontSize: 10, display: 'flex', gap: 6, alignItems: 'center', marginBottom: 2, ...itemStyle }}
              >
                {icon} <span>{ckName}</span>
              </div>
            )
          })}
        </div>
      )}
    </>
  )
}
