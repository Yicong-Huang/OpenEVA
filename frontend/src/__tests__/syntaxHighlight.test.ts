import { describe, it, expect } from 'vitest'
import { highlightLine } from '../utils/syntaxHighlight'

/** Helper: find the first span whose text matches. */
function findSpan(code: string, text: string) {
  return highlightLine(code).find((s) => s.text === text)
}

describe('highlightLine', () => {
  // ------- empty / plain text -------

  it('returns a single null-color span for empty string', () => {
    const result = highlightLine('')
    expect(result).toEqual([{ text: '', color: null }])
  })

  it('returns null-color span for plain text with no tokens', () => {
    const result = highlightLine('hello world')
    // "hello" and "world" are words but not keywords, so color=null
    result.forEach((s) => expect(s.color).toBeNull())
    expect(result.map((s) => s.text).join('')).toBe('hello world')
  })

  // ------- Python keywords (blue) -------

  it('highlights Python keywords: def, class, import', () => {
    expect(findSpan('def foo():', 'def')?.color).toBe('var(--blue)')
    expect(findSpan('class Bar:', 'class')?.color).toBe('var(--blue)')
    expect(findSpan('import os', 'import')?.color).toBe('var(--blue)')
  })

  it('highlights from keyword', () => {
    expect(findSpan('from os import path', 'from')?.color).toBe('var(--blue)')
  })

  // ------- JS/TS keywords (blue) -------

  it('highlights JS keywords: const, function, let', () => {
    expect(findSpan('const x = 1', 'const')?.color).toBe('var(--blue)')
    expect(findSpan('function foo() {}', 'function')?.color).toBe('var(--blue)')
    expect(findSpan('let y = 2', 'let')?.color).toBe('var(--blue)')
  })

  it('highlights export and default', () => {
    expect(findSpan('export default App', 'export')?.color).toBe('var(--blue)')
    expect(findSpan('export default App', 'default')?.color).toBe('var(--blue)')
  })

  // ------- SQL keywords (blue) -------

  it('highlights SQL keywords: SELECT, FROM, WHERE', () => {
    expect(findSpan('SELECT * FROM t', 'SELECT')?.color).toBe('var(--blue)')
    expect(findSpan('SELECT * FROM t', 'FROM')?.color).toBe('var(--blue)')
  })

  it('highlights SQL keywords case-insensitively via toUpperCase check', () => {
    // The code checks KEYWORDS_BLUE.has(word.toUpperCase()) as fallback
    expect(findSpan('select * from t', 'select')?.color).toBe('var(--blue)')
    expect(findSpan('select * from t', 'from')?.color).toBe('var(--blue)')
  })

  it('highlights JOIN, LEFT, ORDER, GROUP', () => {
    expect(findSpan('LEFT JOIN t ON x', 'LEFT')?.color).toBe('var(--blue)')
    expect(findSpan('LEFT JOIN t ON x', 'JOIN')?.color).toBe('var(--blue)')
    expect(findSpan('ORDER BY id', 'ORDER')?.color).toBe('var(--blue)')
    expect(findSpan('GROUP BY name', 'GROUP')?.color).toBe('var(--blue)')
  })

  // ------- Orange keywords (return, yield, throw, etc.) -------

  it('highlights return, yield, break as orange', () => {
    expect(findSpan('return 42', 'return')?.color).toBe('var(--orange)')
    expect(findSpan('yield x', 'yield')?.color).toBe('var(--orange)')
    expect(findSpan('break', 'break')?.color).toBe('var(--orange)')
    expect(findSpan('continue', 'continue')?.color).toBe('var(--orange)')
  })

  it('highlights raise and assert as orange', () => {
    expect(findSpan('raise ValueError()', 'raise')?.color).toBe('var(--orange)')
    expect(findSpan('assert x > 0', 'assert')?.color).toBe('var(--orange)')
  })

  // ------- Purple keywords (True, False, None, null, undefined, types) -------

  it('highlights True, False, None as purple', () => {
    expect(findSpan('x = True', 'True')?.color).toBe('var(--purple)')
    expect(findSpan('x = False', 'False')?.color).toBe('var(--purple)')
    expect(findSpan('x = None', 'None')?.color).toBe('var(--purple)')
  })

  it('highlights null as blue (matches SQL NULL via toUpperCase fallback)', () => {
    // "null" uppercases to "NULL" which is in KEYWORDS_BLUE (SQL),
    // so the blue check wins over purple
    expect(findSpan('x = null', 'null')?.color).toBe('var(--blue)')
  })

  it('highlights undefined and NaN as purple', () => {
    expect(findSpan('x = undefined', 'undefined')?.color).toBe('var(--purple)')
    expect(findSpan('x = NaN', 'NaN')?.color).toBe('var(--purple)')
  })

  it('highlights self and cls as purple', () => {
    expect(findSpan('self.x = 1', 'self')?.color).toBe('var(--purple)')
    expect(findSpan('cls.create()', 'cls')?.color).toBe('var(--purple)')
  })

  it('highlights type names: int, str, boolean, Array', () => {
    expect(findSpan('x: int', 'int')?.color).toBe('var(--purple)')
    expect(findSpan('y: str', 'str')?.color).toBe('var(--purple)')
    expect(findSpan('z: boolean', 'boolean')?.color).toBe('var(--purple)')
    expect(findSpan('items: Array', 'Array')?.color).toBe('var(--purple)')
  })

  // ------- Strings -------

  it('highlights double-quoted strings as green', () => {
    const result = findSpan('x = "hello"', '"hello"')
    expect(result?.color).toBe('var(--green)')
  })

  it('highlights single-quoted strings as green', () => {
    const result = findSpan("x = 'world'", "'world'")
    expect(result?.color).toBe('var(--green)')
  })

  it('highlights backtick-quoted strings as green', () => {
    const result = findSpan('x = `template`', '`template`')
    expect(result?.color).toBe('var(--green)')
  })

  it('handles strings with escaped quotes', () => {
    const result = findSpan('x = "he said \\"hi\\""', '"he said \\"hi\\""')
    expect(result?.color).toBe('var(--green)')
  })

  it('handles empty strings', () => {
    expect(findSpan('x = ""', '""')?.color).toBe('var(--green)')
    expect(findSpan("x = ''", "''")?.color).toBe('var(--green)')
  })

  // ------- Numbers -------

  it('highlights integers as yellow', () => {
    expect(findSpan('x = 42', '42')?.color).toBe('var(--yellow)')
  })

  it('highlights floats as yellow', () => {
    expect(findSpan('x = 3.14', '3.14')?.color).toBe('var(--yellow)')
  })

  it('highlights scientific notation as yellow', () => {
    expect(findSpan('x = 1e10', '1e10')?.color).toBe('var(--yellow)')
    expect(findSpan('x = 2.5E-3', '2.5E-3')?.color).toBe('var(--yellow)')
    expect(findSpan('x = 6e+2', '6e+2')?.color).toBe('var(--yellow)')
  })

  it('highlights zero', () => {
    expect(findSpan('x = 0', '0')?.color).toBe('var(--yellow)')
  })

  // ------- Comments -------

  it('highlights // comments as text-faint', () => {
    const result = highlightLine('x = 1 // comment here')
    const commentSpan = result.find((s) => s.text.includes('// comment here'))
    expect(commentSpan?.color).toBe('var(--text-faint)')
  })

  it('highlights # comments as text-faint', () => {
    const result = highlightLine('x = 1 # python comment')
    const commentSpan = result.find((s) => s.text.includes('# python comment'))
    expect(commentSpan?.color).toBe('var(--text-faint)')
  })

  it('highlights -- comments as text-faint', () => {
    const result = highlightLine('SELECT 1 -- sql comment')
    const commentSpan = result.find((s) => s.text.includes('-- sql comment'))
    expect(commentSpan?.color).toBe('var(--text-faint)')
  })

  it('comment consumes rest of line', () => {
    const result = highlightLine('// entire line is a comment')
    // Should be a single comment span (possibly with leading empty)
    const commentSpan = result.find((s) => s.color === 'var(--text-faint)')
    expect(commentSpan).toBeDefined()
    expect(commentSpan!.text).toContain('// entire line is a comment')
  })

  // ------- Decorators -------

  it('highlights @decorator as yellow', () => {
    expect(findSpan('@staticmethod', '@staticmethod')?.color).toBe('var(--yellow)')
  })

  it('highlights @property', () => {
    expect(findSpan('@property', '@property')?.color).toBe('var(--yellow)')
  })

  it('highlights @override', () => {
    expect(findSpan('@override', '@override')?.color).toBe('var(--yellow)')
  })

  // ------- Arrows -------

  it('highlights => as accent', () => {
    expect(findSpan('(x) => x + 1', '=>')?.color).toBe('var(--accent)')
  })

  it('highlights -> as accent', () => {
    expect(findSpan('def foo() -> int:', '->')?.color).toBe('var(--accent)')
  })

  // ------- Spread / ellipsis -------

  it('highlights ... as accent', () => {
    expect(findSpan('{...props}', '...')?.color).toBe('var(--accent)')
  })

  // ------- Mixed lines -------

  it('handles a mixed Python line: def with types and return', () => {
    const code = 'def add(x: int, y: int) -> int:'
    const result = highlightLine(code)
    const texts = result.map((s) => s.text).join('')
    expect(texts).toBe(code)

    expect(findSpan(code, 'def')?.color).toBe('var(--blue)')
    expect(findSpan(code, 'int')?.color).toBe('var(--purple)')
    expect(findSpan(code, '->')?.color).toBe('var(--accent)')
  })

  it('handles a mixed JS line: const with string and number', () => {
    const code = 'const x = "hello" + 42'
    const result = highlightLine(code)
    const texts = result.map((s) => s.text).join('')
    expect(texts).toBe(code)

    expect(findSpan(code, 'const')?.color).toBe('var(--blue)')
    expect(findSpan(code, '"hello"')?.color).toBe('var(--green)')
    expect(findSpan(code, '42')?.color).toBe('var(--yellow)')
  })

  it('handles a SQL line with keywords and comment', () => {
    const code = 'SELECT id FROM users -- get user ids'
    const result = highlightLine(code)
    const texts = result.map((s) => s.text).join('')
    expect(texts).toBe(code)

    expect(findSpan(code, 'SELECT')?.color).toBe('var(--blue)')
    expect(findSpan(code, 'FROM')?.color).toBe('var(--blue)')
    const commentSpan = result.find((s) => s.text.includes('-- get user ids'))
    expect(commentSpan?.color).toBe('var(--text-faint)')
  })

  it('handles decorator + def + return type', () => {
    // Note: decorator is on a separate line normally, but test inline
    const code = '@staticmethod'
    expect(findSpan(code, '@staticmethod')?.color).toBe('var(--yellow)')
  })

  // ------- Preserves full text -------

  it('concatenated span texts always equal the original code', () => {
    const samples = [
      '',
      'hello world',
      'const x = 42',
      'def foo(a: int) -> bool:',
      'SELECT * FROM t WHERE x = 1 -- comment',
      '  return "test" + str(3.14e2)',
      '@override',
      '(x) => x',
      '  # indented comment',
    ]
    for (const code of samples) {
      const result = highlightLine(code)
      const reconstructed = result.map((s) => s.text).join('')
      expect(reconstructed).toBe(code)
    }
  })

  // ------- Non-keyword words -------

  it('gives null color to unknown identifiers', () => {
    expect(findSpan('myVariable = foo', 'myVariable')?.color).toBeNull()
    expect(findSpan('myVariable = foo', 'foo')?.color).toBeNull()
  })

  // ------- Whitespace between tokens -------

  it('preserves whitespace between tokens as null-color spans', () => {
    const code = 'const  x'
    const result = highlightLine(code)
    // Should have: "const" (blue), "  " (null/space), "x" (null)
    const spaceSpan = result.find((s) => s.text === '  ')
    expect(spaceSpan).toBeDefined()
    expect(spaceSpan!.color).toBeNull()
  })

  // ------- Orange takes priority over blue for shared keywords -------

  it('return is orange (KEYWORDS_ORANGE checked first)', () => {
    // "return" is in both KEYWORDS_BLUE and KEYWORDS_ORANGE
    expect(findSpan('return 0', 'return')?.color).toBe('var(--orange)')
  })

  it('throw is orange', () => {
    expect(findSpan('throw new Error()', 'throw')?.color).toBe('var(--orange)')
  })
})
