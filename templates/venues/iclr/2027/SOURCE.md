# ICLR 2027 — International Conference on Learning Representations

- Field: Machine learning
- CCF rank: A (人工智能, conference; ICLR was added to class A in the 7th edition) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2027
- Status: vendored

## Source

- Official: https://media.iclr.cc/Conferences/ICLR2027/iclr-2027-style-files.zip
- Linked from https://iclr.cc/Conferences/2027/AuthorGuidelines (identical to `iclr2027.zip` in https://github.com/ICLR/Master-Template at commit 46ed6f4c6c)
- Downloaded: 2026-11-18

| Archive | SHA-256 |
|---|---|
| `iclr-2027-style-files.zip` | `0d940dfa9398ae99a18f24a85a8a683f367204b6af6d17d2899e60a67102529e` |

## License / redistribution

No license or redistribution terms are stated in the kit or on the download page; the venue publishes it publicly for authors to use. Nothing forbids redistribution, so the unmodified files are vendored. Copyright stays with the original authors. (the GitHub repository has no LICENSE file.)

## Submission rules (main paper)

- Page limit: Main text at most 9 pages at submission (10 pages during rebuttal and camera-ready); references and appendices unlimited; strictly enforced (https://iclr.cc/Conferences/2027/AuthorGuidelines).
- Anonymity: Double-blind; papers revealing author identity are desk-rejected (AuthorGuidelines).

## Build

- Expected engine: pdflatex
- Main file: `iclr2027_conference.tex`
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - TeX Live 2026 pdflatex: **OK** (7 pages)
  - Tectonic: OK (7 pages)

## Notes

- Files come from the archive's `iclr2027/` folder (path prefix stripped).

## Files

| File | Bytes | SHA-256 |
|---|---|---|
| `fancyhdr.sty` | 20521 | `b56ec4434b9f4607529a4b23dc68ad8d4b94f1f631c8cddaf7da78140d53a5ea` |
| `iclr2027_conference.bib` | 629 | `cdd86e7d4c31854dcf2145871657c944588a6d44c3b72e160ff4baa8df1a52fb` |
| `iclr2027_conference.bst` | 26973 | `2d67552db7ed38ccfccb5957b52f95656e25c249724761d3cf5f7922ad1844c5` |
| `iclr2027_conference.sty` | 9025 | `797deef41724e93761426ac0cbcca46279a91cc650dd1f0ce76a4f08d2098ea6` |
| `iclr2027_conference.tex` | 19740 | `03e556d6e5593e498fd39f262ec2d184ebfe8693cbf47fb7cea3402d8d5166ac` |
| `math_commands.tex` | 12284 | `90473c4d0542070db244cea73ef962d6cddc5b2a746757e6a40ddf5fdfb90ba9` |
| `natbib.sty` | 45154 | `88bc70c0e48461934cab5b2accef06b74a8b3ac45ad03ccd3f2a6b7e0d6d530d` |
