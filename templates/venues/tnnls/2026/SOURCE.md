# IEEE Transactions on Neural Networks and Learning Systems (TNNLS)

- Field: Machine learning (journal)
- CCF rank: B (人工智能, journal) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2026 (IEEEtran V1.8b)
- Status: in TeX Live (no files copied)

## Source

- Class: TeX Live `IEEEtran.cls` V1.8b
- Official example: `bare_jrnl.tex` from CTAN https://ctan.org/pkg/ieeetran (SHA-256 7a5e5dfdb7c17d6dcc827d1a985245b873e7e903d9d66882392c47bee4be47ed)
- IEEE Author Center templates (see TPAMI)
- Journal author info: https://cis.ieee.org/publications/t-neural-networks-and-learning-systems/tnnls
- Downloaded: 2026-11-18

## License / redistribution

LPPL 1.3 (IEEEtran on CTAN: https://ctan.org/pkg/ieeetran). Not copied: it ships with TeX Live.

## Submission rules (main paper)

- Page limit: Full papers: over-length charges beyond 10 pages, at most 15 pages; surveys 15/21; brief papers 6/9 (https://cis.ieee.org/publications/t-neural-networks-and-learning-systems/tnnls-page-charges).
- Anonymity: Single-anonymous (reviewers know the authors), per TNNLS Information for Authors (https://doi.org/10.1109/TNNLS.2024.3383617).

## Class / options

In TeX Live: `\documentclass[journal]{IEEEtran}`; bibliography `IEEEtran.bst`.

## Build

- Expected engine: pdflatex
- Main file: (none vendored; see example link)
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - `bare_jrnl.tex` with TeX Live 2026 pdflatex: **OK** (1 page)
  - Tectonic: OK (1 page)
