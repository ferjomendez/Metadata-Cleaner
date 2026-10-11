# Design brief

Visual language for the Metadata Cleaner web interface. Appearance only: behavior,
copy, accessibility attributes and the CSP are unchanged.

## Inventory (before the redesign)

- Entry: `frontend/src/main.tsx` imports three fontsource packages and one stylesheet,
  `frontend/src/styles/app.css` (about 1400 lines, plain CSS, custom properties as tokens).
- Pages: `Login`, `Clean` (dropzone, file list, mode selector, per-format warnings, start),
  `JobView` (summary, progress, countdown, downloads, result cards with `ReportPanel`),
  `History`, `Account`, `Admin` (health stats, quota meter, user table, create and reset forms).
- Components: `Dropzone`, `StatusBadge`, `Notice`, `Countdown`, `ReportPanel`, `ThemeToggle`
  (system, light, dark), `Icons` (inline SVG, 1.8 stroke).
- Previous look: warm paper light theme, Fraunces serif headings, pill buttons, 14 px radius
  cards with soft shadows, uppercase mono labels, redaction bar motif.
- Constraints: `style-src 'self'` and `font-src 'self'`, so no inline `style` attributes
  (the quota meter uses width classes) and no external fonts or images.

## Direction

An operations console for a privacy tool: tinted dark neutrals, one muted green used only
for primary actions, focus, selection and progress, thin borders, small radii, dense aligned
rows. The one signature element is the redaction bar, kept from the previous design because it
is literally what the product does: it appears in the logomark and over removed values in the
report. Everything else stays quiet.

What was rejected and why: neon green on black, glows and gradients (generic "security AI"
look); uppercase tracked mono labels above every block (template chrome); identical rounded
cards with drop shadows (SaaS kit). Mono is reserved for real data, not labels.

## Color

All ratios measured with the WCAG 2.x formula. Text needs 4.5:1, UI boundaries 3:1.

| Token | Dark (default) | Light | Use |
|---|---|---|---|
| `--bg` | `#0E1311` | `#F4F6F5` | Page |
| `--surface` | `#141A17` | `#FFFFFF` | Panels |
| `--raised` | `#1A221E` | `#EDF1EF` | Rows, inputs, table head |
| `--border` | `#26302B` | `#D6DED9` | Panel and row dividers (decorative) |
| `--control` | `#64736B` | `#7D8B83` | Input, checkbox and secondary button borders |
| `--text` | `#E6ECE8` | `#131C17` | Primary text |
| `--text-2` | `#9AA8A0` | `#4A5A51` | Secondary text |
| `--text-3` | `#859189` | `#5C6B63` | Hints, placeholders |
| `--accent` | `#3FA877` | `#22744E` | Primary button fill, progress, selection |
| `--accent-hover` | `#52B887` | `#1B6142` | Hover |
| `--accent-press` | `#2F8F63` | `#155036` | Pressed |
| `--on-accent` | `#06120C` | `#FFFFFF` | Text on accent |
| `--accent-text` | `#5CC492` | `#1F6C48` | Links, focus ring |
| `--warn` / soft | `#E0A84A` / `#2A2214` | `#8A5300` / `#FBF0DC` | Deep mode risk, not verified, locked |
| `--danger` / soft | `#EE8070` / `#2D1A17` | `#B3261E` / `#FBE7E4` | Errors, rejected, delete |
| `--info` / soft | `#9DB2C2` / `#18212A` | `#3D5568` / `#E6EDF2` | Neutral information |

Key measurements (dark / light):

- Text on surface 14.73 / 17.41. Secondary text on surface 7.13 / 7.31. Hint text on raised 4.96 / 4.93.
- Text on primary button 6.44 / 5.71 (hover 7.79 / 7.41, pressed 4.76 / 9.41).
- Links and focus ring on page 8.72 / 5.87.
- Control borders on surface 3.54 / 3.56, on raised 3.26 / 3.13.
- Amber on surface 8.29 / 6.33. Red on surface 6.69 / 6.54. Blue-grey on surface 8.05 / 7.78.

Status is never color only: every badge and notice carries an icon and a text label.

## Type

- UI: Instrument Sans Variable (SIL OFL 1.1). Narrow, technical proportions that suit dense rows.
- Data: JetBrains Mono Variable (SIL OFL 1.1) for sizes, formats, metadata keys, timestamps,
  countdown, tool versions. Tabular numerals everywhere numbers align.
- Both are already bundled from the existing `@fontsource-variable` dependencies and served by
  the app (`font-src 'self'`). The Fraunces import is removed; the package stays installed.
- Scale (px): 12, 13, 14, 16, 20, 28. Body 15 on tables and rows, 16 on forms and prose.
  Weights 400, 500, 600. Headings sentence case, letter-spacing -0.01em on 20 and 28 only.

## Space, shape, motion

- 4 px grid: 4, 8, 12, 16, 24, 32, 48.
- Radius: 4 px for badges, tags and inputs; 6 px for buttons and rows; 8 px for panels.
- Borders 1 px. No drop shadows except the sticky header hairline.
- Controls 40 px tall on desktop, 44 px minimum on touch (`pointer: coarse`).
- Motion: 120 ms color and border transitions, progress width, a busy-dot pulse.
  All disabled under `prefers-reduced-motion`.

## Components

- Header: logomark (three bars, one redacted) plus wordmark, tab-style nav with a 2 px green
  underline for the current page, theme toggle.
- Panels: surface, 1 px border, 8 px radius, a header row with title and optional count.
- File list and results: table-like rows with name, mono size, format tag, status badge and
  actions on one line at desktop; stacked at phone widths. Upload progress is a 2 px bar
  under the row.
- Mode selector: two side-by-side options. Conservative shows a check; Deep carries an amber
  alert icon and an amber left rule so its risk reads before it is selected. When Deep is
  selected, the per-format consequences panel switches to an amber border.
- Notices: tinted background, 3 px left rule in the semantic color, icon, text.
- Admin health: a grid of cells sharing borders, labels in sans, values in mono.
