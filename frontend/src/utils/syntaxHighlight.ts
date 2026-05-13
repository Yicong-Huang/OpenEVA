/**
 * Lightweight syntax highlighter for diff views.
 * No external dependencies. Covers common patterns in Python, Java, Scala, TypeScript, SQL.
 * Returns an array of {text, color} spans for a single line of code.
 */

interface Span {
  text: string
  color: string | null  // null = inherit (use line's default color)
}

// Language keywords by color category
const KEYWORDS_BLUE = new Set([
  // Python
  'def', 'class', 'import', 'from', 'return', 'yield', 'async', 'await',
  'if', 'elif', 'else', 'for', 'while', 'try', 'except', 'finally',
  'with', 'as', 'raise', 'pass', 'break', 'continue', 'lambda', 'global', 'nonlocal',
  // Java/Scala
  'public', 'private', 'protected', 'static', 'final', 'abstract', 'interface',
  'extends', 'implements', 'override', 'new', 'this', 'super', 'void',
  'case', 'match', 'sealed', 'trait', 'object', 'implicit', 'lazy', 'val', 'var',
  'package', 'throws', 'throw', 'synchronized', 'volatile', 'transient', 'native',
  // TypeScript/JavaScript
  'const', 'let', 'function', 'export', 'default', 'type', 'interface',
  'enum', 'namespace', 'declare', 'module', 'require', 'typeof', 'keyof',
  'readonly', 'instanceof', 'in', 'of', 'delete',
  // SQL
  'SELECT', 'FROM', 'WHERE', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP',
  'TABLE', 'INDEX', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'ON', 'AND', 'OR',
  'NOT', 'NULL', 'IS', 'AS', 'GROUP', 'BY', 'ORDER', 'HAVING', 'LIMIT', 'OFFSET',
  'INTO', 'VALUES', 'SET', 'PRIMARY', 'KEY', 'FOREIGN', 'REFERENCES', 'CASCADE',
])

const KEYWORDS_PURPLE = new Set([
  'True', 'False', 'None', 'self', 'cls',
  'true', 'false', 'null', 'undefined', 'NaN', 'Infinity',
  'bool', 'int', 'float', 'str', 'list', 'dict', 'tuple', 'set', 'bytes',
  'String', 'Int', 'Long', 'Double', 'Float', 'Boolean', 'Byte', 'Short', 'Char',
  'number', 'string', 'boolean', 'any', 'never', 'unknown', 'symbol', 'bigint',
  'Array', 'Map', 'Set', 'Record', 'Promise', 'Optional',
])

const KEYWORDS_ORANGE = new Set([
  'return', 'yield', 'throw', 'raise', 'break', 'continue',
  'assert', 'debugger',
])

// Regex patterns for tokenization (order matters)
const TOKEN_RE = /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)|(\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|(\/\/.*|#.*|--.*)|(@\w+)|(=>|->|\.\.\.)|(\b[A-Za-z_]\w*\b)/g

export function highlightLine(code: string): Span[] {
  const spans: Span[] = []
  let lastIndex = 0

  TOKEN_RE.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = TOKEN_RE.exec(code)) !== null) {
    // Text before match
    if (m.index > lastIndex) {
      spans.push({ text: code.slice(lastIndex, m.index), color: null })
    }

    const [full, str, num, comment, decorator, arrow, word] = m
    if (str) {
      spans.push({ text: full, color: 'var(--green)' })
    } else if (num) {
      spans.push({ text: full, color: 'var(--yellow)' })
    } else if (comment) {
      spans.push({ text: full, color: 'var(--text-faint)' })
    } else if (decorator) {
      spans.push({ text: full, color: 'var(--yellow)' })
    } else if (arrow) {
      spans.push({ text: full, color: 'var(--accent)' })
    } else if (word) {
      if (KEYWORDS_ORANGE.has(word)) {
        spans.push({ text: full, color: 'var(--orange)' })
      } else if (KEYWORDS_BLUE.has(word) || KEYWORDS_BLUE.has(word.toUpperCase())) {
        spans.push({ text: full, color: 'var(--blue)' })
      } else if (KEYWORDS_PURPLE.has(word)) {
        spans.push({ text: full, color: 'var(--purple)' })
      } else {
        spans.push({ text: full, color: null })
      }
    } else {
      spans.push({ text: full, color: null })
    }

    lastIndex = m.index + full.length
  }

  // Remaining text
  if (lastIndex < code.length) {
    spans.push({ text: code.slice(lastIndex), color: null })
  }

  return spans.length > 0 ? spans : [{ text: code, color: null }]
}
