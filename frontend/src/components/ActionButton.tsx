interface Props {
  label: string
  // Event is forwarded so callers that need pointer position (e.g.
  // TaskCard's create-ticket-first popconfirm) can anchor a bubble
  // at the click site. Existing callers can keep their bare
  // `() => fn()` form thanks to TS arg-count widening.
  onClick: (event: React.MouseEvent) => void
  accent?: boolean
  style?: React.CSSProperties
  disabled?: boolean
}

export function ActionButton({ label, onClick, accent, style, disabled }: Props) {
  return (
    <button
      className={`btn-action${accent ? ' accent' : ''}`}
      onClick={(e) => { e.stopPropagation(); onClick(e) }}
      style={style}
      disabled={disabled}
    >
      {label}
    </button>
  )
}
