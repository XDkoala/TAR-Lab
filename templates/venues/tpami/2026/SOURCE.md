# IEEE Transactions on Pattern Analysis and Machine Intelligence (TPAMI)

- Field: Computer vision / ML (journal)
- CCF rank: A (人工智能, journal) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2026 (IEEEtran V1.8b, 2015/08/26, current)
- Status: in TeX Live (no files copied)

## Source

- Class: TeX Live `IEEEtran.cls` V1.8b (`texmf-dist/tex/latex/ieeetran/`)
- Official example: `bare_jrnl_compsoc.tex` from CTAN https://ctan.org/pkg/ieeetran (fetched from https://mirrors.ctan.org/macros/latex/contrib/IEEEtran/bare_jrnl_compsoc.tex, SHA-256 61414ad995e5dfc9294f967122796eb1973101cde74891c2d6ce0c2d43c84f43)
- IEEE Author Center templates: https://journals.ieeeauthorcenter.ieee.org/create-your-ieee-journal-article/authoring-tools-and-templates/tools-for-ieee-authors/ieee-article-templates/
- Journal author info: https://www.computer.org/csdl/journal/tp/write-for-us/15083
- Downloaded: 2026-11-18

## License / redistribution

LPPL 1.3 (IEEEtran on CTAN: https://ctan.org/pkg/ieeetran). Not copied: it ships with TeX Live.

## Submission rules (main paper)

- Page limit: Regular papers 12 double-column pages in the IEEE CS template, including references and reasonably sized figures (up to 18 pages with mandatory over-length page charges); short 8, survey 20 (TPAMI Information for Authors, https://doi.org/10.1109/TPAMI.2021.3089310; computer.org author page).
- Anonymity: Single-blind (reviewers know the authors), per TPAMI Information for Authors.

## Class / options

In TeX Live: `\documentclass[10pt,journal,compsoc]{IEEEtran}` (IEEE Computer Society journal style); bibliography `IEEEtran.bst`.

## Build

- Expected engine: pdflatex
- Main file: (none vendored; see example link)
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - `bare_jrnl_compsoc.tex` with TeX Live 2026 pdflatex: **OK** (1 page)
  - Tectonic: OK (1 page)
