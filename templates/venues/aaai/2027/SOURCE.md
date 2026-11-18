# AAAI-27 — AAAI Conference on Artificial Intelligence

- Field: Artificial intelligence / ML
- CCF rank: A (人工智能, conference) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2027
- Status: vendored

## Source

- Official: https://aaai.org/authorkit27/ (redirects to https://aaai.org/wp-content/uploads/2026/10/AuthorKit27.zip; the site rejects curl's default user agent, a browser user agent works)
- Linked from https://aaai.org/conference/aaai/aaai-27/submission-instructions/
- Downloaded: 2026-11-18

| Archive | SHA-256 |
|---|---|
| `AuthorKit27.zip` | `9406a545d5d213757245eb37118fe652b2fccf64f555aff28b49ebe2a780c960` |

Files from the archive that are **not** copied here (rendered PDFs, Word files, OS junk); every other file is byte-identical to the archive:

- `Word/` (Word templates and their PDFs)
- `AnonymousSubmission2027.pdf`, `CameraReady2027.pdf`, `ReproducibilityChecklist.pdf` (rendered examples)

## License / redistribution

No license or redistribution terms are stated in the kit or on the download page; the venue publishes it publicly for authors to use. Nothing forbids redistribution, so the unmodified files are vendored. Copyright stays with the original authors. The style prints a CC BY 4.0 notice on camera-ready papers; that concerns the papers, not the kit.

## Submission rules (main paper)

- Page limit: Up to 9 pages, of which pages 8–9 are only for references: at most 7 pages of content; ethics statements must fit in the 7 pages (https://aaai.org/conference/aaai/aaai-27/submission-instructions/).
- Anonymity: Double-blind; remove all author and affiliation information; omit acknowledgements (submission instructions). Start from `AnonymousSubmission2027.tex`; `CameraReady2027.tex` is the camera-ready version.

## Build

- Expected engine: pdflatex (the style aborts with 'pdfTeX is required')
- Main file: `AnonymousSubmission2027.tex`
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - TeX Live 2026 pdflatex: **OK** (10 pages)
  - Tectonic: **FAIL** — `aaai2027.sty` requires pdfTeX

## Notes

- Files come from the archive's `AuthorKit27/` folder (path prefix stripped).

## Files

| File | Bytes | SHA-256 |
|---|---|---|
| `AnonymousSubmission2027.tex` | 59071 | `95a9e0ee566c6f76276c9dcad09a83637542dac168383213252671daec3408ab` |
| `CameraReady2027.tex` | 57156 | `e0b55f09da66a69af9866ec07ce7a05d200880a57a8fa0a8f60db92ffb4d86a1` |
| `Figures/figure1.pdf` | 252557 | `29e16580aae21c9c7785b6851d244a9948c6ccf96e308ee366fa651db22c922b` |
| `Figures/figure2.pdf` | 1099641 | `8899687a74776c5ba3179f630f14a946c36278560a997fbac0cde58cdca1750f` |
| `ReproducibilityChecklist.tex` | 8729 | `06a3459158089bf1c64b738986118f1d1566e816da4b710c6397561e33c3d5e6` |
| `aaai2027.bib` | 4814 | `ff8a860f20602b2645723856b80f0a0ad28ee7ec270158254400fdb80b429e45` |
| `aaai2027.bst` | 30207 | `5db7765ba99de5c1e4686f9b3940a0add9c5e702f2164514462bec130ccb6e3c` |
| `aaai2027.sty` | 20264 | `01990f8d3716f8e39c5b28a7478f3ac6e28630d1161de6105a7217f5e7f60e43` |
