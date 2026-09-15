/**
 * codeHighlight — dependency-free token highlighter for code blocks.
 *
 * Produces React nodes (never raw HTML) so there is no XSS surface.
 * Deliberately language-agnostic: it colours strings, numbers, comments and a
 * common keyword set covering the languages agents most often emit
 * (js/ts/python/sql/shell/json).
 */
import { type ReactNode } from 'react';

const KEYWORDS = new Set([
  'const', 'let', 'var', 'function', 'return', 'if', 'else', 'for', 'while', 'do',
  'switch', 'case', 'break', 'continue', 'new', 'class', 'extends', 'import', 'from',
  'export', 'default', 'async', 'await', 'try', 'catch', 'finally', 'throw', 'typeof',
  'instanceof', 'in', 'of', 'this', 'super', 'yield', 'void', 'delete',
  'def', 'lambda', 'None', 'True', 'False', 'and', 'or', 'not', 'elif', 'with', 'as',
  'pass', 'raise', 'global', 'nonlocal', 'assert',
  'select', 'insert', 'update', 'delete', 'where', 'join', 'null', 'true', 'false',
  'public', 'private', 'protected', 'static', 'interface', 'type', 'enum',
]);

const TOKEN_RE =
  /(\/\/[^\n]*|#[^\n]*|\/\*[\s\S]*?\*\/)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_$][\w$]*)/g;

export function highlightCode(code: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  let key = 0;
  let m: RegExpExecArray | null;
  TOKEN_RE.lastIndex = 0;
  while ((m = TOKEN_RE.exec(code)) !== null) {
    if (m.index > last) nodes.push(code.slice(last, m.index));
    const [full, comment, str, num, ident] = m;
    if (comment) {
      nodes.push(<span key={key++} className="italic text-slate-500">{full}</span>);
    } else if (str) {
      nodes.push(<span key={key++} className="text-emerald-400">{full}</span>);
    } else if (num) {
      nodes.push(<span key={key++} className="text-amber-400">{full}</span>);
    } else if (ident && KEYWORDS.has(ident)) {
      nodes.push(<span key={key++} className="text-sky-400">{full}</span>);
    } else {
      nodes.push(full);
    }
    last = m.index + full.length;
  }
  if (last < code.length) nodes.push(code.slice(last));
  return nodes;
}
