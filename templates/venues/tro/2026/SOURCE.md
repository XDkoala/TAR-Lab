# IEEE Transactions on Robotics (T-RO)

- Field: Robotics (journal)
- CCF rank: B (交叉/综合/新兴, journal; listed as TR) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2026 (IEEEtran V1.8b)
- Status: in TeX Live (no files copied)

## Source

- Class: TeX Live `IEEEtran.cls` V1.8b
- Official example: `bare_jrnl.tex` from CTAN https://ctan.org/pkg/ieeetran (SHA-256 7a5e5dfdb7c17d6dcc827d1a985245b873e7e903d9d66882392c47bee4be47ed)
- Journal author info: https://www.ieee-ras.org/publications/t-ro/information-for-authors
- Downloaded: 2026-11-18

## License / redistribution

LPPL 1.3 (IEEEtran on CTAN: https://ctan.org/pkg/ieeetran). Not copied: it ships with TeX Live.

## Submission rules (main paper)

- Page limit: Regular and survey papers at most 12 pages in Transactions format, including figures, tables, references and biographies (T-RO Information for Authors).
- Anonymity: Double-anonymous (T-RO Information for Authors; IEEE RAS rules https://www.ieee-ras.org/publications/rules-for-the-double-anonymous-review-process): no author names in the PDF.

## Class / options

In TeX Live: `\documentclass[journal]{IEEEtran}` at 10 pt (as required by T-RO); abstract ≤200 words.

## Build

- Expected engine: pdflatex
- Main file: (none vendored; see example link)
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - `bare_jrnl.tex` with TeX Live 2026 pdflatex: **OK** (1 page)
  - Tectonic: OK (1 page)
