import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { GlobalSearch } from '../components/GlobalSearch'

vi.mock('../api', () => ({
  api: {
    search: vi.fn(),
  },
}))

vi.mock('../hooks/useClickOutside', () => ({
  useClickOutside: vi.fn(),
}))

import { api } from '../api'
const mockSearch = api.search as unknown as ReturnType<typeof vi.fn>

describe('GlobalSearch', () => {
  beforeEach(() => {
    mockSearch.mockReset()
  })

  const setupProps = () => ({
    onNavigate: vi.fn(),
    onSelectTask: vi.fn(),
    onSelectPR: vi.fn(),
    onSelectReview: vi.fn(),
    onSelectTicket: vi.fn(),
  })

  it('renders the input', () => {
    render(<GlobalSearch {...setupProps()} />)
    expect(screen.getByTestId('global-search-input')).toBeInTheDocument()
  })

  it('does not show dropdown on empty input', () => {
    render(<GlobalSearch {...setupProps()} />)
    expect(screen.queryByTestId('global-search-dropdown')).not.toBeInTheDocument()
  })

  it('debounces and shows task results', async () => {
    mockSearch.mockResolvedValue({
      results: [
        { type: 'task', title: 'task-a', subtitle: 'proj - foundation', badge: 'open', project_id: 'p1', task_id: 'task-a' },
      ],
    })
    const props = setupProps()
    render(<GlobalSearch {...props} />)
    fireEvent.change(screen.getByTestId('global-search-input'), { target: { value: 'task-a' } })
    await waitFor(() => {
      expect(screen.getByTestId('global-search-result-0')).toBeInTheDocument()
    })
    expect(mockSearch).toHaveBeenCalledWith('task-a', 12)
  })

  it('clicking a task result navigates + selects task', async () => {
    mockSearch.mockResolvedValue({
      results: [
        { type: 'task', title: 'task-a', subtitle: 'x', badge: 'open', project_id: 'p1', task_id: 'task-a' },
      ],
    })
    const props = setupProps()
    render(<GlobalSearch {...props} />)
    fireEvent.change(screen.getByTestId('global-search-input'), { target: { value: 'task-a' } })
    await waitFor(() => screen.getByTestId('global-search-result-0'))
    fireEvent.click(screen.getByTestId('global-search-result-0'))
    expect(props.onNavigate).toHaveBeenCalledWith('p1', 'graph')
    expect(props.onSelectTask).toHaveBeenCalledWith('task-a')
  })

  it('clicking a pr result navigates to all-prs and selects PR', async () => {
    mockSearch.mockResolvedValue({
      results: [
        { type: 'pr', title: '#100', subtitle: 'x', badge: 'merged', project_id: 'p1', pr_number: 100, pr_repo: 'o/r', task_id: 'task-a' },
      ],
    })
    const props = setupProps()
    render(<GlobalSearch {...props} />)
    fireEvent.change(screen.getByTestId('global-search-input'), { target: { value: 'type:pr' } })
    await waitFor(() => screen.getByTestId('global-search-result-0'))
    fireEvent.click(screen.getByTestId('global-search-result-0'))
    expect(props.onNavigate).toHaveBeenCalledWith(null, 'all-prs')
    expect(props.onSelectPR).toHaveBeenCalledWith({
      repo: 'o/r', number: 100, taskId: 'task-a', projectId: 'p1',
    })
  })

  it('clicking a ticket result navigates to tickets and selects ticket', async () => {
    mockSearch.mockResolvedValue({
      results: [
        { type: 'ticket', title: 'EX-123', subtitle: 'x', badge: 'Open', project_id: 'EX', task_id: 'EX-123', ticket_key: 'EX-123', ticket_instance: 'primary' },
      ],
    })
    const props = setupProps()
    render(<GlobalSearch {...props} />)
    fireEvent.change(screen.getByTestId('global-search-input'), { target: { value: 'type:ticket EX-123' } })
    await waitFor(() => screen.getByTestId('global-search-result-0'))
    fireEvent.click(screen.getByTestId('global-search-result-0'))
    expect(props.onNavigate).toHaveBeenCalledWith(null, 'tickets')
    expect(props.onSelectTicket).toHaveBeenCalledWith({
      key: 'EX-123', instance: 'primary',
    })
  })

  it('clicking a review result navigates to all-reviews and selects review', async () => {
    const reviewUrl = 'https://github.com/o/r/pull/300'
    mockSearch.mockResolvedValue({
      results: [
        { type: 'review', title: '#300', subtitle: 'x', badge: 'queued', project_id: '', review_url: reviewUrl, pr_number: 300, pr_repo: 'o/r' },
      ],
    })
    const props = setupProps()
    render(<GlobalSearch {...props} />)
    fireEvent.change(screen.getByTestId('global-search-input'), { target: { value: 'type:review 300' } })
    await waitFor(() => screen.getByTestId('global-search-result-0'))
    fireEvent.click(screen.getByTestId('global-search-result-0'))
    expect(props.onNavigate).toHaveBeenCalledWith(null, 'all-reviews')
    expect(props.onSelectReview).toHaveBeenCalledWith(reviewUrl)
  })

  it('clicking a session result navigates to Live Tasks view with the task selected', async () => {
    // Old behaviour navigated to view='sessions', which no longer
    // exists in App.tsx -- the per-project sessions view was
    // consolidated into the global Live Tasks page. Picking a
    // session result must land on 'all-tasks' so the page can
    // auto-focus the matching task card and render its terminal.
    mockSearch.mockResolvedValue({
      results: [
        { type: 'session', title: 'sess-a', subtitle: 'x', badge: 'running', project_id: 'p1', task_id: 'task-a' },
      ],
    })
    const props = setupProps()
    render(<GlobalSearch {...props} />)
    fireEvent.change(screen.getByTestId('global-search-input'), { target: { value: 'type:session' } })
    await waitFor(() => screen.getByTestId('global-search-result-0'))
    fireEvent.click(screen.getByTestId('global-search-result-0'))
    expect(props.onNavigate).toHaveBeenCalledWith('p1', 'all-tasks')
    expect(props.onSelectTask).toHaveBeenCalledWith('task-a')
  })

  it('ArrowDown moves active index, Enter selects', async () => {
    mockSearch.mockResolvedValue({
      results: [
        { type: 'task', title: 'task-a', subtitle: 'x', badge: '', project_id: 'p1', task_id: 'task-a' },
        { type: 'task', title: 'task-b', subtitle: 'x', badge: '', project_id: 'p1', task_id: 'task-b' },
      ],
    })
    const props = setupProps()
    render(<GlobalSearch {...props} />)
    const input = screen.getByTestId('global-search-input')
    fireEvent.change(input, { target: { value: 'task' } })
    await waitFor(() => screen.getByTestId('global-search-result-1'))
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(props.onSelectTask).toHaveBeenCalledWith('task-b')
  })

  it('Escape closes the dropdown', async () => {
    mockSearch.mockResolvedValue({
      results: [
        { type: 'task', title: 'task-a', subtitle: 'x', badge: '', project_id: 'p1', task_id: 'task-a' },
      ],
    })
    const props = setupProps()
    render(<GlobalSearch {...props} />)
    const input = screen.getByTestId('global-search-input')
    fireEvent.change(input, { target: { value: 'task' } })
    await waitFor(() => screen.getByTestId('global-search-dropdown'))
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.queryByTestId('global-search-dropdown')).not.toBeInTheDocument()
  })

  it('shows "No results" when API returns empty', async () => {
    mockSearch.mockResolvedValue({ results: [] })
    render(<GlobalSearch {...setupProps()} />)
    fireEvent.change(screen.getByTestId('global-search-input'), { target: { value: 'xxxxxx' } })
    await waitFor(() => {
      expect(screen.getByText('No results.')).toBeInTheDocument()
    })
  })

  it('Cmd+K focuses the input', () => {
    render(<GlobalSearch {...setupProps()} />)
    const input = screen.getByTestId('global-search-input') as HTMLInputElement
    const focusSpy = vi.spyOn(input, 'focus')
    fireEvent.keyDown(window, { key: 'k', metaKey: true })
    expect(focusSpy).toHaveBeenCalled()
  })
})
