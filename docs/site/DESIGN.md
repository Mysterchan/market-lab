# Market Lab visual direction

Keep the existing `m/` logo and blue identity. The website, reading pages and Desktop
share the semantic values in `sim_platform/design.py`; they need different compositions.
Light is the initial appearance. Respect an explicitly selected dark website theme.

## Product homepage

The user's approved visual baseline is the broad two-column introduction with the
large “Give your agent a market.” heading, explanatory copy on the right, and a full
Desktop capture in a blue presentation field below. Preserve its composition, copy,
logo and reading density during visual refinements. The compact finance-desk variant
was rejected: do not recreate it or add technical tables to convey a visual mood.

The requested tech/finance feeling comes from art direction: cool ambient light on
the canvas, deep blue reflective material, fine lit edges, controlled elevation and
translucent navigation/playback surfaces. Use the existing Manrope family and shared
palette. Keep normal reading text at 17px. Preserve contrast over the decorative
backgrounds. No gradient text, invented market metrics, fake tickers or stock imagery.

The blue presentation field has a restrained pointer-responsive light reflection,
updated once per animation frame only for fine pointers with motion enabled. Touch
and reduced-motion users receive the complete static design. Hover affordances and
button feedback should be subtle; the screenshot itself stays stable and readable.

The real renderer screenshot remains labelled as synthetic demonstration data. Link
the capture to the single existing tutorial. This iteration refines website surfaces;
it does not change Desktop behavior or introduce additional content/verification panels.

## Documentation

Keep guide navigation, comfortable prose measure, search and source references.
Use Manrope headings, consistent link/focus states and a translucent header.
Avoid enlarging operational labels or applying the homepage display scale to guides.

## Desktop representative screen

The native App is an operating surface. Do not add promotional banners or slogans.
Use clearly grouped setup panels, field-specific guidance and a persistent action shelf.
Time and number fields allow direct input without stepper buttons; show units and
formats where relevant, and reveal custom time only when selected. Keep progress and
failure feedback in operational status or alongside the related action.
Preserve validation, preparation, worker ownership and all experiment semantics.
Retain Qt for this iteration. Judge the rendered screen before proposing a renderer
migration. This does not constitute a complete Desktop redesign.

## Authoring and review

Tailwind utilities own responsive homepage layout; `presentation.css` owns its visual
components. `site.css` owns shared chrome and Markdown reading styles. Do not copy
whole palettes into either stylesheet. CSS is compiled locally and checked in; Node
is an authoring dependency, not required to run Desktop or build the Python-only site.

Review the real render at desktop, tablet and phone widths, plus light/dark, navigation,
video, focus, search and reduced-motion behavior. Tests of layout source alone do not
prove visual quality. Impeccable's published mode, typography and craft guidance informed
this direction; its launcher/detector was not installed or run.
