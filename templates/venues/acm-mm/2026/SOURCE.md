# ACM Multimedia 2026 — ACM International Conference on Multimedia (ACM `acmart`)

- Field: Multimedia / CV
- CCF rank: A (计算机图形学与多媒体, conference) — source: CCF 7th edition (2026) 中国计算机学会推荐国际学术会议和期刊目录, announced 2026-03-31 at https://www.ccf.org.cn/Academic_Evaluation/By_category/ (full PDF list as re-hosted by SJTU: https://icisee.sjtu.edu.cn/upload/ueditor/file/20260914/11781106/ ; ccf.org.cn serves the PDF behind a captcha)
- Edition / year: 2026 (ACM MM 2027 site has no CFP yet); acmart v2.21 (2026/11/01)
- Status: in TeX Live (no files copied)

## Source

- Class: TeX Live `acmart.cls` v2.21 (`texmf-dist/tex/latex/acmart/`), matching ACM's 'LaTeX (Version 2.21)' on https://www.acm.org/publications/proceedings-template
- Official example: `sample-sigconf-authordraft.tex`, generated from CTAN acmart `samples/samples.dtx` + `samples.ins` (https://ctan.org/pkg/acmart; samples.dtx SHA-256 22bcbe6255bba5dd1b5e8227d618180098cf7bf5935623e820ba784aac8e71fe). ACM's own zip https://portalparts.acm.org/hippo/latex_templates/acmart-primary.zip returned HTTP 403 to scripted downloads.
- Venue rules: https://2026.acmmm.org/site/author-instructions.html and https://2026.acmmm.org/site/cfp-guidelines.html
- Downloaded: 2026-11-18

## License / redistribution

acmart is LPPL 1.3c (CTAN). Not copied: ships with TeX Live.

## Submission rules (main paper)

- Page limit: Main technical track: up to 8 pages + 2 additional pages for references (https://2026.acmmm.org/site/author-instructions.html).
- Anonymity: Double-blind for the main track (CFP; OpenReview). Use `\documentclass[sigconf, screen, review, anonymous]{acmart}` for submission (Author Instructions); add CCS concepts and keywords.

## Class / options

Submission: `\documentclass[sigconf,screen,review,anonymous]{acmart}`; camera-ready: `\documentclass[sigconf]{acmart}`; bibliography `ACM-Reference-Format.bst`.

## Build

- Expected engine: pdflatex (ACM now also recommends LuaLaTeX for accessible PDFs: https://authors.acm.org/proceedings/production-information/preparing-your-article-with-latex)
- Main file: (none vendored; see example link)
- Compile check (a /tmp copy, `env -u PYTHONPATH .venv/bin/python -m tar_lab.cli tex build <copy> --main <file>`):
  - `sample-sigconf-authordraft.tex` (+ `sample-base.bib`, `sampleteaser.pdf`, `sample-franklin.png` from CTAN) with TeX Live 2026 pdflatex: **OK** (7 pages), also with the `[sigconf,screen,review,anonymous]` options
  - Tectonic: **FAIL** (acmart needs a newer LaTeX kernel than Tectonic's bundle)
