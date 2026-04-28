# web/AGENTS.md

Frontend conventions and the **67quant** branding system.
See repo-root [`AGENTS.md`](../AGENTS.md) for brand rationale and the
hard rules around the `67` reference.

## Stack

- **Next.js 16** (App Router, static export — see `next.config.ts`)
- **React 19**
- **Tailwind v4** — theme defined via `@theme` in
  [`src/app/globals.css`](./src/app/globals.css) (no `tailwind.config.*`)
- **TanStack Query** for server state (read-only)
- **Recharts** for charts
- **Geist Sans / Geist Mono** loaded in [`src/app/layout.tsx`](./src/app/layout.tsx)

The frontend is statically exported and served by FastAPI in production
(single container). Treat it as a read-only dashboard with no server
mutations beyond auth.

## Branding system

### Wordmark

The wordmark is the digits **6 7** in heavy mono with a two-bar wobble
motif between them — geometrically restating the alternating-palms
hand gesture. The wobble *is* the brand; do not redesign it.

- Component: [`src/components/wordmark.tsx`](./src/components/wordmark.tsx)
- Sizes: `sm` (sidebar / footer), `md` (login / app header), `lg`
  (sub-hero), `xl` (landing hero only)
- Bars use `currentColor`, so wrap the wordmark in any text-color
  context and it picks it up. The xl wordmark on `/welcome` uses
  `--color-positive` (drill-green); everywhere else it uses default
  body text color.
- Animation respects `prefers-reduced-motion`.

### Static brand assets

These live in `public/` and are referenced from
[`src/app/layout.tsx`](./src/app/layout.tsx) via `Metadata.icons` /
`Metadata.openGraph`. The wobble doesn't survive at icon size and
doesn't run on home-screen icons or scraped OG cards, so all three use
a **frozen pose** of the gesture (left hand up, right hand down) with
hand path data preserved verbatim from `wordmark.tsx`.

- [`public/favicon.svg`](./public/favicon.svg) — 32×32 monogram, frozen pose.
- [`public/apple-touch-icon.png`](./public/apple-touch-icon.png) — 180×180,
  near-black field, wordmark centered. SVG source at
  [`public/brand/apple-touch-icon.svg`](./public/brand/apple-touch-icon.svg).
- [`public/og.png`](./public/og.png) — 1200×630 default OG / Twitter card,
  wordmark + tagline + bot color row. SVG source at
  [`public/brand/og.svg`](./public/brand/og.svg).

When regenerating PNGs from the SVG sources on macOS:
`sips -s format png -z <h> <w> <in.svg> --out <out.png>` (note: sips
takes height, then width).

### Voice

Deadpan-technical, with at most **one** tongue-in-cheek line per page.
We sound like a Bloomberg terminal that dropped out of grad school.

- Real numbers, real backtests, real Sharpe ratios.
- One ironic moment per page is the budget. Currently in good standing:
  the `/welcome` footer (`markets are 6-7. so is everything else.`).
- No exclamation marks. No "supercharged", no "AI-powered", no
  "revolutionary". We describe the math.
- Numbers always use `font-mono` and tabular figures (the `.num` class
  in `globals.css`).

### Palette

Source of truth: [`src/app/globals.css`](./src/app/globals.css)
(`@theme` block). Surfaces and semantics:

| Token                    | Usage                                          |
| ------------------------ | ---------------------------------------------- |
| `--color-bg`             | page background (near-black)                   |
| `--color-surface` / `-2` | cards, sidebar, raised surfaces                |
| `--color-border` / `-strong` | dividers, input borders                    |
| `--color-text` / `-muted` / `-subtle` | body / secondary / tertiary type   |
| `--color-accent`         | primary action (the only "fintech" blue)       |
| `--color-positive` / `-negative` | **P/L semantics only** — never decorative |
| `--color-warn` / `-info` | system/status indicators                       |
| `--color-momentum`, `--color-mean_reversion`, `--color-congress`, `--color-sentiment`, `--color-xs_momentum` | per-bot identity colors; assign with `botColorVar(b.id)` |

Do not introduce new colors without updating this table.

### Routing surfaces

| Route                | Auth      | Purpose                                          |
| -------------------- | --------- | ------------------------------------------------ |
| `/welcome`           | public    | brand + marketing copy. The only marketing surface. |
| `/login`             | public    | password gate. Wordmark only, no marketing prose. |
| `/(app)/*`           | gated     | dashboard. No brand prose, only product UI.      |

App-shell routes (sidebar + topbar + auth gate) live in
`src/app/(app)/*`. Public routes are siblings: `src/app/login`,
`src/app/welcome`. They share `layout.tsx` only.

To add a public route: `src/app/<name>/page.tsx`.
To add a gated route: `src/app/(app)/<name>/page.tsx`.

## Don'ts

- Don't add a second logo. The wordmark is the logo.
- Don't put bot lists, charts, or any product chrome on `/welcome` —
  that page is for people who don't have access yet.
- Don't pull `recharts` or `@tanstack/react-query` into `/welcome`.
  Keep it light; it's the page that loads first for new visitors.
- Don't render the brand as a plain `<h1>67quant</h1>`. Use the
  `<Wordmark>` component (it sets `aria-label="67quant"` for
  accessibility).
- Don't reach for `--color-positive` or `--color-negative` for
  decoration — they're reserved for P/L semantics so `green = up` and
  `red = down` stay true throughout the dashboard.
