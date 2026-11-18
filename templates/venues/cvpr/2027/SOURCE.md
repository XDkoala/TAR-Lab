# CVPR 2027 — IEEE/CVF Conference on Computer Vision and Pattern Recognition

- Field: Computer vision
- CCF rank: A (人工智能, conference) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2027
- Status: vendored

## Source

- Official: https://media.eventhosts.cc/Conferences/CVPR2027/author-kit-CVPR2027-v1-latex.zip
- Linked from https://cvpr.thecvf.com/Conferences/2027/AuthorGuidelines (same as release `CVPR2027-v1` of https://github.com/cvpr-org/author-kit)
- Downloaded: 2026-11-18

| Archive | SHA-256 |
|---|---|
| `author-kit-CVPR2027-v1-latex.zip` | `da1ec5db9b73531ea82a87723e4aedb631083331fe655d1972279b48a7bdb2fe` |

## License / redistribution

No license or redistribution terms are stated in the kit or on the download page; the venue publishes it publicly for authors to use. Nothing forbids redistribution, so the unmodified files are vendored. Copyright stays with the original authors. (the GitHub repository has no LICENSE file.)

## Submission rules (main paper)

- Page limit: 8 pages including figures and tables; extra pages only for references and the mandatory LLM Usage Statement (https://cvpr.thecvf.com/Conferences/2027/AuthorGuidelines).
- Anonymity: Double-blind; non-anonymized papers are rejected without review. A section titled 'LLM Usage Statement' (after the main text, before references) is mandatory. `\usepackage[review]{cvpr}` for submission (see `main.tex`).

## Build

- Expected engine: pdflatex
- Main file: `main.tex`
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - TeX Live 2026 pdflatex: **OK** (5 pages)
  - Tectonic: OK (5 pages)

## Files

| File | Bytes | SHA-256 |
|---|---|---|
| `README.md` | 1695 | `82154abbe11cdbc336bfb9ecc7e6cf9f2be823c4ff18de16235f2cca2561101a` |
| `cvpr.sty` | 17887 | `1cd7f434b13d41c475df4a1901a5d82d9ca8b80b8cf6205dc4602a5c164286b4` |
| `ieeenat_fullname.bst` | 26900 | `e38e6166bd7b1e6d23a1b79dcdb55c656e4fcdbe91bdf6b50d827e6b5d1aacfc` |
| `main.bib` | 2971 | `1d6d78b4c02eae2607a40e15376f6ac96ef491e311241acd6c8a8a970ac3c1b2` |
| `main.tex` | 2030 | `9f6d8ef0ffaeac9494283f563d2666b125af5951f8d9604f7f805dca87460bfe` |
| `preamble.tex` | 1380 | `f666a85903f49929395951e7afb620e96d4e208334623f23135298b4d7f06228` |
| `rebuttal.tex` | 6613 | `e64a94a7a4c41f0758c9309983f195a5b7e85b2f8d73fd436badede9ddf5b292` |
| `sec/0_abstract.tex` | 497 | `ac059e9cb8595bab8f7795d088d71e321ee46e8397d5cfb56957107d43c868ed` |
| `sec/1_intro.tex` | 10424 | `6b7bf826be4e68e92a037d3d238a108ed7e4432204353826d237e00faeecf7a6` |
| `sec/2_formatting.tex` | 7718 | `bd4233b307bfe3ad791c433e021b00ed4e4017db7adde8ee8e475c267fc7191d` |
| `sec/3_finalcopy.tex` | 360 | `ae261b3daaff50cf609bb3ca7dfd030bb1e835fd85aa4fda45cdaf53cc35cc1a` |
| `sec/X_suppl.tex` | 1123 | `594291f94cf3b925908dc127a93f3525c1998e96abf37a14f69d6077a7e58bd7` |
