# Driving DashBox from an AI agent (MCP)

DashBox ships an [MCP](https://modelcontextprotocol.io) server that exposes the
whole drama pipeline — ingest, characters, script, storyboards, first frames,
video, audio, compose, export — as ~34 tools. Any MCP-speaking agent
(**Claude Code**, Codex, etc.) can use it to build a drama end to end.

The server (`src/novelvideo/chat/dashbox_mcp.py`) is a thin stdio bridge over
the DashBox REST API: each tool is an HTTP call to your running instance, so it
inherits the same auth, project guards, and task queue as the web UI.

## Prerequisites

- A running DashBox instance (`docker compose up -d`, REST API on `:8780`).
- A configured, funded model gateway (Settings → Model Config) — the agent can
  plan/script without it, but image/video/audio generation needs working models.
- Local checkout with [`uv`](https://docs.astral.sh/uv/) synced (`uv sync`) so the
  `novelvideo` package is importable.

## Auth: two modes

The server authenticates to the API with two environment variables:

| Variable | Purpose |
|---|---|
| `DASHBOX_API_URL` | Base URL of your instance, e.g. `http://localhost:8780` |
| `DASHBOX_CE_OWNER` | Set to `1` for **CE** (self-host): calls are made as the local owner, no token needed. The target **must be loopback** (`localhost`, `127.0.0.1`, `::1`, `[::1]`) |
| `DASHBOX_CE_OWNER_ALLOW_REMOTE` | Unsafe override: set to `1` to allow tokenless CE-owner mode against a **non-loopback** `DASHBOX_API_URL` (see the warning below) |
| `DASHBOX_AGENT_TOKEN` | **EE / multi-user** only: a scoped agent-session bearer token |
| `DASHBOX_PROJECT_ID` | Optional: default project so tools can omit `project_id` |

**Community Edition** trusts local requests as the owner, so `DASHBOX_CE_OWNER=1`
is all you need — no token to mint. On EE, set `DASHBOX_AGENT_TOKEN` instead;
CE-owner mode is ignored when a token is present (the token always takes
precedence and its `Authorization` header is sent).

**Loopback is enforced.** Because tokenless owner mode sends *unauthenticated,
owner-level* requests, the bridge refuses any `DASHBOX_API_URL` whose host is
not a loopback address (`localhost`, `127.0.0.1`, `::1`, `[::1]`) with a clear
error. If you genuinely must reach a remote CE without a token — e.g. an SSH
tunnel you fully control — opt in explicitly with
`DASHBOX_CE_OWNER_ALLOW_REMOTE=1`. This is deliberately a separate variable so
it can never be enabled by accident; prefer a scoped `DASHBOX_AGENT_TOKEN` for
any non-local target.

## Connect Claude Code

This repo ships a project [`.mcp.json`](../../../.mcp.json). Open the repo in
Claude Code and approve the `dashbox` server when prompted — that's it.

Or add it explicitly:

```bash
claude mcp add dashbox \
  --env DASHBOX_API_URL=http://localhost:8780 \
  --env DASHBOX_CE_OWNER=1 \
  -- uv run python -m novelvideo.chat.dashbox_mcp
```

Verify the tools are live:

```bash
claude mcp list        # dashbox → ✓ connected
```

> **Running via Docker only?** Launch the bridge inside the container instead:
> set the `.mcp.json` command to
> `docker compose exec -T -e DASHBOX_API_URL=http://localhost:8780 -e DASHBOX_CE_OWNER=1 api python -m novelvideo.chat.dashbox_mcp`.

## What the agent can do

The tools cover the full pipeline. A typical end-to-end run:

1. `dashbox_post` → `/projects/{p}/ingest/upload` + `/ingest/start` — ingest a manuscript
2. `dashbox_build_characters` → extract the cast
3. `dashbox_plan_episodes` → segment into episodes
4. `dashbox_plan_identities` → per-episode identities
5. `dashbox_generate_script` → episode script
6. `dashbox_generate_portrait` / `dashbox_generate_scene_master` → key art
7. `dashbox_generate_sketches` → storyboards
8. `dashbox_render_first_frames` → first frames
9. `dashbox_start_single_video` → shot video
10. `dashbox_generate_audio` → voice-over
11. `dashbox_compose_episode` → assemble
12. `dashbox_get_final_video` → the finished episode (returns a servable URL)

Generation is **asynchronous**: a tool call starts a task, then the agent polls
`dashbox_pipeline_status` / `dashbox_list_tasks` / `dashbox_get_task` until
it completes. Each tool's description names the exact task to poll. The generic
`dashbox_get` / `dashbox_post` / `dashbox_patch` / `dashbox_delete` tools
are an escape hatch for any endpoint not covered by a curated verb.

## Security notes

- CE-owner mode grants **full owner access** to the instance it points at — only
  use it against a **local** CE you control. Never point it at a shared instance.
- The bridge enforces this: tokenless CE-owner mode only accepts a **loopback**
  `DASHBOX_API_URL` and errors out on any remote host unless you set the
  explicit `DASHBOX_CE_OWNER_ALLOW_REMOTE=1` override.
- On EE, use a scoped `DASHBOX_AGENT_TOKEN`; the server enforces the token's
  project boundary and write scopes.
