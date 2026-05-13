import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { FileList } from '../components/pr/FileList'
import type { PRDetail } from '../types'

const mockGetPRDiff = vi.fn().mockResolvedValue({ files: {} })

vi.mock('../api', () => ({
  api: {
    getPRDiff: (...args: unknown[]) => mockGetPRDiff(...args),
  },
}))

type FileEntry = PRDetail['files'][number]

const files: FileEntry[] = [
  { path: 'src/app.tsx', additions: 10, deletions: 3 },
  { path: 'src/utils.ts', additions: 5, deletions: 0 },
  { path: 'README.md', additions: 0, deletions: 8 },
]

const defaultProps = { repo: 'org/repo', prNumber: 42 }

describe('FileList', () => {
  beforeEach(() => {
    mockGetPRDiff.mockReset()
    mockGetPRDiff.mockResolvedValue({ files: {} })
  })

  it('renders file paths', () => {
    render(<FileList files={files} {...defaultProps} />)
    expect(screen.getByText('src/app.tsx')).toBeInTheDocument()
    expect(screen.getByText('src/utils.ts')).toBeInTheDocument()
    expect(screen.getByText('README.md')).toBeInTheDocument()
  })

  it('shows file count header', () => {
    render(<FileList files={files} {...defaultProps} />)
    expect(screen.getByText('Files (3)')).toBeInTheDocument()
  })

  it('shows additions and deletions per file', () => {
    render(<FileList files={files} {...defaultProps} />)
    expect(screen.getByText('+10')).toBeInTheDocument()
    expect(screen.getByText('-3')).toBeInTheDocument()
    expect(screen.getByText('+5')).toBeInTheDocument()
    expect(screen.getByText('-0')).toBeInTheDocument()
    expect(screen.getByText('+0')).toBeInTheDocument()
    expect(screen.getByText('-8')).toBeInTheDocument()
  })

  it('returns null for empty file list', () => {
    const { container } = render(<FileList files={[]} {...defaultProps} />)
    expect(container.innerHTML).toBe('')
  })

  it('returns null for undefined files', () => {
    const { container } = render(<FileList files={undefined as unknown as PRDetail['files']} {...defaultProps} />)
    expect(container.innerHTML).toBe('')
  })

  it('handles files with missing additions/deletions', () => {
    const sparse: FileEntry[] = [
      { path: 'foo.ts', additions: undefined as unknown as number, deletions: undefined as unknown as number },
    ]
    render(<FileList files={sparse} {...defaultProps} />)
    expect(screen.getByText('foo.ts')).toBeInTheDocument()
    expect(screen.getByText('+0')).toBeInTheDocument()
    expect(screen.getByText('-0')).toBeInTheDocument()
  })

  it('shows expand arrow on each file', () => {
    render(<FileList files={files} {...defaultProps} />)
    // Each file should have a clickable row with arrow
    const fileRows = screen.getAllByText(/src\/app\.tsx|src\/utils\.ts|README\.md/)
    expect(fileRows.length).toBe(3)
  })

  it('clicking a file triggers diff loading', async () => {
    render(<FileList files={files} {...defaultProps} />)
    // Click first file
    fireEvent.click(screen.getByText('src/app.tsx'))
    expect(mockGetPRDiff).toHaveBeenCalledWith('org/repo', 42)
  })

  // ===================== Expandable diffs =====================

  /**
   * Helper: find a diff line div by checking textContent across child spans.
   * Needed because syntax highlighting splits text into multiple <span> elements.
   */
  function findDiffLine(container: HTMLElement, textMatch: RegExp): HTMLElement | null {
    const allLineDivs = container.querySelectorAll('div[data-line-idx]')
    for (const div of allLineDivs) {
      if (textMatch.test(div.textContent || '')) return div as HTMLElement
    }
    return null
  }

  describe('expanding files with diffs', () => {
    const sampleDiff = [
      'diff --git a/src/app.tsx b/src/app.tsx',
      '--- a/src/app.tsx',
      '+++ b/src/app.tsx',
      '@@ -1,3 +1,4 @@',
      ' import React from "react"',
      '-const old = 1',
      '+const updated = 2',
      '+const added = 3',
      ' export default App',
    ].join('\n')

    it('shows "Loading diff..." before diff data arrives', async () => {
      // Return a promise that we never resolve so loading state is visible
      let resolvePromise: (value: unknown) => void
      mockGetPRDiff.mockReturnValue(new Promise((resolve) => { resolvePromise = resolve }))

      render(<FileList files={[files[0]]} {...defaultProps} />)
      fireEvent.click(screen.getByText('src/app.tsx'))

      expect(screen.getByText('Loading diff...')).toBeInTheDocument()

      // Clean up by resolving
      resolvePromise!({ files: {} })
    })

    it('renders diff lines after loading', async () => {
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': sampleDiff },
      })

      const { container } = render(<FileList files={[files[0]]} {...defaultProps} />)
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        // Syntax highlighting splits text into multiple spans,
        // so check textContent of the line div container
        const importLine = findDiffLine(container, /import React from/)
        expect(importLine).toBeTruthy()
      })
    })

    it('renders add lines with green border', async () => {
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': sampleDiff },
      })

      const { container } = render(<FileList files={[files[0]]} {...defaultProps} />)
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        const addLine = findDiffLine(container, /updated = 2/)
        expect(addLine).toBeTruthy()
        expect(addLine!.style.background).toContain('rgba(34')
        expect(addLine!.style.borderLeft).toBe('3px solid var(--green)')
      })
    })

    it('renders remove lines with red border', async () => {
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': sampleDiff },
      })

      const { container } = render(<FileList files={[files[0]]} {...defaultProps} />)
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        const rmLine = findDiffLine(container, /old = 1/)
        expect(rmLine).toBeTruthy()
        expect(rmLine!.style.background).toContain('rgba(239')
        expect(rmLine!.style.borderLeft).toBe('3px solid var(--red)')
      })
    })

    it('renders context lines with transparent background', async () => {
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': sampleDiff },
      })

      const { container } = render(<FileList files={[files[0]]} {...defaultProps} />)
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        const ctxLine = findDiffLine(container, /export default App/)
        expect(ctxLine).toBeTruthy()
        expect(ctxLine!.style.background).toBe('transparent')
      })
    })

    it('renders hunk header (@@ line) with blue-tinted background', async () => {
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': sampleDiff },
      })

      const { container } = render(<FileList files={[files[0]]} {...defaultProps} />)
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        const hunkLine = findDiffLine(container, /@@ -1,3 \+1,4 @@/)
        expect(hunkLine).toBeTruthy()
        expect(hunkLine!.style.background).toContain('rgba(59')
      })
    })

    it('clicking an expanded file collapses it', async () => {
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': sampleDiff },
      })

      const { container } = render(<FileList files={[files[0]]} {...defaultProps} />)
      // Expand
      fireEvent.click(screen.getByText('src/app.tsx'))
      await waitFor(() => {
        expect(findDiffLine(container, /import React from/)).toBeTruthy()
      })

      // Collapse
      fireEvent.click(screen.getByText('src/app.tsx'))
      expect(findDiffLine(container, /import React from/)).toBeNull()
    })

    it('only calls getPRDiff once even when expanding multiple files', async () => {
      mockGetPRDiff.mockResolvedValue({
        files: {
          'src/app.tsx': sampleDiff,
          'src/utils.ts': '@@ -1,1 +1,1 @@\n-old\n+new',
        },
      })

      render(<FileList files={files.slice(0, 2)} {...defaultProps} />)
      // Click first file -> triggers loadAllDiffs
      fireEvent.click(screen.getByText('src/app.tsx'))
      await waitFor(() => {
        expect(mockGetPRDiff).toHaveBeenCalledTimes(1)
      })

      // Click second file -> should NOT call getPRDiff again (diffs already loaded)
      fireEvent.click(screen.getByText('src/utils.ts'))
      // Still only one call
      expect(mockGetPRDiff).toHaveBeenCalledTimes(1)
    })

    it('handles getPRDiff failure gracefully', async () => {
      mockGetPRDiff.mockRejectedValue(new Error('API error'))

      render(<FileList files={[files[0]]} {...defaultProps} />)
      fireEvent.click(screen.getByText('src/app.tsx'))

      // Should show "Loading diff..." but no crash
      await waitFor(() => {
        expect(screen.getByText('Loading diff...')).toBeInTheDocument()
      })
    })
  })

  // ===================== parseDiff (tested via rendering) =====================

  describe('diff parsing via rendering', () => {
    it('parses line numbers correctly from hunk header', async () => {
      const diff = '@@ -10,3 +20,4 @@\n context line\n+added line\n-removed line'
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': diff },
      })

      const { container } = render(<FileList files={[files[0]]} {...defaultProps} />)
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        // Context line: oldLine=10, newLine=20
        const ctxLine = findDiffLine(container, /context line/)
        expect(ctxLine).toBeTruthy()
        expect(ctxLine!.getAttribute('data-new-line')).toBe('20')

        // Add line: newLine=21
        const addLine = findDiffLine(container, /added line/)
        expect(addLine).toBeTruthy()
        expect(addLine!.getAttribute('data-new-line')).toBe('21')

        // Remove line: no new line number
        const rmLine = findDiffLine(container, /removed line/)
        expect(rmLine).toBeTruthy()
        expect(rmLine!.getAttribute('data-new-line')).toBe('')
      })
    })

    it('parses diff header lines (--- and +++ and diff)', async () => {
      const diff = 'diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,1 +1,1 @@\n-old\n+new'
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': diff },
      })

      const { container } = render(<FileList files={[files[0]]} {...defaultProps} />)
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        // Header lines should be rendered (with header style = transparent bg, dim text)
        const headerLine = findDiffLine(container, /diff --git a\/f b\/f/)
        expect(headerLine).toBeTruthy()
        expect(headerLine!.style.background).toBe('transparent')
      })
    })

    it('strips leading +/- from add/remove display text', async () => {
      // Use plain words to avoid syntax highlighting splitting
      const diff = '@@ -1,1 +1,1 @@\n-removed_value\n+added_value'
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': diff },
      })

      const { container } = render(<FileList files={[files[0]]} {...defaultProps} />)
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        // The leading - and + should be replaced with a space
        const rmLine = findDiffLine(container, /removed_value/)
        expect(rmLine).toBeTruthy()
        // The textContent should start with a space (not -)
        const codeSpan = rmLine!.querySelector('span[style*="padding-left"]')
        expect(codeSpan?.textContent).toContain('removed_value')

        const addLine = findDiffLine(container, /added_value/)
        expect(addLine).toBeTruthy()
      })
    })

    it('handles multiple hunk headers in one diff', async () => {
      const diff = [
        '@@ -1,2 +1,2 @@',
        '-line1old',
        '+line1new',
        ' context',
        '@@ -50,2 +50,2 @@',
        '-line50old',
        '+line50new',
      ].join('\n')

      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': diff },
      })

      const { container } = render(<FileList files={[files[0]]} {...defaultProps} />)
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        // Both hunk headers should render
        expect(findDiffLine(container, /@@ -1,2 \+1,2 @@/)).toBeTruthy()
        expect(findDiffLine(container, /@@ -50,2 \+50,2 @@/)).toBeTruthy()
        // Both add lines should render
        expect(findDiffLine(container, /line1new/)).toBeTruthy()
        expect(findDiffLine(container, /line50new/)).toBeTruthy()
      })
    })

    it('applies syntax highlighting to diff code lines', async () => {
      // "const" is a keyword (blue), "42" is a number (yellow)
      const diff = '@@ -1,1 +1,2 @@\n+const x = 42'
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': diff },
      })

      render(<FileList files={[files[0]]} {...defaultProps} />)
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        // "const" should be in its own span with blue color
        const constSpans = screen.getAllByText('const')
        // Find the one that is syntax-highlighted (has color style)
        const highlighted = constSpans.find((el) => el.style.color === 'var(--blue)')
        expect(highlighted).toBeTruthy()

        // "42" should be highlighted as a number (yellow)
        const numSpan = screen.getByText('42')
        expect(numSpan).toBeInTheDocument()
        expect(numSpan.style.color).toBe('var(--yellow)')
      })
    })
  })

  // ===================== Selection popup =====================

  describe('selection popup', () => {
    const sampleDiff = [
      '@@ -1,3 +1,4 @@',
      ' import React from "react"',
      '-const old = 1',
      '+const updated = 2',
      '+const added = 3',
      ' export default App',
    ].join('\n')

    /**
     * Helper: simulate text selection on a diff line and trigger mouseup.
     * We mock window.getSelection to return the selected text.
     */
    function simulateSelection(container: HTMLElement, text: string) {
      // Find the first diff data line to anchor the selection
      const lineDivs = container.querySelectorAll('div[data-line-idx]')
      const targetLine = lineDivs[1] || lineDivs[0] // use a code line, not the hunk header

      const mockRange = {
        getBoundingClientRect: () => ({ right: 200, top: 100, left: 100, bottom: 120, width: 100, height: 20 }),
      }

      // Must set up the mock BEFORE triggering mouseup
      const origGetSelection = window.getSelection
      window.getSelection = vi.fn().mockReturnValue({
        isCollapsed: false,
        toString: () => text,
        anchorNode: targetLine,
        focusNode: targetLine,
        getRangeAt: () => mockRange,
      }) as unknown as typeof window.getSelection

      // The diff container is the parent div of the line divs (has onMouseUp handler)
      // It is the element that directly contains data-line-idx children
      const diffContainer = targetLine?.parentElement
      if (diffContainer) {
        fireEvent.mouseUp(diffContainer)
      }

      return () => {
        window.getSelection = origGetSelection
      }
    }

    it('shows selection popup with Comment button when text is selected', async () => {
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': sampleDiff },
      })

      const onComment = vi.fn()
      const { container } = render(
        <FileList files={[files[0]]} {...defaultProps} onComment={onComment} />,
      )
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        expect(findDiffLine(container, /updated = 2/)).toBeTruthy()
      })

      const restore = simulateSelection(container, 'const updated = 2')

      await waitFor(() => {
        // The popup should show the Comment button
        const commentBtns = screen.getAllByText('Comment')
        // There should be at least one Comment button from the popup
        expect(commentBtns.length).toBeGreaterThanOrEqual(1)
      })

      restore()
    })

    it('shows Ask Agent button when onAskAgent is provided', async () => {
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': sampleDiff },
      })

      const onComment = vi.fn()
      const onAskAgent = vi.fn()
      const { container } = render(
        <FileList files={[files[0]]} {...defaultProps} onComment={onComment} onAskAgent={onAskAgent} />,
      )
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        expect(findDiffLine(container, /updated = 2/)).toBeTruthy()
      })

      const restore = simulateSelection(container, 'const updated = 2')

      await waitFor(() => {
        expect(screen.getByText('Ask Agent')).toBeInTheDocument()
      })

      restore()
    })

    it('Comment button in popup calls onComment with correct args', async () => {
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': sampleDiff },
      })

      const onComment = vi.fn()
      const { container } = render(
        <FileList files={[files[0]]} {...defaultProps} onComment={onComment} />,
      )
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        expect(findDiffLine(container, /updated = 2/)).toBeTruthy()
      })

      const restore = simulateSelection(container, 'const updated = 2')

      await waitFor(() => {
        // The popup textarea should exist
        expect(screen.getByPlaceholderText(/Comment or question/)).toBeInTheDocument()
      })

      // Type a comment in the popup textarea
      const textarea = screen.getByPlaceholderText(/Comment or question/)
      fireEvent.change(textarea, { target: { value: 'Needs clarification' } })

      // Click Comment button in popup (may be second one after the page-level one)
      const commentBtns = screen.getAllByText('Comment')
      fireEvent.click(commentBtns[commentBtns.length - 1])

      expect(onComment).toHaveBeenCalledWith('src/app.tsx', expect.any(Number), 'Needs clarification')

      restore()
    })

    it('Ask Agent button in popup calls onAskAgent with context', async () => {
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': sampleDiff },
      })

      const onComment = vi.fn()
      const onAskAgent = vi.fn()
      const { container } = render(
        <FileList files={[files[0]]} {...defaultProps} onComment={onComment} onAskAgent={onAskAgent} />,
      )
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        expect(findDiffLine(container, /updated = 2/)).toBeTruthy()
      })

      const restore = simulateSelection(container, 'const updated = 2')

      await waitFor(() => {
        expect(screen.getByText('Ask Agent')).toBeInTheDocument()
      })

      // Type a question in the popup textarea
      const textarea = screen.getByPlaceholderText(/Comment or question/)
      fireEvent.change(textarea, { target: { value: 'Why was this changed?' } })

      // Click Ask Agent
      fireEvent.click(screen.getByText('Ask Agent'))

      expect(onAskAgent).toHaveBeenCalledWith(expect.stringContaining('Why was this changed?'))

      restore()
    })
  })

  // ===================== Multiple hunks =====================

  describe('multiple hunks in one file', () => {
    it('renders multiple hunks with correct line numbers', async () => {
      const multiHunkDiff = [
        '@@ -5,3 +5,3 @@',
        '-old_function()',
        '+new_function()',
        ' context_between',
        '@@ -100,2 +100,2 @@',
        '-old_value = 10',
        '+new_value = 20',
      ].join('\n')

      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': multiHunkDiff },
      })

      const { container } = render(<FileList files={[files[0]]} {...defaultProps} />)
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        // Both hunks should render
        expect(findDiffLine(container, /@@ -5,3 \+5,3 @@/)).toBeTruthy()
        expect(findDiffLine(container, /@@ -100,2 \+100,2 @@/)).toBeTruthy()

        // Both add lines
        const newFunc = findDiffLine(container, /new_function/)
        expect(newFunc).toBeTruthy()
        expect(newFunc!.getAttribute('data-new-line')).toBe('5')

        const newVal = findDiffLine(container, /new_value = 20/)
        expect(newVal).toBeTruthy()
        expect(newVal!.getAttribute('data-new-line')).toBe('100')
      })
    })
  })

  // ===================== Syntax highlighting =====================

  describe('syntax highlighting details', () => {
    it('highlights keywords and strings with colored spans', async () => {
      // Use a diff with recognizable syntax tokens
      const diff = '@@ -1,1 +1,2 @@\n+function hello() { return "world" }'
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': diff },
      })

      const { container } = render(<FileList files={[files[0]]} {...defaultProps} />)
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        // "function" is a keyword -> blue
        const funcSpans = screen.getAllByText('function')
        const blueFunc = funcSpans.find((el) => el.style.color === 'var(--blue)')
        expect(blueFunc).toBeTruthy()

        // "world" is a string -> green
        const worldSpan = container.querySelector('span[style*="var(--green)"]')
        expect(worldSpan).toBeTruthy()
      })
    })

    it('number literals are highlighted with yellow color', async () => {
      const diff = '@@ -1,1 +1,2 @@\n+const limit = 9999'
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': diff },
      })

      render(<FileList files={[files[0]]} {...defaultProps} />)
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        const numSpan = screen.getByText('9999')
        expect(numSpan).toBeInTheDocument()
        expect(numSpan.style.color).toBe('var(--yellow)')
      })
    })

    it('renders context lines without syntax color applied to plain text', async () => {
      const diff = '@@ -1,1 +1,2 @@\n sometext_without_keyword'
      mockGetPRDiff.mockResolvedValue({
        files: { 'src/app.tsx': diff },
      })

      const { container } = render(<FileList files={[files[0]]} {...defaultProps} />)
      fireEvent.click(screen.getByText('src/app.tsx'))

      await waitFor(() => {
        const ctxLine = findDiffLine(container, /sometext_without_keyword/)
        expect(ctxLine).toBeTruthy()
        // Context lines have transparent background
        expect(ctxLine!.style.background).toBe('transparent')
      })
    })
  })
})
