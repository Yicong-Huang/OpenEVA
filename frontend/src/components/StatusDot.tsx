import type { TaskStatus } from '../types'

interface Props {
  status: TaskStatus | string
  style?: React.CSSProperties
}

export function StatusDot({ status, style }: Props) {
  return <span data-testid="status-dot" className={`dot dot-${status}`} style={style} />
}
