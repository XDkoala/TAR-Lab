# IEEE Robotics and Automation Letters (RA-L)

- Field: Robotics (journal)
- CCF rank: not in CCF list (7th edition) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2026 (rolling submissions; files as currently linked)
- Status: vendored

## Source

- Initial submission kit (official): https://ras.papercept.net/conferences/support/files/ieeeconf.zip
- Final-version example source (official, Dropbox link from IEEE RAS): https://www.dropbox.com/s/quyayglnzjgifi5/RA-L-Template-YY.tex?dl=0
- Both linked from https://www.ieee-ras.org/publications/ra-l/information-for-authors-ra-l
- Downloaded: 2026-11-18

| Archive | SHA-256 |
|---|---|
| `ieeeconf.zip` | `11d1051d5fe3dafd1e25bc7a8b66265cbe65dc135e26440cc6661baeeeb90c76` |
| `RA-L-Template-YY.tex` | `c6b21ea4c20c75b4495c67850e1ea71b30b27343aa1f2e4ce9ed0f06557f557e` |

Files from the archive that are **not** copied here (rendered PDFs, Word files, OS junk); every other file is byte-identical to the archive:

- `root.pdf` (rendered example)

## License / redistribution

`ieeeconf.cls` states it is distributed under the Perl Artistic License (redistribution permitted). `root.tex` has no license statement; PaperCept/IEEE RAS publish it for authors. `RA-L-Template-YY.tex` is derived from Michael Shell's `bare_jrnl.tex` (LPPL legal notice in its header).

## Submission rules (main paper)

- Page limit: 6 pages in two-column IEEE format including figures, tables, references and appendices, plus at most 2 extra pages at a charge ($175/page), i.e. 8 maximum; no supplementary text beyond that (RA-L Information for Authors).
- Anonymity: Double-anonymous (RA-L Information for Authors, 'Peer Review Model'): leave space for a byline but no author information.

## Class / options

Initial (and revised) submission: `root.tex` with `\documentclass[letterpaper, 10 pt, conference]{ieeeconf}`. Final accepted version: TeX Live's `IEEEtran` with `\documentclass[letterpaper, 10 pt, journal, twoside]{IEEEtran}` following `RA-L-Template-YY.tex` (headers via `\markboth`, `IEEEkeywords`, no `\overrideIEEEmargins`).

## Build

- Expected engine: pdflatex
- Main file: `root.tex (submission); RA-L-Template-YY.tex (final)`
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - `root.tex`: as for ICRA — **FAIL as shipped** (Mac Roman quotes), **OK** with `\UseRawInputEncoding`; Tectonic OK
  - `RA-L-Template-YY.tex`, TeX Live 2026 pdflatex: **FAIL as shipped** — non-UTF-8 bytes (line 449) and a typo `\footnotesizel` (line 389) in the official file; **OK** (4 pages) with `\UseRawInputEncoding` and `\footnotesize` in the /tmp copy
  - `RA-L-Template-YY.tex`, Tectonic: **FAIL** (same typo)

## Notes

- `root.tex` is not UTF-8 (Mac Roman curly quotes, `0xD2`/`0xD3`), so current LaTeX stops with 'Invalid UTF-8 byte sequence' at line 61. When starting a paper from it, either add `\UseRawInputEncoding` as the first line or replace the quotes with ASCII ``...''. The vendored file is left unmodified.
- `RA-L-Template-YY.tex` has CRLF line endings and the `\footnotesizel` typo; fix both in your copy, not here.

## Files

| File | Bytes | SHA-256 |
|---|---|---|
| `RA-L-Template-YY.tex` | 48656 | `c6b21ea4c20c75b4495c67850e1ea71b30b27343aa1f2e4ce9ed0f06557f557e` |
| `ieeeconf.cls` | 202167 | `4befef671c2a996889d325f5170d3387bf42aac9a37dcaa93724ad49816e4ec2` |
| `root.tex` | 17736 | `a7eba1fe1fcd8c39dcc523fd787dc713a90d6830b37c516a0bdaeaeeeac2408f` |
