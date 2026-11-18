# ICCV 2025 — IEEE/CVF International Conference on Computer Vision

- Field: Computer vision
- CCF rank: A (人工智能, conference) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2025 (ICCV is biennial; no ICCV 2027 author guidelines yet — https://iccv.thecvf.com/Conferences/2027/AuthorGuidelines returns 404)
- Status: vendored

## Source

- Official: https://media.eventhosts.cc/Conferences/ICCV2025/ICCV2025-Author-Kit-Feb.zip
- Linked from https://iccv.thecvf.com/Conferences/2025/AuthorGuidelines
- Downloaded: 2026-11-18

| Archive | SHA-256 |
|---|---|
| `ICCV2025-Author-Kit-Feb.zip` | `22c1bee4cf64c2106b9afaecf18807be9f8350a60576bc4bf4ba14a50ca36d83` |

Files from the archive that are **not** copied here (rendered PDFs, Word files, OS junk); every other file is byte-identical to the archive:

- `__MACOSX/` and `.DS_Store` (macOS metadata)

## License / redistribution

No license or redistribution terms are stated in the kit or on the download page; the venue publishes it publicly for authors to use. Nothing forbids redistribution, so the unmodified files are vendored. Copyright stays with the original authors.

## Submission rules (main paper)

- Page limit: 8 pages including figures and tables; extra pages only for references (https://iccv.thecvf.com/Conferences/2025/AuthorGuidelines).
- Anonymity: Double-blind (AuthorGuidelines). For ICCV 2027 re-check the guidelines and kit once released; CVPR 2027 now also requires an LLM Usage Statement.

## Build

- Expected engine: pdflatex
- Main file: `main.tex`
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - TeX Live 2026 pdflatex: **OK** (5 pages)
  - Tectonic: OK (5 pages)

## Notes

- Files come from the archive's `ICCV2025-Author-Kit-Feb/` folder (path prefix stripped).

## Files

| File | Bytes | SHA-256 |
|---|---|---|
| `README.md` | 1479 | `89b2ac41c39a64c19c592e53cc656ae82803ffe4cc4980fb3cdb402f5e5bc655` |
| `iccv.sty` | 17969 | `d4b522c1b3ecc0029d23f3d057383fd3bc8145c9aebf47f1b7058e91a35e3eff` |
| `ieeenat_fullname.bst` | 26900 | `e38e6166bd7b1e6d23a1b79dcdb55c656e4fcdbe91bdf6b50d827e6b5d1aacfc` |
| `main.bib` | 2971 | `15cfec69b06051537bd2841554f9462d8f8c1e100fb8ed70b8eae02e1bd7c20e` |
| `main.tex` | 1969 | `40f1353ad07decc2b26f110bb2e3af768cb31fcd2033624cdd7be898adda290d` |
| `preamble.tex` | 251 | `75415cdda3738200750a9f348b3dc99f5afd7ebaa96e92e11cd6a55c3db1cf70` |
| `rebuttal.tex` | 6795 | `a6563cb8514200bf38dce994bd26b152efe40f72cc605bcfdb6c952f91314f72` |
| `sec/0_abstract.tex` | 497 | `ac059e9cb8595bab8f7795d088d71e321ee46e8397d5cfb56957107d43c868ed` |
| `sec/1_intro.tex` | 10734 | `cb73ca6af756417dba6502a3ee1e2a2b653cdc923dbfd1c77e6a444675be1084` |
| `sec/2_formatting.tex` | 7620 | `44634ad045b015e73222fb5c92c6649738c537dce827778fa8c2ca260e817d35` |
| `sec/3_finalcopy.tex` | 360 | `ae261b3daaff50cf609bb3ca7dfd030bb1e835fd85aa4fda45cdaf53cc35cc1a` |
