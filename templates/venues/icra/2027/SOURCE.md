# ICRA 2027 — IEEE International Conference on Robotics and Automation

- Field: Robotics
- CCF rank: B (人工智能, conference) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2027 (Seoul)
- Status: vendored

## Source

- Official: https://ras.papercept.net/conferences/support/files/ieeeconf.zip
- Linked from https://ras.papercept.net/conferences/support/tex.php, which the ICRA 2027 CFP (https://2027.ieee-icra.org/contribute/call-for-icra-2027-papers-now-accepting-submissions/) points to
- Downloaded: 2026-11-18

| Archive | SHA-256 |
|---|---|
| `ieeeconf.zip` | `11d1051d5fe3dafd1e25bc7a8b66265cbe65dc135e26440cc6661baeeeb90c76` |

Files from the archive that are **not** copied here (rendered PDFs, Word files, OS junk); every other file is byte-identical to the archive:

- `root.pdf` (rendered example)

## License / redistribution

`ieeeconf.cls` states it is distributed under the Perl Artistic License (redistribution permitted). `root.tex` has no license statement; PaperCept/IEEE RAS publish it for authors.

## Submission rules (main paper)

- Page limit: 8 pages for the complete paper including references and acknowledgements at submission; over-length papers are returned without review (ICRA 2027 CFP). Supplementary material must fit within the 8 pages; a video (≤180 s, ≤20 MB) may be attached.
- Anonymity: Double-anonymous per IEEE RAS rules (https://www.ieee-ras.org/publications/rules-for-the-double-anonymous-review-process): no authors or affiliations in the PDF.

## Class / options

`\documentclass[letterpaper, 10 pt, conference]{ieeeconf}` (as in `root.tex`).

## Build

- Expected engine: pdflatex
- Main file: `root.tex`
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - TeX Live 2026 pdflatex: **FAIL as shipped** (non-UTF-8 bytes in `root.tex`); **OK** (3 pages) with `\UseRawInputEncoding` prepended in the /tmp copy
  - Tectonic: OK (3 pages)

## Notes

- `root.tex` is not UTF-8 (Mac Roman curly quotes, `0xD2`/`0xD3`), so current LaTeX stops with 'Invalid UTF-8 byte sequence' at line 61. When starting a paper from it, either add `\UseRawInputEncoding` as the first line or replace the quotes with ASCII ``...''. The vendored file is left unmodified.
- The PaperCept `IEEEtranBST.zip` (2003) is not copied: TeX Live ships a newer `IEEEtran.bst`.

## Files

| File | Bytes | SHA-256 |
|---|---|---|
| `ieeeconf.cls` | 202167 | `4befef671c2a996889d325f5170d3387bf42aac9a37dcaa93724ad49816e4ec2` |
| `root.tex` | 17736 | `a7eba1fe1fcd8c39dcc523fd787dc713a90d6830b37c516a0bdaeaeeeac2408f` |
