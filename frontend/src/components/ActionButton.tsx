interface Props {
  label: string
  onClick: () => void
  accent?: boolean
  style?: React.CSSProperties
  disabled?: boolean
}

export function ActionButton({ label, onClick, accent, style, disabled }: Props) {
  return (
    <button
      className={`btn-action${accent ? ' accent' : ''}`}
      onClick={(e) => { e.stopPropagation(); onClick() }}
      style={style}
      disabled={disabled}
    >
      {label}
    </button>
  )
}
