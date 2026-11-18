# RSS 2027 — Robotics: Science and Systems

- Field: Robotics
- CCF rank: not in CCF list (7th edition) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2027 (the RSS template itself dates from 2010/2014 and is unchanged)
- Status: vendored

## Source

- Official: https://roboticsconference.org/docs/paper-template-latex.tar.gz
- Linked from https://roboticsconference.org/information/cfp/ (RSS 2027, Athens)
- Downloaded: 2026-11-18

| Archive | SHA-256 |
|---|---|
| `paper-template-latex.tar.gz` | `ffc8b8053bd218ba9be4c93a569de4c2f36c7f70ae8d2245c747611bed697271` |

Files from the archive that are **not** copied here (rendered PDFs, Word files, OS junk); every other file is byte-identical to the archive:

- `paper_template.pdf` (rendered example)

## License / redistribution

No license or redistribution terms are stated in the kit or on the download page; the venue publishes it publicly for authors to use. Nothing forbids redistribution, so the unmodified files are vendored. Copyright stays with the original authors. The kit bundles an old `IEEEtran.cls` (2010); it is kept because it is part of the venue's kit.

## Submission rules (main paper)

- Page limit: Two-stage process: Stage 1 extended abstract of at most 6 pages (5 content + 1 references); Stage 2 full paper at most 8 pages excluding references (https://roboticsconference.org/information/cfp/).
- Anonymity: Double-blind; no names/affiliations, no acknowledgements, no external links (even anonymous ones discouraged), clean PDF metadata (CFP, 'Author Anonymity').

## Build

- Expected engine: pdflatex
- Main file: `paper_template.tex`
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - TeX Live 2026 pdflatex: **OK** (1 page)
  - Tectonic: **FAIL** — `\pdfinfo` undefined under XeTeX

## Files

| File | Bytes | SHA-256 |
|---|---|---|
| `IEEEtran.cls` | 201353 | `d63d4bf27f29603a733d06e27ba5fbb40488acf9d1d696354a88b886758f70a7` |
| `paper_template.tex` | 5251 | `0d8c15afd40c033d887770460248d5ee1e98486030a140097aaa5aed605da422` |
| `references.bib` | 694 | `a2b6a8558f0f15fa8d3c6895dac2cf8d7982904282e78a9dc1a90f32774caf79` |
