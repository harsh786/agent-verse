import type { ResultArtifact } from '../resultArtifact';

export function openPrintView(artifact: ResultArtifact, goal: string): void {
  const table = artifact.tables[0];
  const rows = table?.rows ?? [];
  const columns = table?.columns ?? [];

  const tableHtml = table
    ? `
      <table>
        <thead>
          <tr>${columns.map((c) => `<th>${String(c.label)}</th>`).join('')}</tr>
        </thead>
        <tbody>
          ${rows
            .map(
              (row) =>
                `<tr>${columns
                  .map((c) => `<td>${String(row[c.key] ?? '—')}</td>`)
                  .join('')}</tr>`
            )
            .join('')}
        </tbody>
      </table>
    `
    : `<p>${String(artifact.summary)}</p>`;

  const metricsHtml =
    artifact.metrics.length > 0
      ? `<div class="metrics">${artifact.metrics
          .map(
            (m) =>
              `<div class="metric"><span class="label">${String(m.label)}</span><span class="value">${String(m.value)}</span></div>`
          )
          .join('')}</div>`
      : '';

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>${String(artifact.title)}</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: system-ui, -apple-system, sans-serif; font-size: 12px; color: #111; padding: 24px; }
    h1 { font-size: 20px; margin-bottom: 6px; }
    .goal { font-size: 11px; color: #555; margin-bottom: 4px; }
    .summary { font-size: 13px; color: #444; margin-bottom: 16px; }
    .metrics { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
    .metric { border: 1px solid #ddd; border-radius: 6px; padding: 8px 12px; }
    .metric .label { font-size: 10px; color: #666; text-transform: uppercase; display: block; }
    .metric .value { font-size: 18px; font-weight: bold; }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; }
    th { text-align: left; padding: 8px; background: #f5f5f5; border-bottom: 2px solid #ddd; font-size: 11px; text-transform: uppercase; color: #555; }
    td { padding: 8px; border-bottom: 1px solid #eee; }
    @media print { body { padding: 0; } }
  </style>
</head>
<body>
  <h1>${String(artifact.title)}</h1>
  <p class="goal">Goal: ${String(goal)}</p>
  <p class="summary">${String(artifact.summary)}</p>
  ${metricsHtml}
  ${tableHtml}
  <script>window.onload = function() { window.print(); };<\/script>
</body>
</html>`;

  const printWindow = window.open('', '_blank', 'width=900,height=700');
  if (printWindow) {
    printWindow.document.write(html);
    printWindow.document.close();
    printWindow.focus();
  }
}
