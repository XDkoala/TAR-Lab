# Elsevier journals — `elsarticle`

- Field: Generic journals (e.g. Pattern Recognition, Neural Networks, Artificial Intelligence, CVIU)
- CCF rank: n/a (publisher class; e.g. AI journal is A, Pattern Recognition / Neural Networks / CVIU are B) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2026 (elsarticle v3.5, 2026/01/09)
- Status: in TeX Live (no files copied)

## Source

- Class: TeX Live `elsarticle.cls` v3.5 (`texmf-dist/tex/latex/elsarticle/`)
- Official example: `elsarticle-template-num.tex` from CTAN https://ctan.org/pkg/elsarticle (SHA-256 3b02f9b8c80972bda933f281de3c96bb5147969fcebeb23fd8468ff28e33e15d)
- Elsevier LaTeX instructions: https://www.elsevier.com/researcher/author/policies-and-guidelines/latex-instructions
- Downloaded: 2026-11-18

## License / redistribution

LPPL 1.3 (header of elsarticle files). Not copied: ships with TeX Live.

## Submission rules (main paper)

- Page limit: Journal-specific (see each journal's Guide for Authors).
- Anonymity: Journal-specific (single- or double-anonymous per journal).

## Class / options

`\documentclass[preprint,12pt]{elsarticle}` for submission (template default); `[final,5p,times,twocolumn]` approximates the printed layout; bibliography `elsarticle-num.bst`.

## Build

- Expected engine: pdflatex
- Main file: (none vendored; see example link)
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - `elsarticle-template-num.tex` with TeX Live 2026 pdflatex: **OK** (4 pages)
  - Tectonic: OK (4 pages)
