# CoRL 2026 — Conference on Robot Learning

- Field: Robot learning / embodied AI
- CCF rank: not in CCF list (7th edition) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2026
- Status: vendored

## Source

- Official (Google Drive link given by the venue): https://drive.google.com/file/d/1R9irIW0ImqeDHh5g8Ukm2XGy91sWxOsQ/view (direct: https://drive.google.com/uc?export=download&id=1R9irIW0ImqeDHh5g8Ukm2XGy91sWxOsQ, file `corl_2026_template_submission.zip`)
- Linked from https://www.corl.org/contributions/instruction-for-authors
- Downloaded: 2026-11-18

| Archive | SHA-256 |
|---|---|
| `corl_2026_template_submission.zip` | `bd9dc4b55e1bf9c34db44e7e313d1b1dad10e5563c3422442a75e4dd2978a95c` |

Files from the archive that are **not** copied here (rendered PDFs, Word files, OS junk); every other file is byte-identical to the archive:

- `__MACOSX/` and `.DS_Store` (macOS metadata)

## License / redistribution

No license or redistribution terms are stated in the kit or on the download page; the venue publishes it publicly for authors to use. Nothing forbids redistribution, so the unmodified files are vendored. Copyright stays with the original authors. Proceedings appear in PMLR.

## Submission rules (main paper)

- Page limit: 8 pages for the main paper; acknowledgments, references and optional appendix do not count; camera-ready gets a 9th page (https://www.corl.org/contributions/instruction-for-authors).
- Anonymity: Double-blind; all papers must be anonymized; accepted papers and reviews are made public (instruction-for-authors). Camera-ready uses `\usepackage[final]{corl_2026}`.

## Build

- Expected engine: pdflatex
- Main file: `example.tex`
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - TeX Live 2026 pdflatex: **OK** (3 pages)
  - Tectonic: OK (3 pages)

## Notes

- Files come from the archive's `corl_2026_template_submission/` folder (path prefix stripped).

## Files

| File | Bytes | SHA-256 |
|---|---|---|
| `corl_2026.sty` | 14861 | `62f38cd8df7ad718796617595c8230da136fc83ea1d7e1ba80084113409ec957` |
| `corlabbrvnat.bst` | 26694 | `eb5c200b6c5a81cd774155c79c28d3a32ac8a5e25bb246a40dda74deb94dfa71` |
| `example.bib` | 620 | `e23beeadef75cdad1019c3bfe1469e4ea69112b28b19fae104b0e4f21ee33851` |
| `example.tex` | 7653 | `cd593743dfe04e9f0f954e20d0f06a66fc2c5848436bf99aca9b285b73f84724` |
