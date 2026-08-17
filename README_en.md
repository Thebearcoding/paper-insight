<div align='center'>

<img src="./images/head.png" alt="Paper Insight research workflow" width="92%">
<h1><a href="https://paper.athebear.me">Paper Insight</a></h1>
<p><strong>Find the papers worth reading before you go deep.</strong></p>
<p>Built for a research rhythm of at least five papers a day: conference browsing, arXiv analysis, paper chat, and a personal reading library.</p>

<p>
  <a href="https://paper.athebear.me">Live demo</a> ·
  <a href="./develop.md">Developer guide</a> ·
  <a href="./README.md">简体中文</a>
</p>

</div>

## Introduction

Paper Insight is a fast paper-screening and analysis tool for AI conference papers. It uses LLMs to generate concise paper summaries, helping you decide whether a paper is worth reading in depth before saving it to Zotero or continuing with a deeper review.

If the goal is to read at least five papers a day, the bottleneck is often not opening PDFs; it is quickly deciding which candidates deserve real attention. Paper Insight turns that judgment step into a stable, reusable research entrypoint.

No great paper should have its close reading replaced by AI; researchers still need to understand its details and subtleties themselves. Paper Insight makes the initial screening step faster, so users can more efficiently find candidates worth reading in depth from a large volume of papers.

By default, each analysis focuses on four screening questions:

- Is the code open-sourced?
- What task does the paper solve?
- What evaluation metrics does it use?
- Why is it better than the baseline?

## Project Background

This personally maintained fork combines paper collection, conference browsing, LLM-assisted screening, full-text chat, and Zotero management in one self-hosted research workspace. It reduces repetitive organization work by sending conference papers, Hugging Face Daily Papers, and newly discovered arXiv papers through the same reading flow.

The current version is maintained by [Athebear](https://github.com/Thebearcoding) and continuously deployed on a personal server. It builds on the upstream Paper Insight project and adds personal deployment tooling, a Zotero library, API search, and additional conference importers.

## Current Entrypoints

| Entrypoint | What it is for |
| --- | --- |
| [ICLR 2026](https://paper.athebear.me/conference/iclr_2026) / [CHI 2026](https://paper.athebear.me/conference/chi_2026) / [CVPR 2026](https://paper.athebear.me/conference/cvpr_2026) / [NeurIPS 2025](https://paper.athebear.me/conference/neurips_2025) / [ICML 2025](https://paper.athebear.me/conference/icml_2025) | Browse conference papers with pagination, keyword search, and field filters |
| [Hugging Face Daily Papers](https://paper.athebear.me/hf-daily) | Sync popular Daily Papers and send them through the same analysis flow |
| [arXiv analysis](https://paper.athebear.me/arxiv) | Paste an arXiv link or ID to add a new paper to the analysis and reading flow |

## Core Capabilities

- Quick analysis: summarizes code, task, metrics, and baseline-oriented evidence for faster screening.
- Research workspace: brings conference papers, Daily Papers, and arXiv into one flow.
- Paper chat: ask multi-turn questions based on paper content, with saved chat history.
- Personal paper library: track viewed and liked papers for later review.
- Accounts and admin: supports GitHub login, user management, online metrics, and manual sync jobs.

## Difference From cool papers

[cool papers](https://papers.cool/) is an excellent paper-reading tool. The positioning is different:

| Dimension | Paper Insight | cool papers |
| --- | --- | --- |
| Positioning | Quick paper screening | Deep paper understanding |
| Use case | Decide whether a paper is worth reading | Understand one paper in depth |
| Core output | Code, task, metrics, baseline-oriented screening | Problem, method, experiments, background, future directions |
| Extra capabilities | Conference browsing, search, paper chat, personal records | Deep paper interpretation |

In short, Paper Insight helps you quickly find candidate papers from a large pool; cool papers helps you deeply understand a specific paper.

## Suggested Flow

1. Start from a conference page, Hugging Face Daily Papers, or an arXiv link.
2. Read the four screening answers first to decide whether the paper matches your current direction.
3. For promising papers, ask follow-up questions about method details, experiments, related work, and possible reproduction paths.
4. Mark the papers worth reading, then move them into Zotero or your local close-reading workflow.

Paper Insight is not meant to read the paper for you; it is meant to make the daily screening rhythm easier to keep.

## Development

If you only want to try the product, use the online version.

For local development or self-hosting, see [develop.md](./develop.md). It covers PostgreSQL, `config.yaml`, GitHub OAuth, data import, and Docker/VPS deployment.

## License

Apache 2.0 License

## Maintenance

- Maintainer: [Athebear](https://github.com/Thebearcoding)
- Repository: [Thebearcoding/paper-insight](https://github.com/Thebearcoding/paper-insight)
- Live service: [paper.athebear.me](https://paper.athebear.me)
- Feedback: [GitHub Issues](https://github.com/Thebearcoding/paper-insight/issues)

This repository preserves the upstream project's commit history and Apache 2.0 license information. Athebear maintains the personal deployment and extensions in this branch.

## Acknowledgements

Thanks to the upstream Paper Insight project and its contributors. The upstream project acknowledged [StepFun](https://www.stepfun.com/) for token support.
