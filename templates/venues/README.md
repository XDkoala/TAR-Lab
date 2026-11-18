# Official venue templates

Unmodified LaTeX templates from each venue's own site (or the publisher's author kit / CTAN), so a
paper can start offline from the right template. Each `<venue>/<year>/SOURCE.md` records the official
URL, download date (2026-11-18), SHA-256 of the archive and of every file, the license, the page limit
and anonymity rules (with links to the call for papers), the expected engine, and the compile check.

To start a paper: copy the folder to `paper/`, rename or `\input` the example as `main.tex`, and
keep the venue's style files unchanged. Read the folder's SOURCE.md first: some official examples
need a small fix in *your copy* before they compile (marked "FAIL as shipped" below).

CCF ranks are from the CCF 7th edition list (2026), 中国计算机学会推荐国际学术会议和期刊目录,
announced 2026-03-31 (https://www.ccf.org.cn/Academic_Evaluation/By_category/). The 7th edition
moved ICLR to A and IJCAI to B; the per-category pages on ccf.org.cn still show the 2022 lists.

Status: **vendored** = official files copied here; **TeX Live** = the class ships with TeX Live
(`.tools/texlive/2026`), only SOURCE.md is kept, with the class options and the official example link.
No template forbids redistribution, so there are no fetch-only (`fetch.sh`) entries.

Compile check: the official example built in a /tmp copy with
`env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`, using TeX Live 2026
(latexmk + pdflatex, `-shell-restricted`) and, separately, Tectonic.

| Venue | Field | CCF | Year | Folder | Status | Main-paper page limit | Double-blind | Engine | TeX Live 2026 | Tectonic |
|---|---|---|---|---|---|---|---|---|---|---|
| NeurIPS | ML | A | 2026 | `neurips/2026` | vendored | 9 content pages + refs/appendix/checklist | yes | pdflatex | OK | OK |
| ICML | ML | A | 2027 | `icml/2027` | vendored | 8 pages + refs/appendix | yes | pdflatex | OK | OK |
| ICLR | ML | A | 2027 | `iclr/2027` | vendored | 9 pages + refs/appendix (10 at rebuttal/camera-ready) | yes | pdflatex | OK | OK |
| AAAI | AI/ML | A | 2027 | `aaai/2027` | vendored | 7 content pages + 2 pages refs only | yes | pdflatex (required) | OK | FAIL (needs pdfTeX) |
| IJCAI | AI | B | 2027 | `ijcai/2027` | vendored | 7 pages + 2 pages refs | yes | pdflatex | OK | FAIL (`\pdfinfo`) |
| CVPR | CV | A | 2027 | `cvpr/2027` | vendored | 8 pages + refs + LLM Usage Statement | yes | pdflatex | OK | OK |
| ICCV | CV | A | 2025 | `iccv/2025` | vendored | 8 pages + refs | yes | pdflatex | OK | OK |
| ECCV | CV | B | 2026 | `eccv/2026` | vendored (LNCS-based) | 14 pages + refs | yes | pdflatex | OK | FAIL (`\pdfcompresslevel`) |
| CoRL | Robot learning | not listed | 2026 | `corl/2026` | vendored | 8 pages + ack/refs/appendix (9 camera-ready) | yes | pdflatex | OK | OK |
| RSS | Robotics | not listed | 2027 | `rss/2027` | vendored | Stage 1: 6 pages (5+1 refs); Stage 2: 8 pages + refs | yes | pdflatex | OK | FAIL (`\pdfinfo`) |
| ICRA | Robotics | B | 2027 | `icra/2027` | vendored (PaperCept `ieeeconf`) | 8 pages incl. refs | yes (double-anonymous) | pdflatex | FAIL as shipped (non-UTF-8 `root.tex`); OK with `\UseRawInputEncoding` | OK |
| IROS | Robotics | C | 2026 | `iros/2026` | vendored (PaperCept `ieeeconf`) | 8 pages incl. refs | yes (double-anonymous) | pdflatex | as ICRA | OK |
| RA-L | Robotics (journal) | not listed | 2026 | `ral/2026` | vendored (`ieeeconf` + RA-L final example) | 6 pages + up to 2 paid (8 max), all inclusive | yes (double-anonymous) | pdflatex | `root.tex` as ICRA; `RA-L-Template-YY.tex` FAIL as shipped (encoding + `\footnotesizel` typo), OK after fixing both | `root.tex` OK; final FAIL (typo) |
| TPAMI | CV/ML (journal) | A | 2026 | `tpami/2026` | TeX Live (`IEEEtran`, compsoc) | 12 pages (18 with over-length charges) | no (single-blind) | pdflatex | OK | OK |
| TNNLS | ML (journal) | B | 2026 | `tnnls/2026` | TeX Live (`IEEEtran`) | 10 pages free, 15 max with charges | no (single-anonymous) | pdflatex | OK | OK |
| T-RO | Robotics (journal) | B (as TR) | 2026 | `tro/2026` | TeX Live (`IEEEtran`) | 12 pages, all inclusive | yes (double-anonymous) | pdflatex | OK | OK |
| IJCV | CV (journal) | A | 2024 | `ijcv/2024` | vendored (Springer Nature `sn-jnl`, not in TeX Live) | none stated | no (single-blind) | pdflatex | FAIL as shipped (`.bst` in `bst/`); OK with `BSTINPUTS=./bst:` | FAIL (EPS) |
| ACM MM | Multimedia | A | 2026 | `acm-mm/2026` | TeX Live (`acmart` v2.21) | 8 pages + 2 pages refs | yes | pdflatex (ACM also suggests LuaLaTeX) | OK | FAIL |
| Springer LNCS | generic proceedings | n/a | 2026 | `springer-lncs/2026` | vendored (TeX Live's `llncs` is older than Springer's kit) | per conference | per conference | pdflatex | OK | FAIL (EPS) |
| Elsevier `elsarticle` | generic journals | n/a | 2026 | `elsevier-elsarticle/2026` | TeX Live (`elsarticle` v3.5) | per journal | per journal | pdflatex | OK | OK |

"Not listed" means the venue is absent from the CCF 7th edition list. Years are the newest released
edition as of 2026-11-18: NeurIPS 2027, ICCV 2027, IROS 2027 and ACM MM 2027 had not published kits
or calls yet.

Missing venue? Download the template from the venue's own call-for-papers page (never a third-party
copy), add `<venue>/<year>/` with the unmodified files and a SOURCE.md in the same format, and record
the URL and SHA-256.
