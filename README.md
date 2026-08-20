<div align="center">

<!-- TBD: replace with official logo assets/logo.svg -->
<h1>DashBox</h1>

<p><strong>DashBox 是基于 <a href="https://github.com/dramaclaw/dramaclaw">DramaClaw CE</a>（Elastic License 2.0）的本地化二次开发版本。<br/>
DashBox is a locally customized derivative of <a href="https://github.com/dramaclaw/dramaclaw">DramaClaw CE</a> (Elastic License 2.0).</strong></p>

## Make Your Own DC.

<p align="left">

They say you're obsolete.<br/>
Maybe it's the whole idea of "working for someone else" that's obsolete.<br/>
<br/>
In the age of AI, the real question isn't whether machines replace people.<br/>
The real question is:<br/>
Who owns the machines?<br/>
Who owns the pipeline?<br/>
Who owns industrialized productivity?<br/>
<br/>
If the answer is always Big Tech,<br/>
then AI isn't empowerment.<br/>
It's just a new wall.<br/>
<br/>
I'm Eric.<br/>
<br/>
This isn't a demo.<br/>
Not a toy.<br/>
Not a crippled edition.<br/>
<br/>
This is the industrialized drama-production line our own team runs every day.<br/>
From script to storyboard, from assets to finished film — the whole chain.<br/>
<br/>
Because people aren't beasts of burden.<br/>
Because creativity is humanity's last line of defense.<br/>
<br/>
What DashBox sets out to do is simple:<br/>
<br/>
<strong>Tear down the wall.</strong><br/>
<br/>
Put the industrialized drama-production power that only Big Tech had<br/>
into the hands of ordinary creators.<br/>
<br/>
The code is here.<br/>
If this resonates, leave a ⭐.<br/>
We'll keep tearing down walls.

</p>

<br/>

[![License](https://img.shields.io/badge/License-Elastic_2.0-blue.svg)](./LICENSES/Elastic-2.0.txt)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)](#quick-start)

**English** &nbsp;|&nbsp; [简体中文](./readme/README_zh.md) &nbsp;|&nbsp; [Docs](./docs/en/README.md) &nbsp;|&nbsp; [Quick Start](./docs/en/getting-started/quickstart.md)

</div>

<br/>

<p align="center">
  <img src="./assets/hero.png" alt="DashBox — storytellers, back in front of the camera" width="820"/>
</p>

<br/>

## What is DashBox?

DashBox is an industrialized drama-production line whose **source is available**. Drop in a manuscript and DashBox takes over all the heavy lifting: extracting characters, planning episodes, generating scripts, drawing storyboards and first frames, synthesizing voice-over, and cutting the final film.

It's built for creators, indie studios, and creative engineers — letting you run the whole "drama factory" on your own infrastructure, without stitching together a dozen disconnected tools or handing your material to an opaque black-box cloud service.

And although it's built around drama production, the same pipeline — characters, assets, scripts, storyboards, voice-over, and compositing — carries just as well to other visual-content formats: short-form ads, e-commerce product videos, and interactive otome (romance) games.

<br/>

## Core Capabilities

- **Novel parsing & story graph** &mdash; parse the manuscript into a queryable graph of characters, relationships, and timeline
- **Asset Library & identity consistency** &mdash; unified management of characters, scenes, props and voices; keep stable identities across episodes, generate character portraits and per-episode variants
- **Episode planning & narrative pacing** &mdash; automatic chapter segmentation, beat planning, multi-episode arcs
- **Script generation** &mdash; multiple modes (adaptive, literal, staged) with review / repair loops
- **Storyboards & first frames** &mdash; beat-driven stylized image generation, grid splitting, image-pool selection
- **Voice-over synthesis** &mdash; emotion-aware speech synthesis, switchable across providers
- **Video composition & export** &mdash; assemble episodes, export video + subtitle files and the full asset pack
- **Freezone (infinite canvas)** &mdash; node-based visual workbench: drag in project assets to generate images / video / audio, promote satisfying candidates back to the main line; the main pipeline and canvas exploration run as dual tracks
- **Director World / 3GS (scene variants)** &mdash; a framable virtual set that locks spatial structure, character blocking and camera placement to keep the same location consistent across shots
- **Xia Director (AI assistant)** &mdash; conversational production assistant that checks project progress, advances script / shot tasks, audits deliverable completeness and suggests next steps
- **Visual Style (style templates)** &mdash; upload a reference image to auto-extract style parameters and apply them across the whole project for a consistent look
- **Task Center** &mdash; status, progress, logs and cancel / retry for background generation tasks, with resume-from-checkpoint for long runs

<br/>

## Pipeline at a Glance

<p align="center">
  <img src="./assets/pipeline.png" alt="DashBox pipeline — Ingest, Plan, Produce, Deliver" width="900"/>
</p>

Every step has its own interface — run them in order, skip steps, resume from any checkpoint, or even plug in your own orchestrator.

<br/>

## System Requirements

DashBox runs all inference through a **remote OpenAI-compatible gateway** — nothing runs models on your machine — so the local footprint is light. An ordinary laptop or a small VPS is enough.

| Item | Requirement |
|---|---|
| **CPU / RAM** | ≥ 2 vCPU / 4 GB recommended (excludes model inference — that runs on the gateway) |
| **GPU** | Not required for the standard pipeline. Only the optional `world` extra (voxel / panorama-to-3D) needs a GPU + CUDA image |
| **Disk** | A few GB for images plus generated media/state under the `ce-data` volume (no hard minimum) |
| **OS** | macOS (Apple Silicon / Intel), Windows (Docker Desktop + WSL2 backend), Linux (Docker Engine + compose plugin) |
| **Docker** | Docker + `docker compose` |
| **Ports** | `8080` web UI · `8780` REST API · `3000` bundled gateway (self-hosted variant only) |
| **Datastores** | None required — no Postgres, Redis, Celery or Ray. Tasks run in-process; state lives on the local filesystem (SQLite + files) |
| **Network** | Outbound access to the model gateway (official `relayclaw.cdnfg.com`, or your own BYO endpoint) |

> Local development (non-Docker) additionally needs Python 3.11–3.12 + [`uv`](https://docs.astral.sh/uv/) + `ffmpeg`. Full prerequisites in the [Self-hosting guide](docs/en/guides/self-hosting.md).

<br/>

## <a name="quick-start"></a>Quick Start

### Docker

```bash
cp .env.example .env
# Edit .env — set PROMPT_EXPORT_PASSWORD to a non-default value,
# and point NEWAPI_BASE_URL at your OpenAI-compatible gateway.

docker compose up -d --build   # starts two services: api / web
```

Open the app at <http://localhost:8080>; the REST API is at <http://localhost:8780>. Full steps in the [Quick Start](docs/en/getting-started/quickstart.md).

### Local development (uv + Python 3.11+)

```bash
uv sync
cp .env.example .env && $EDITOR .env

uv run novelvideo api --port 8780        # start the REST API (CE defaults to inline tasks, no Ray/Redis)
uv run python -m local_gateway.main      # optional: local model gateway adapter on :8790
cd frontend && pnpm install && pnpm dev --port 5180   # start the web UI
```

<br/>

## Supported Models & Providers

DashBox stays model-neutral — all text/image/video/audio models connect through a single **OpenAI-compatible gateway**, in two ways:

- **DashBox official key (recommended)**: `docker compose up`, open <http://localhost:8080> → Settings → Model Config → Official, paste your DC key, save. Works instantly — no model mapping needed. Get a key at <https://relayclaw.cdnfg.com>.
- **Bring your own gateway (BYO)**: point `NEWAPI_BASE_URL` at your own OpenAI-compatible endpoint and map model names (see [Configuring Models](docs/en/getting-started/configuring-models.md)).

> Prefer fully local? Run `docker compose -f docker-compose.selfhosted.yml up` for a bundled `newapi` gateway you configure yourself.

| Stage                | Connected via gateway                                               |
|----------------------|---------------------------------------------------------------------|
| **Text / LLM**       | via OpenAI-compatible gateway (DashBox official key, or BYO)      |
| **Image**            | gpt-image · nano-banana                                             |
| **Video**            | Seedance 1.0 / 1.5 / 2.0 series · happyhorse                        |
| **Voice-over**       | IndexTTS2                                                           |
| **Story graph**      | Cognee                                                             |
| **Task runtime**     | in-process inline (no Ray / Redis / Celery)                        |
| **Storage**          | local filesystem                                                   |

<br/>

## Why DashBox?

**Built for novel-to-short-drama.** General workflow tools can wire nodes together, but they don't know what an "episode beat" is, don't understand why a character's cross-scene identity consistency matters, and won't guard a chapter's emotional arc across image + voice + editing. DashBox builds all that judgment into the tool.

**Every step is decomposable.** Each stage is an independent async task with its own interface. Run sequentially, skip steps, resume mid-way — the toolchain itself is the product, with no hidden black box.

**Self-hostable, model-neutral.** Your manuscript, your characters, your models, your servers. Use closed-source frontier models when you want the best results; switch to open-weight models when you want full control. DashBox won't lock you into any single vendor.

### How DashBox compares

The edge isn't "more generation" — it's organizing the whole short-drama production loop (script → assets → shots → canvas → final cut) into something reusable, collaborative and scalable.

<sub>Legend: ✅ Full · ◐ Partial · ○ Planned · ❌ None — competitor names partially masked; comparison based on publicly available product docs and positioning.</sub>

| Capability | L\*TV | R\*Hub | T\*Now | S\*ko | U\*dream | O\*II | J\*/K\* | **DashBox** |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| Storyboard preview (script→shots, boards) | ◐ | ✅ | ◐ | ✅ | ◐ | ❌ | ❌ | ✅ |
| Interactive series (multi-episode, branching, IP) | ◐ | ◐ | ◐ | ✅ | ◐ | ❌ | ❌ | ✅ |
| Asset library (characters/scenes/props/voices) | ◐ | ❌ | ◐ | ✅ | ◐ | ○ | ❌ | ✅ |
| Scene consistency (variants, 360°, multi-state) | ✅ | ◐ | ❌ | ❌ | ○ | ❌ | ❌ | ✅ |
| Director's world (360°/3D set, camera, framing) | ✅ | ◐ | ❌ | ❌ | ◐ | ❌ | ❌ | ✅ |
| Final delivery (multi-shot, subtitles/SRT, pack) | ✅ | ○ | ○ | ✅ | ✅ | ❌ | ❌ | ✅ |
| Team production (sharing, roles, tasks, cost) | ✅ | ✅ | ○ | ✅ | ○ | ○ | ○ | ✅ |
| Infinite canvas (node-based, free exploration) | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| Dual-track (main pipeline + canvas exploration) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Custom style (templates, prompts, negatives) | ✅ | ◐ | ○ | ✅ | ◐ | ○ | ◐ | ✅ |
| Built-in agent (assistant, skills, suggestions) | ✅ | ✅ | ✅ | ○ | ✅ | ○ | ✅ | ✅ |
| Creative companion (persona, nudges, feedback) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Source-available (self-host, fork, customize) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |

<br/>

## <a name="documentation"></a>Documentation

- [**Product user manual**](https://neo-flying.feishu.cn/docx/JGNTdsjJuo748TxJkxecoYs2nth) — full UI walkthrough (Feishu)
- [Feature overview](./docs/en/concepts/features.md)
- [Architecture](./docs/en/concepts/architecture.md)
- [Quick Start](./docs/en/getting-started/quickstart.md)
- [Self-hosting guide](./docs/en/guides/self-hosting.md)
- [Configuring model providers](./docs/en/getting-started/configuring-models.md)
- [More docs &rarr;](./docs/en/README.md)

<br/>

## Contribute

DashBox is a local customized fork maintained for private use — it does not track upstream releases or accept upstream-bound contributions.

- [Contributing Guide](./CONTRIBUTING.md)
- [Security Policy](./SECURITY.md)

<br/>

## Contributors

The people building DashBox — thank you. 💜

<table>
  <tr>
    <td align="center"><a href="https://github.com/bopy-zou"><img src="https://github.com/bopy-zou.png?size=100" width="72" alt="bopy-zou"/><br/><sub>bopy-zou</sub></a></td>
    <td align="center"><a href="https://github.com/Handanhhhy"><img src="https://github.com/Handanhhhy.png?size=100" width="72" alt="Handanhhhy"/><br/><sub>Handanhhhy</sub></a></td>
    <td align="center"><a href="https://github.com/Hanlin-Gabriel"><img src="https://github.com/Hanlin-Gabriel.png?size=100" width="72" alt="Hanlin-Gabriel"/><br/><sub>Hanlin-Gabriel</sub></a></td>
    <td align="center"><a href="https://github.com/ryanhuang-duat"><img src="https://github.com/ryanhuang-duat.png?size=100" width="72" alt="ryanhuang-duat"/><br/><sub>ryanhuang-duat</sub></a></td>
    <td align="center"><a href="https://github.com/lywaterman"><img src="https://github.com/lywaterman.png?size=100" width="72" alt="lywaterman"/><br/><sub>lywaterman</sub></a></td>
    <td align="center"><a href="https://github.com/n7s4"><img src="https://github.com/n7s4.png?size=100" width="72" alt="n7s4"/><br/><sub>n7s4</sub></a></td>
  </tr>
  <tr>
    <td align="center"><a href="https://github.com/NewYuee"><img src="https://github.com/NewYuee.png?size=100" width="72" alt="NewYuee"/><br/><sub>NewYuee</sub></a></td>
    <td align="center"><a href="https://github.com/SimonRen"><img src="https://github.com/SimonRen.png?size=100" width="72" alt="SimonRen"/><br/><sub>SimonRen</sub></a></td>
    <td align="center"><a href="https://github.com/vkiki"><img src="https://github.com/vkiki.png?size=100" width="72" alt="vkiki"/><br/><sub>vkiki</sub></a></td>
    <td align="center"><a href="https://github.com/wangwenqq"><img src="https://github.com/wangwenqq.png?size=100" width="72" alt="wangwenqq"/><br/><sub>wangwenqq</sub></a></td>
    <td align="center"><a href="https://github.com/zhen2025109"><img src="https://github.com/zhen2025109.png?size=100" width="72" alt="zhen2025109"/><br/><sub>zhen2025109</sub></a></td>
  </tr>
</table>

<br/>

## Produced By

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./assets/partners/neoflying-lab-dark.png">
    <img src="./assets/partners/neoflying-lab.png" alt="Neo Flying AI Laboratory" height="48">
  </picture>
</p>

<p align="center"><sub>Logo is a trademark of its respective owner, shown with permission.</sub></p>

<br/>

## License

[Elastic License 2.0](./LICENSES/Elastic-2.0.txt). Free to use, modify, and redistribute — the only restriction is that you may not resell the software as a hosted service. See the [license explainer](./docs/en/license.md).

<br/>

<div align="center">
  <sub>Built for storytellers. Source, open to all.</sub>
</div>

## Notices

See `NOTICE` for required branding and third-party attribution notices.
