# ICML 2027 — International Conference on Machine Learning

- Field: Machine learning
- CCF rank: A (人工智能, conference) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2027
- Status: vendored

## Source

- Official: https://media.icml.cc/Conferences/ICML2027/styles/icml2027.zip
- Linked from https://icml.cc/Conferences/2027/AuthorInstructions
- Downloaded: 2026-11-18

| Archive | SHA-256 |
|---|---|
| `icml2027.zip` | `52384a2a4f32e562755e44ced55a58f857f8706425f2f04904190f613531801e` |

Files from the archive that are **not** copied here (rendered PDFs, Word files, OS junk); every other file is byte-identical to the archive:

- `example_paper.pdf` (rendered example)

## License / redistribution

No license or redistribution terms are stated in the kit or on the download page; the venue publishes it publicly for authors to use. Nothing forbids redistribution, so the unmodified files are vendored. Copyright stays with the original authors. (`fancyhdr.sty`, `algorithm.sty`, `algorithmic.sty` inside the kit carry their own LPPL/GPL headers.)

## Submission rules (main paper)

- Page limit: Main body up to 8 pages; unlimited references and appendices in the same PDF; +1 page for camera-ready. Over 8 pages is rejected automatically (https://icml.cc/Conferences/2027/AuthorInstructions).
- Anonymity: Double-blind; anonymized submission mandatory, no acknowledgements or links to public repos at submission (AuthorInstructions). `\usepackage{icml2027}` for review, `[accepted]` for camera-ready.

## Build

- Expected engine: pdflatex
- Main file: `example_paper.tex`
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - TeX Live 2026 pdflatex: **OK** (7 pages)
  - Tectonic: OK (7 pages)

## Files

| File | Bytes | SHA-256 |
|---|---|---|
| `algorithm.sty` | 2223 | `93fd0eb31c112eb405833db8f1d7f5d238c7e691b1c05680d7276e68f36d564a` |
| `algorithmic.sty` | 7414 | `48d18794a5d97c0479a588cc2eac0917992feb9da83acc4631b8f55757d80f9b` |
| `example_paper.bib` | 2051 | `df950103d38f9cfc81b1f40d84c9be2a3525d046d2991a6973a4446922c06bd1` |
| `example_paper.tex` | 30319 | `209713159d3590e8fc871a9d629916a8efc21b9bdf90f56482b6060653f065e0` |
| `fancyhdr.sty` | 31715 | `9130c52f91087abc6d223164ffa587e207e3257fcbcd069ef09ecb5391043f14` |
| `icml2027.bst` | 27147 | `0ec3d5eb9b02efb7e0b44a32f3775882f42a743d0bdc618f34e6936309b98764` |
| `icml2027.sty` | 27344 | `3a61b88dc041e0f1db75cd8ff0a8e5396979cfb88cbde684333eb1a0ae585e30` |
| `icml_numpapers.pdf` | 2823 | `d34e8da982296363627996e6e18850c11fbd616e6946061237051e7a8f7080bb` |
