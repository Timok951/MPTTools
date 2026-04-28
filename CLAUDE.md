# Design System Integration Rules (Figma MCP)

This document describes how to map Figma designs into the current codebase with minimal drift.

## 1) Design Token Definitions

### Where tokens live
- Primary UI tokens are defined as CSS custom properties in `TIP/inventory/templates/inventory/base.html` under:
  - `:root` (light)
  - `body.theme-contrast`
  - `body.theme-dark`

### Token categories in use
- Colors: `--bg`, `--panel`, `--surface*`, `--ink`, `--muted`, `--accent*`, `--warn`, `--border`, `--ring`
- Elevation: `--shadow`
- Radii and spacing are mostly inline class-level values (not centralized as variables yet)
- Typography is global (Google Fonts + size/weight on components)

### Transformation system
- No build-time token transformation pipeline (no Style Dictionary/Tailwind config).
- Tokens are runtime CSS variables in a Django template.

### Pattern example
```css
:root {
  --bg: #f5f7fb;
  --panel: #ffffff;
  --ink: #1f2a44;
  --accent: #4c67ff;
}
body.theme-dark {
  --bg: #0b1020;
  --panel: #141c33;
  --ink: #f3f7ff;
  --accent: #7d96ff;
}
```

---

## 2) Component Library

### Where components are defined
- Server-rendered Django templates in `TIP/inventory/templates/inventory/`.
- Reusable field partial: `TIP/inventory/templates/inventory/_form_field.html`
- Shared layout + component styles: `TIP/inventory/templates/inventory/base.html`

### Component architecture
- Template composition (`{% extends %}` + `{% include %}`).
- Utility-like semantic classes (e.g., `.card`, `.btn`, `.badge`, `.kpi-card`, `.filters`, `.form-grid`).
- No React/Vue component runtime.

### Documentation/storybook
- No Storybook/docs site detected.
- Component behavior is implicit in templates and CSS blocks.

---

## 3) Frameworks & Libraries

### UI framework
- Django Templates (server-side rendering), not SPA.

### Styling stack
- Plain CSS embedded in `base.html`.
- CSS variables for theming.
- Minimal JS for interactions (hotkeys, toggles, form helpers) inline in templates.

### Build system/bundler
- None for frontend assets (no `package.json` found).
- Python/Django app with Docker orchestration.

---

## 4) Asset Management

### Storage and references
- Uploaded media via Django `MEDIA_ROOT`:
  - `TIP/TIP/settings.py`: `MEDIA_URL = 'media/'`, `MEDIA_ROOT = BASE_DIR / 'media'`
- Image fields are in Django models (e.g., equipment/request photos).

### Optimization
- Basic browser-level optimization only (e.g., `loading="lazy"` in templates).
- No explicit image pipeline (no webpack/image minification found).

### CDN
- No CDN configuration found.

---

## 5) Icon System

### Where icons are stored
- No dedicated icon package/library.
- Uses text glyphs/emoji-like markers and initials in UI (`✏️`, `🗑`, `EQ`, `RQ`, etc.).

### Import/use pattern
- Inline text/icons directly in templates.

### Naming convention
- No formal icon naming system.

---

## 6) Styling Approach

### Methodology
- Global CSS in a single shared template (`base.html`), class-based styling.
- Theme switching through body class and CSS variable overrides.

### Global styles
- Entire design system baseline is defined in `base.html`.
- All pages inherit by extending `inventory/base.html`.

### Responsive design
- Media queries in `base.html`:
  - breakpoints around `1080px`, `960px`, `720px`, `520px`

### Pattern example
```html
{% extends "inventory/base.html" %}
<div class="card">
  <div class="kpi-grid">
    <article class="kpi-card">...</article>
  </div>
</div>
```

---

## 7) Project Structure

### High-level organization
- `TIP/core` — shared domain entities and preferences
- `TIP/assets` — equipment/checkouts/adjustments domain
- `TIP/operations` — requests/usage domain
- `TIP/inventory` — app UI, forms, views, portal, API
- `TIP/audit` — audit/event logging
- `TIP/TIP` — Django project config (settings, URLs)

### Feature organization pattern
- Per-feature split: `models.py`, `views.py`, `forms.py`, `templates/`, `api/`.
- Admin portal has dedicated templates/views/forms under `inventory/portal_*`.

---

## Figma-to-Code Rules (Operational)

1. **Always map Figma colors to existing CSS variables first**; only add new vars if strictly needed.
2. **Prefer existing primitives** (`.card`, `.btn`, `.badge`, `.kpi-card`, `.form-grid`) before creating new classes.
3. **Keep theme parity**: any light-theme visual change must include matching `body.theme-dark` updates.
4. **No absolute positioning from Figma** unless unavoidable; convert to grid/flex flow.
5. **Keep templates server-renderable**; avoid introducing SPA-only patterns.
6. **For new UI blocks**, add styles in `base.html` and consume via semantic classes in page templates.
7. **Accessibility baseline**: maintain visible focus states and sufficient contrast in dark mode.

---

## Suitability vs Provided Figma References

- Current UI is now **directionally compatible** with dashboard kits (card-based layout, KPI blocks, neutral background, blue accents).
- Major differences from the references are mostly structural:
  - no persistent left sidebar shell
  - limited iconography system
  - global CSS monolith (instead of token/component package)

Recommended incremental alignment:
- Add optional sidebar layout shell in `base.html` for desktop dashboards.
- Introduce an icon set (e.g., Heroicons SVG sprite) and replace emoji/action glyphs.
- Split CSS into themed sections/tokens/components for maintainability.

