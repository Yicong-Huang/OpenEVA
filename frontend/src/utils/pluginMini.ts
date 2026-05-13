import type { CSSProperties } from 'react'

// Shared style for the collapsed-sidebar mini variants of plugins.
// Each plugin's Mini<Name>Plugin component spreads this onto its
// outermost div so the sidebar's compact column stays visually
// consistent regardless of which extension shipped the plugin.
export const miniPluginStyle: CSSProperties = {
  width: 36,
  height: 36,
  borderRadius: 6,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  cursor: 'default',
  fontSize: 10,
  position: 'relative',
  fontFamily: 'monospace',
}
