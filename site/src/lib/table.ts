/**
 * Sortable tables (SPEC §9: tables are the primary surface).
 *
 * Sorting reads `data-v` rather than the cell's text, because the text is
 * formatted -- "$84,378" and "—" do not sort numerically, and a missing value
 * must sort to the end rather than to zero, which would put every unknown at
 * the bottom of a "cheapest first" list as though it were the cheapest.
 */

export function sortableTable(table: HTMLTableElement | null): void {
  if (!table) return;
  const headers = Array.from(table.querySelectorAll('th[data-sort]'));

  headers.forEach((header, columnIndex) => {
    header.setAttribute('tabindex', '0');
    header.setAttribute('role', 'button');

    const activate = () => {
      const kind = header.getAttribute('data-sort');
      const ascending = header.getAttribute('aria-sort') !== 'ascending';
      headers.forEach((h) => h.removeAttribute('aria-sort'));
      header.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');

      const body = table.tBodies[0];
      const rows = Array.from(body.rows);
      rows.sort((a, b) => {
        const cellA = a.cells[columnIndex];
        const cellB = b.cells[columnIndex];
        const rawA = cellA?.dataset.v ?? cellA?.textContent ?? '';
        const rawB = cellB?.dataset.v ?? cellB?.textContent ?? '';
        const emptyA = rawA === '' || rawA === '—';
        const emptyB = rawB === '' || rawB === '—';
        // Unknowns always sink, in either direction.
        if (emptyA && emptyB) return 0;
        if (emptyA) return 1;
        if (emptyB) return -1;
        if (kind === 'num') {
          const diff = Number(rawA) - Number(rawB);
          return ascending ? diff : -diff;
        }
        return ascending ? rawA.localeCompare(rawB) : rawB.localeCompare(rawA);
      });
      rows.forEach((row) => body.appendChild(row));
    };

    header.addEventListener('click', activate);
    header.addEventListener('keydown', (event) => {
      if ((event as KeyboardEvent).key === 'Enter' || (event as KeyboardEvent).key === ' ') {
        event.preventDefault(); activate();
      }
    });
  });
}
