# Web edition of the LxMLS lab guide

`../build_web.py` converts the LaTeX guide (`../guide.tex` and its `pages/`) into
the static website section served at `lxmls-website/guide/`. The generated HTML is
committed to the website repo so GitHub Pages deploys it as-is — re-run the script
whenever the guide changes and commit the result.

## Rebuild

```bash
cd lxmls-guide
python3 build_web.py
```

Output goes to `../lxmls-website/guide/` (`index.html` landing page + `day0..day4.html`,
plus `assets/`). The script wipes and regenerates that directory each run.

## Requirements

- `pandoc` (LaTeX → HTML)
- `pdftocairo` (poppler) — converts `.pdf` figures to SVG
- `inkscape` — converts `.eps` figures to SVG
- `python3`

## How it works

For each of the 5 active Days in `guide.tex`, the script:

1. Flattens `\input` into one document.
2. Rewrites figure paths and converts `.pdf`/`.eps` → `.svg`, copying PNG/SVG as-is
   into `assets/figs/`.
3. Preprocesses custom LaTeX:
   - the `python` listing environment → fenced code (highlighted client-side by
     highlight.js, with a copy button from `copy-button.js`);
   - theorem-like environments (`exercise`, `theorem`, `definition`, `example`,
     `remark`) → numbered styled boxes;
   - `algorithm`/`algorithmic` → pseudocode blocks;
   - editor/margin macros (`\todo`, `\afm`, …) are stripped.
4. Runs `pandoc` to HTML, with all `\newcommand`s prepended so text+math macros expand.
5. Renders math via MathJax (`assets/mathjax/`), with the guide's custom math macros
   (from `math.tex`) injected into the MathJax config.
6. Wraps the result in `template.html` (site navbar/footer + sidebar TOC).

## Vendored libraries

`vendor/` holds the front-end libraries copied into `assets/` at build time
(MathJax `tex-chtml` + fonts, highlight.js + GitHub theme). They are vendored
locally to match the site's no-CDN convention.

## Menu link

`lxmls-website/index.html` and `call-for-monitors.html` link to the guide via a
**Guide** navbar item (`guide/index.html`).

## Known limitations

- Algorithms and complex `multirow` tables are the lowest-fidelity conversions
  (pandoc's weak spots); they use styled fallbacks — spot-check after big edits.
- Cross-references that point to another Day's page degrade to plain text.
