/** Minimal markdown → HTML converter */
export function markdownToHtml(content: string): string {
  if (!content) return '';

  // Step 1: extract fenced code blocks to protect them
  const codeBlocks: string[] = [];
  let result = content.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
    codeBlocks.push(`<pre><code>${code}</code></pre>`);
    return `\x00CODE${codeBlocks.length - 1}\x00`;
  });

  // Step 2: extract markdown tables before line-level transforms
  result = result.replace(
    /(^(\|.+)\n)(^\|[\s:|-]+\n)((?:^\|.+\n?)+)/gm,
    (_, headerLine, _h, _sep, bodyBlock) => {
      const parseRow = (row: string) => {
        const trimmed = row.trim();
        const inner = trimmed.startsWith('|') ? trimmed.slice(1) : trimmed;
        const stripped = inner.endsWith('|') ? inner.slice(0, -1) : inner;
        return stripped.split('|').map(c => c.trim());
      };
      const headers = parseRow(headerLine);
      const bodyRows = bodyBlock.trim().split('\n').map(parseRow);
      let table = '<table><thead><tr>';
      for (const h of headers) table += `<th>${h}</th>`;
      table += '</tr></thead><tbody>';
      for (const row of bodyRows) {
        table += '<tr>';
        for (const cell of row) table += `<td>${cell}</td>`;
        table += '</tr>';
      }
      table += '</tbody></table>';
      return table;
    }
  );

  // Step 3: inline & block transforms
  result = result
    .replace(/^##### (.+)$/gm, '<h6>$1</h6>')
    .replace(/^#### (.+)$/gm, '<h5>$1</h5>')
    .replace(/^### (.+)$/gm, '<h4>$1</h4>')
    .replace(/^## (.+)$/gm, '<h3>$1</h3>')
    .replace(/^# (.+)$/gm, '<h2>$1</h2>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>')
    .replace(/\n\n/g, '<br/>')
    .replace(/\n/g, '<br/>');

  // Step 4: restore code blocks
  result = result.replace(/\x00CODE(\d+)\x00/g, (_, i) => codeBlocks[parseInt(i)]);

  return result;
}
