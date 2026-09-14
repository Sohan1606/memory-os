# Design notes

## Direction

A near-black instrument panel, not a SaaS landing page. The product is about
recall you can interrogate, so the interface leans on evidence — scores,
reasons, versions, audit events — rather than decoration.

Explicitly avoided: rainbow gradients, generic AI purple, neon glassmorphism,
card-grid templates, stock photography, invented testimonials or metrics, emoji
as UI.

## Palette

| Token | Value | Use |
|-------|-------|-----|
| `--black` | `#050506` | Page ground |
| `--graphite-900/800/700/600` | `#0a0a0d` → `#202028` | Panels, elevation |
| `--warm` | `#f4f1ea` | Primary text |
| `--silver` | `#a8a8b3` | Body copy |
| `--muted` | `#6c6c79` | Labels, metadata |
| `--accent` | `#6ee7d7` | The single accent — live data, active state, recall |

One accent, used to mean something: it marks what the system actually knows.

## Type

A system font stack (`ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto…`)
with a monospace face for data. No webfonts, so there is no network dependency
and no layout shift.

Scale: `.display` `clamp(2.5rem, 8.2vw, 7.5rem)` at weight 200 with tight
tracking; `.headline`; `.subhead`; `.body`; `.mono`/`.label` for instrumentation.

## Motion

Everything eases on `cubic-bezier(0.22, 1, 0.36, 1)`. Sections reveal once via
`IntersectionObserver` and then stop observing. Motion signals state change —
retrieval stages lighting up, a superseded memory striking through — rather than
decorating idle content.

`prefers-reduced-motion` is honoured throughout: the scroll sequence collapses to
a single static frame, reveals resolve immediately, and the canvas backdrops stop
animating.

## The scroll sequence

Used **exactly once**, in the landing page's second act. 60 procedurally
generated WebP frames (1600×900, 961 KB total) of a memory core sealing and
opening, drawn to an `HTMLCanvasElement` — never `<img>`.

- Preloaded with a visible progress bar before the section becomes interactive
- Sticky across ~400vh; scroll offset maps to frame index
- `requestAnimationFrame` redraws only when the index actually changes
- Cover-fit maths with DPR capped at 2
- `IntersectionObserver` pauses work when off-screen
- Below 768px every second frame is used
- Reduced motion pins frame 34

Frames are generated locally by `scripts/generate-frames.mjs`, so there is no
external image service at build or run time.

## Honesty as an interface principle

The UI reports what is true. It says `KEYWORD FALLBACK` when embeddings fail,
`NOT CONFIGURED` when LangMem is inert, `deterministic` when no tool-calling
model exists, and `NO_STRONG_MATCH` when nothing is relevant. A demo that lies
about its own capabilities teaches the wrong thing about the system.
