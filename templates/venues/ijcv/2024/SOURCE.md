# International Journal of Computer Vision (IJCV) — Springer Nature journal template (sn-jnl)

- Field: Computer vision (journal)
- CCF rank: A (人工智能, journal) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2024 (Springer Nature 'December 2024 version', still the current download)
- Status: vendored (sn-jnl is not on CTAN / in TeX Live)

## Source

- Official: https://cms-resources.apps.public.k8s.springernature.io/springer-cms/rest/v1/content/18782940/data/v12
- Linked as 'Download the journal article template package (December 2024 version)' from https://www.springernature.com/gp/authors/campaigns/latex-author-support
- IJCV guidelines (recommend this template with option `iicol`): https://link.springer.com/journal/11263/submission-guidelines
- Downloaded: 2026-11-18

| Archive | SHA-256 |
|---|---|
| `sn-article-template.zip` | `812e76dcaa9c28dc1bff1fb6065d51729b67d4ea140552a05088317414a3ecae` |

Files from the archive that are **not** copied here (rendered PDFs, Word files, OS junk); every other file is byte-identical to the archive:

- `sn-article.pdf` (rendered example)
- `user-manual.pdf` (manual)

## License / redistribution

`sn-jnl.cls` carries the LaTeX Project Public License 1.3c header (redistribution permitted). Other files have no statement.

## Submission rules (main paper)

- Page limit: No page limit stated in the IJCV submission guidelines; abstract 150–250 words, 4–6 keywords.
- Anonymity: Single-blind: the title page must carry author names and affiliations (IJCV submission guidelines). Submit the LaTeX source (style files, figures) plus the PDF.

## Class / options

`\documentclass[pdflatex,sn-mathphys-num,iicol]{sn-jnl}` (IJCV asks for `[iicol]`, two columns). The `.bst` files sit in `bst/`; copy the chosen one next to the `.tex` or set `BSTINPUTS=./bst:`.

## Build

- Expected engine: pdflatex
- Main file: `sn-article.tex`
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - TeX Live 2026 pdflatex: **FAIL as shipped** — BibTeX cannot find `bst/sn-mathphys-num.bst` (EPS figures are converted automatically under `-shell-restricted`); **OK** (12 pages) with `BSTINPUTS=./bst:` or after copying the chosen `.bst` next to the `.tex`
  - Tectonic: **FAIL** — EPS images not supported

## Notes

- Files come from the archive's `sn-article-template/` folder (path prefix stripped).

## Files

| File | Bytes | SHA-256 |
|---|---|---|
| `bst/sn-apacite.bst` | 151345 | `be9346857b116ec0359319ff26ace70da573475e4c92c6fb9418f841ff7a7c75` |
| `bst/sn-aps.bst` | 29828 | `51464202b6682152fcd80583e0898c18b6abc5e8486ec98a9cbabea2004de15d` |
| `bst/sn-basic.bst` | 35515 | `4b368414cc5593169907933b417aacfdb0ce905866a39bdf55d21aad65e9d46c` |
| `bst/sn-chicago.bst` | 33968 | `079647ab15887274acccfc3bd15b3e9928dd16adde8cfc95cf5f1bc5c98b7ba3` |
| `bst/sn-mathphys-ay.bst` | 64023 | `6a2306683997db76b5e58190acea4dac360c742044114f6a923a4380fbab9008` |
| `bst/sn-mathphys-num.bst` | 64164 | `b3a7c7fbcc1e7f9619de634fcea2f5bb7a245818a5942bc576e40ea001633332` |
| `bst/sn-nature.bst` | 39056 | `638d68f5b0e92ffb25d64d29c7334e1bac6bbf54574b602bc633af8d96e1fd9b` |
| `bst/sn-vancouver-ay.bst` | 39951 | `8f4603d045e2e24c304acc9aa9f4389dc0877811c24ff74d62de1a7dad3ebb63` |
| `bst/sn-vancouver-num.bst` | 40758 | `7fa6df12ec8e8945c86958a50fb703d181522745adcf72bf95a86f3633540e8a` |
| `empty.eps` | 2890 | `aca23a40a6a8e1bf92b417dbf15ebdceaefd2e4066e8ec98747232237facaa04` |
| `fig.eps` | 91593 | `000216a239b64210b463bb888e4be0a233410eccb24e7e4692de4f2ba940ee03` |
| `sn-article.tex` | 34686 | `71777936dca4952ac34aef4b98f5ce543fe50597efb2c1798cbb7a97767b70a9` |
| `sn-bibliography.bib` | 5123 | `007a3f0da8a727b721cf43d4b5bc399831c13f5c8bb3a0728a729c67ab1c0c54` |
| `sn-jnl.cls` | 55857 | `36d0c3273a59d48dc6a9c7b080dfa1ec50dc10229d8751568d1f2e490ffa5ecc` |
