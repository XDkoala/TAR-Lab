# IJCAI 2027 — International Joint Conference on Artificial Intelligence

- Field: Artificial intelligence
- CCF rank: B (人工智能, conference; downgraded from A in the 7th edition) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2027
- Status: vendored

## Source

- Official: https://www.ijcai.org/sites/default/files/Guidelines/FormattingGuidelines-IJCAI-27.zip
- Linked from https://www.ijcai.org/authors_kit (referenced by https://2027.ijcai.org/ijcai-2027-call-for-papers-main-track/)
- Downloaded: 2026-11-18

| Archive | SHA-256 |
|---|---|
| `FormattingGuidelines-IJCAI-27.zip` | `1f829e86070353eb72220864632c4e6c5a6dd160c35380e68de14a242078b663` |

Files from the archive that are **not** copied here (rendered PDFs, Word files, OS junk); every other file is byte-identical to the archive:

- `ijcai27.docx` (Word template)

## License / redistribution

No license or redistribution terms are stated in the kit or on the download page; the venue publishes it publicly for authors to use. Nothing forbids redistribution, so the unmodified files are vendored. Copyright stays with the original authors.

## Submission rules (main paper)

- Page limit: At most 9 pages: 7 pages of body (figures and tables included) + up to 2 pages of references; the optional ethics statement may go in either (https://2027.ijcai.org/ijcai-2027-call-for-papers-main-track/).
- Anonymity: Double-blind; no author names or affiliations; anonymize supplementary material and PDF metadata; no acknowledgements at submission (main-track CFP).

## Build

- Expected engine: pdflatex (uses `\pdfinfo`)
- Main file: `ijcai27.tex`
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - TeX Live 2026 pdflatex: **OK** (6 pages)
  - Tectonic: **FAIL** — `\pdfinfo` undefined under XeTeX

## Files

| File | Bytes | SHA-256 |
|---|---|---|
| `ijcai27.bib` | 2536 | `b47c5115eb344dbb5b11707b662817c444b9098a50b6b438578790788b2f9e5c` |
| `ijcai27.sty` | 13104 | `cdf7ca9a28c6b1ba60cde0f42019ccea3722838242b69471db572d21f879d3b2` |
| `ijcai27.tex` | 30268 | `e55c680ffad8213b91da9091a5238e8e0281cc019b7f7b29654776f14d85ab05` |
| `named.bst` | 22941 | `72c53a0febca9745a9a6febdc1e49327d2b6ac4f161e1d7a210dd47e5f5dda40` |
