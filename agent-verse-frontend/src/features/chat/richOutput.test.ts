/** Phase 7 — rich-output segmentation for assistant messages. */
import { describe, it, expect } from 'vitest';
import { parseRichSegments, isMarkdownTable, parseMarkdownTable } from './richOutput';

describe('parseRichSegments', () => {
  it('promotes a pure markdown table to a table segment', () => {
    const segs = parseRichSegments('| Name | Age |\n|------|-----|\n| Ada | 36 |\n| Bob | 41 |');
    expect(segs).toHaveLength(1);
    expect(segs[0].kind).toBe('table');
    if (segs[0].kind === 'table') {
      expect(segs[0].rows).toEqual([
        { Name: 'Ada', Age: '36' },
        { Name: 'Bob', Age: '41' },
      ]);
    }
  });

  it('promotes a pure JSON array of objects to a table segment', () => {
    const segs = parseRichSegments('[{"a":1,"b":2},{"a":3,"b":4}]');
    expect(segs).toHaveLength(1);
    expect(segs[0].kind).toBe('table');
    if (segs[0].kind === 'table') expect(segs[0].rows).toHaveLength(2);
  });

  it('parses a ```chart fenced block into a chart segment', () => {
    const segs = parseRichSegments(
      'Sales below:\n```chart\n{"title":"Q1","data":[{"label":"Jan","value":10},{"label":"Feb","value":20}]}\n```',
    );
    const chart = segs.find((s) => s.kind === 'chart');
    expect(chart).toBeDefined();
    if (chart && chart.kind === 'chart') {
      expect(chart.title).toBe('Q1');
      expect(chart.data).toEqual([
        { label: 'Jan', value: 10 },
        { label: 'Feb', value: 20 },
      ]);
    }
    // Surrounding prose is preserved as a markdown segment.
    expect(segs.some((s) => s.kind === 'markdown')).toBe(true);
  });

  it('parses a ```image fenced block into an image segment', () => {
    const segs = parseRichSegments('```image\nhttps://example.com/pic.png\n```');
    expect(segs[0].kind).toBe('image');
    if (segs[0].kind === 'image') expect(segs[0].src).toBe('https://example.com/pic.png');
  });

  it('promotes a lone markdown image to an image segment', () => {
    const segs = parseRichSegments('![a cat](https://example.com/cat.png)');
    expect(segs[0].kind).toBe('image');
    if (segs[0].kind === 'image') {
      expect(segs[0].src).toBe('https://example.com/cat.png');
      expect(segs[0].alt).toBe('a cat');
    }
  });

  it('keeps mixed prose (with an inline table and a js fence) as one markdown segment', () => {
    const segs = parseRichSegments(
      'Here is **bold**.\n\n```js\nconst x = 1;\n```\n\n| A | B |\n|---|---|\n| 1 | 2 |',
    );
    expect(segs).toHaveLength(1);
    expect(segs[0].kind).toBe('markdown');
  });

  it('returns a markdown segment for plain prose', () => {
    const segs = parseRichSegments('Just a sentence.');
    expect(segs).toEqual([{ kind: 'markdown', content: 'Just a sentence.' }]);
  });
});

describe('markdown table helpers', () => {
  it('detects a valid markdown table', () => {
    expect(isMarkdownTable('| a | b |\n|---|---|\n| 1 | 2 |')).toBe(true);
    expect(isMarkdownTable('not a table')).toBe(false);
    expect(isMarkdownTable('| a | b |\n| 1 | 2 |')).toBe(false); // no separator row
  });

  it('parses rows keyed by header', () => {
    expect(parseMarkdownTable('| x | y |\n|---|---|\n| 5 | 6 |')).toEqual([{ x: '5', y: '6' }]);
  });
});

describe('humanizeJsonObject / bare-JSON rendering', () => {
  it('renders {success,reason} as a friendly line, not raw JSON', () => {
    const segs = parseRichSegments('{"success": true, "reason": "Goal achieved"}');
    expect(segs).toEqual([{ kind: 'markdown', content: '✅ Goal achieved' }]);
  });
  it('renders {steps:[...]} as a checklist', () => {
    const segs = parseRichSegments('{"steps": ["Complete the requested task"]}');
    expect(segs[0]).toEqual({ kind: 'markdown', content: "Here's the plan:\n- Complete the requested task" });
  });
  it('pretty-prints an unknown JSON object instead of an inline blob', () => {
    const segs = parseRichSegments('{"weird": 1}');
    expect(segs[0].kind).toBe('markdown');
    expect((segs[0] as { content: string }).content).toContain('```json');
  });
  it('leaves normal prose untouched', () => {
    const segs = parseRichSegments('Here is a normal answer.');
    expect(segs).toEqual([{ kind: 'markdown', content: 'Here is a normal answer.' }]);
  });
});
