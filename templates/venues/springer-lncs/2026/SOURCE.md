# Springer LNCS — Lecture Notes in Computer Science proceedings (`llncs`)

- Field: Generic CS proceedings (many conferences/workshops)
- CCF rank: n/a (publisher series; rank depends on the conference) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2026 (Springer kit llncs v2.25, dated 2026-09-03)
- Status: vendored (deviation from the TeX-Live-only rule, see notes)

## Source

- Official: https://cms-resources.apps.public.k8s.springernature.io/springer-cms/rest/v1/content/27851904/data ('LaTeX2e Proceedings Template (ZIP)')
- Linked from https://www.springer.com/gp/computer-science/lncs/conference-proceedings-guidelines
- Downloaded: 2026-11-18

| Archive | SHA-256 |
|---|---|
| `LaTeX2e Proceedings Template.zip` | `42afb32ed4fadc9e209134ec65dab1bd2a7ead42f9a3f8abb184f250c4a197b9` |

Files from the archive that are **not** copied here (rendered PDFs, Word files, OS junk); every other file is byte-identical to the archive:

- `samplepaper.pdf` (rendered example)
- `llncsdoc.pdf` (class documentation)

## License / redistribution

No license text in the kit; Springer distributes it publicly to authors; the class is also on CTAN (https://ctan.org/pkg/llncs, CC BY 4.0 per CTAN).

## Submission rules (main paper)

- Page limit: Set by each conference (LNCS itself has no fixed limit; e.g. ECCV 2026: 14 pages + references).
- Anonymity: Set by each conference.

## Class / options

`\documentclass[runningheads]{llncs}`; bibliography `splncs04.bst`.

## Build

- Expected engine: pdflatex
- Main file: `samplepaper.tex`
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - TeX Live 2026 pdflatex: **OK** (3 pages; `fig1.eps` converted by `repstopdf` under `-shell-restricted`)
  - With TeX Live's own `llncs.cls` (v2.26, 2025-02-25) instead of the kit's: **FAIL** — `\Description` (alt text, required by the 2026 sample) is undefined
  - Tectonic: **FAIL** — EPS images not supported

## Notes

- TeX Live does ship `llncs`, but its copy (v2.26, 2025-02-25) predates Springer's current kit (v2.25, 2026-09-03; Springer's version number went down) and lacks `\Description`, so the official sample does not compile with it. The kit is therefore vendored as downloaded.
- ECCV uses its own bundled copy, `../../eccv/2026/llncs.cls` (v2.22).

## Files

| File | Bytes | SHA-256 |
|---|---|---|
| `DescriptionTexts.txt` | 153 | `31666ccea9072ad9b6af59624f50e239e6d663cb952d894891ed1cff2efb7868` |
| `fig1.eps` | 357553 | `e0da72903f4ba3ae2307869fd2f1d2ad460281725e0a3205862e91c8d6daf831` |
| `history.txt` | 8956 | `9a4a85d0e7ab49fa35c638fee5e4083ae2b5bd0f5a943113098dc2e86e62d261` |
| `llncs.cls` | 43700 | `04ec99046ad9867f6cd54ffd1ef8c47c767b327d1baec77536db0528cd492fa2` |
| `readme.txt` | 573 | `d5a69ddfcd5d316877c301e9504c6302d1f1dc7b2c8f128d8066cdd0b0a7e21a` |
| `samplepaper.tex` | 6748 | `08142e057525c0edcab3e62a7028084a136cdbd7a66af8eb5157c2e5ae1806a6` |
| `splncs04.bst` | 32146 | `f36c3a17e5304a692706359aafa9de709395a085e579eb47c027095aeaa35174` |
