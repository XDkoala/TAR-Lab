# NeurIPS 2026 — Conference on Neural Information Processing Systems

- Field: Machine learning
- CCF rank: A (人工智能, conference) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2026 (NeurIPS 2027 kit not released yet: `Formatting_Instructions_For_NeurIPS_2027.zip` returns 404)
- Status: vendored

## Source

- Official: https://media.neurips.cc/Conferences/NeurIPS2026/Formatting_Instructions_For_NeurIPS_2026.zip
- Linked from https://neurips.cc/Conferences/2026/CallForPapers
- Downloaded: 2026-11-18

| Archive | SHA-256 |
|---|---|
| `Formatting_Instructions_For_NeurIPS_2026.zip` | `82473931e3ef710fcd3f4a8cd4119b9de32e56825f90f9e5a6d55f2d01b817d9` |

## License / redistribution

No license or redistribution terms are stated in the kit or on the download page; the venue publishes it publicly for authors to use. Nothing forbids redistribution, so the unmodified files are vendored. Copyright stays with the original authors.

## Submission rules (main paper)

- Page limit: 9 content pages (figures and tables included); references, appendices and the mandatory checklist do not count; +1 page for camera-ready. Only this LaTeX style is accepted (https://neurips.cc/Conferences/2026/MainTrackHandbook).
- Anonymity: Double-blind; submissions must be anonymized, including supplementary material and code links (MainTrackHandbook). Use `\usepackage{neurips_2026}` for submission, `[preprint]` for arXiv, `[final]` for camera-ready.

## Build

- Expected engine: pdflatex
- Main file: `neurips_2026.tex`
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - TeX Live 2026 pdflatex: **OK** (12 pages)
  - Tectonic: OK (12 pages)

## Files

| File | Bytes | SHA-256 |
|---|---|---|
| `checklist.tex` | 26167 | `780ba13c480f652dcc42e69ed61a752ce0ea270f15d332d4a45b059dabad84f6` |
| `neurips_2026.sty` | 13704 | `c3fc2894e83d2517ca18b66741d6c595986d97957dc08ec08bb2125a7ec4555a` |
| `neurips_2026.tex` | 19118 | `cf4cee7991665306d1daaa3985be4feec7f8889d6d072ffa12f99a8e1537d797` |
