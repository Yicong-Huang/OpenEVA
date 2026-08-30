interface MobileTerminalToolbarProps {
  sendInput: (data: string) => void
  scrollHistory?: (direction: 'up' | 'down', lines?: number) => void
}

// Typed explicitly rather than `as const`: with a const assertion each
// element gets its own literal type, so the array's union only carries
// `primary` on the Enter member and reading `key.primary` off the union
// is a type error even though it is merely undefined at runtime.
interface TerminalKey {
  label: string
  value: string
  title: string
  primary?: boolean
}

const KEYS: readonly TerminalKey[] = [
  { label: 'Esc', value: '\x1b', title: 'Escape' },
  { label: 'Tab', value: '\t', title: 'Tab' },
  { label: 'Ctrl+C', value: '\x03', title: 'Interrupt' },
  { label: 'Enter', value: '\r', title: 'Send / Enter', primary: true },
]

/** Touch keyboard helpers for xterm on phones and tablets. */
export function MobileTerminalToolbar({ sendInput, scrollHistory }: MobileTerminalToolbarProps) {
  return (
    <div className="mobile-terminal-toolbar" aria-label="Terminal keys">
      <div className="mobile-terminal-key-row">
        {KEYS.map((key) => (
          <button
            key={key.label}
            type="button"
            className={key.primary ? 'primary' : undefined}
            title={key.title}
            onPointerDown={(event) => event.preventDefault()}
            onClick={() => sendInput(key.value)}
          >
            {key.label}
          </button>
        ))}
      </div>
      {scrollHistory && (
        <div className="mobile-terminal-history-row">
          <button type="button" onClick={() => scrollHistory('up', 40)}>
            ↑ 更早记录
          </button>
          <button type="button" onClick={() => scrollHistory('down', 50)}>
            最新记录 ↓
          </button>
        </div>
      )}
    </div>
  )
}
