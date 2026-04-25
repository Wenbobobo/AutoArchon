## 2024-04-25 - DiffPlayback Icon Button Accessibility
**Learning:** Found that the `DiffPlayback` view utilized purely text-based symbol icons (`◀` and `▶`) for navigating between diff steps without any accessible labels. These symbols are often announced poorly or inconsistently by screen readers.
**Action:** Always ensure that icon-only or symbol-only buttons include descriptive `aria-label`s and `title` tooltips to improve both screen reader support and general usability through tooltips.
