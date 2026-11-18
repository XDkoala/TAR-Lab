# ECCV 2026 — European Conference on Computer Vision

- Field: Computer vision
- CCF rank: B (人工智能, conference) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2026 (biennial; next is 2028)
- Status: vendored

## Source

- Official: https://github.com/paolo-favaro/paper-template/archive/refs/tags/Latest.zip (tag `Latest` = commit da8c09c40239d5665757527e77388f4716a6564a)
- Linked from https://eccv.ecva.net/Conferences/2026/SubmissionPolicies
- Downloaded: 2026-11-18

| Archive | SHA-256 |
|---|---|
| `paper-template-Latest.zip` | `8ed161c418f690cc4f48f7c9f57f78b630c2d615d74ebfb3bff37699bd6e4fa1` |

Files from the archive that are **not** copied here (rendered PDFs, Word files, OS junk); every other file is byte-identical to the archive:

- `ECCV_2026_Paper_Template.pdf` (rendered example)
- `llncsdoc.pdf` (LNCS class documentation)

## License / redistribution

MIT License (`LICENSE` in the kit, also reported by GitHub). The kit bundles Springer's `llncs.cls` v2.22 and `splncs04.bst`.

## Submission rules (main paper)

- Page limit: 14 pages in Springer LNCS style including figures and tables; extra pages only for references (https://eccv.ecva.net/Conferences/2026/SubmissionPolicies). Do not change the template's font.
- Anonymity: Double-blind (SubmissionPolicies). Review mode is the default in `main.tex`; see its `TODO REVIEW` / `TODO FINAL` comments.

## Build

- Expected engine: pdflatex (README: pdflatex + bibtex, or latex + dvips)
- Main file: `main.tex`
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - TeX Live 2026 pdflatex: **OK** (15 pages, i.e. the instructions document itself)
  - Tectonic: **FAIL** — `axessibility.sty` uses `\pdfcompresslevel`

## Notes

- Files come from the archive's `paper-template-Latest/` folder (path prefix stripped).
- The tag `Latest` moves; the SHA-256 above pins the 2026 version.

## Files

| File | Bytes | SHA-256 |
|---|---|---|
| `LICENSE` | 1063 | `82d8789febf72672fc2c6ce0b3f4b6ae17bad4b7c195b436179d8b71529c81a6` |
| `README.md` | 1083 | `2892b0207b3c6bb4ebd45efbe75cb25da74f9163871b76add5d1e05870654491` |
| `eccv.sty` | 9117 | `07785f87dd4741a761e5064d6752e59dbca308b7999de35df983e70dc0a33d80` |
| `eccvabbrv.sty` | 1343 | `1c9aba814d428d8d90bfdfde53e3ad8edc9f1db0e5f12e052f713fd5b38e483f` |
| `eijkel2.eps` | 13532 | `ea0550c153a8d17c6f601b4768b0d35a1f02d99c2cb82c70b0a7cb8b3daa7feb` |
| `eijkel2.pdf` | 5863 | `c42356b1c36d49a336f10828fd547d77040df67ef649a915a8a93f8d479abc9d` |
| `llncs.cls` | 42312 | `8152fe7afa929cbd3f3679d6f7c1d562525414ed9c5940a69e86cd923416cb2b` |
| `lncs_history.txt` | 7789 | `d4779d25219ce1d022e45df45ba19e4d31a43314af4cf6099003e9794120ba00` |
| `lncs_readme.txt` | 575 | `7dbe59c722450e662ff2ffa248fb4338ea3bbcdaabc3ad6d648be7054aa062d1` |
| `main.bib` | 3711 | `b0109230a442139b4d0914ae07f0a33b9b86c4277190962216f756a267a2f6ca` |
| `main.tex` | 26184 | `ac91a7eb6d0991301bca2b1879177495f176dfc7737c77f5d1079852f33fd7e9` |
| `splncs04.bst` | 32146 | `564cb6156e2400e3755a58bed9caabccb3067dd37a48efcb66851a56d4475c4c` |
