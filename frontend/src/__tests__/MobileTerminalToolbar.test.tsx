import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MobileTerminalToolbar } from '../components/MobileTerminalToolbar'

describe('MobileTerminalToolbar', () => {
  it('sends terminal control sequences from touch buttons', async () => {
    const sendInput = vi.fn()
    render(<MobileTerminalToolbar sendInput={sendInput} />)

    await userEvent.click(screen.getByRole('button', { name: 'Enter' }))
    await userEvent.click(screen.getByRole('button', { name: 'Ctrl+C' }))

    expect(sendInput).toHaveBeenNthCalledWith(1, '\r')
    expect(sendInput).toHaveBeenNthCalledWith(2, '\x03')
  })

  it('offers reliable history controls on mobile', async () => {
    const scrollHistory = vi.fn()
    render(
      <MobileTerminalToolbar sendInput={() => {}} scrollHistory={scrollHistory} />,
    )

    await userEvent.click(screen.getByRole('button', { name: '↑ 更早记录' }))
    await userEvent.click(screen.getByRole('button', { name: '最新记录 ↓' }))

    expect(scrollHistory).toHaveBeenNthCalledWith(1, 'up', 40)
    expect(scrollHistory).toHaveBeenNthCalledWith(2, 'down', 50)
  })
})
