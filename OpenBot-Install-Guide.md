# OpenBot — Install & Usage Guide (Windows)

*Installed September 30, 2026. Release: v0.0.13 (internal build g35ad699f6f76), SHA256-verified against the official `SHA256SUMS-desktop.txt` from the CopilotKit/OpenBot GitHub releases.*

## What OpenBot is

Open-source **AI coworkers** you host yourself. Each coworker ("Bot") gets its own computer — a real Chromium browser with its own profile and files, plus an optional shell — and every action it takes is policy-checked and recorded in an audit trail. It's a template to clone and make your own, not a hosted service.

## What got installed on this machine

| Component | Location / version |
|---|---|
| OpenBot desktop app | `C:\Users\henry\AppData\Local\OpenBot\openbot-desktop.exe` (v0.0.13) |
| Bun (JS runtime it uses) | `C:\Users\henry\.bun\bin\bun.exe` (1.4.2) |
| Docker Desktop + WSL2 | Installed via winget; engine verified running (29.8.1) |
| Source clone (optional, for editing OpenBot itself) | `C:\Users\henry\OpenBot` (with `bun install` already done) |
| Installer kept at | `C:\Users\henry\Downloads\OpenBot-setup.exe` |

## Finish setup inside the app (the only steps left)

The app is open now. It walks you through the rest — the wizard installs whatever is still missing and asks for your sign-ins:

1. **Set up OpenBot** → keep the default Bot (LangGraph) unless you want a specific framework.
2. **Choose where OpenBot lives** → the local deployment folder the app prepares.
3. **Click Install OpenBot** → it provisions the stack (Postgres, API, app, Bot containers) using the Docker engine already running.
4. **Connect an AI provider** → OpenAI, Anthropic, or any OpenAI-compatible endpoint (Ollama, Azure, a gateway). This needs a real API key from that provider — paste it in the app, not into any file.
5. **Sign in to CopilotKit** → free account; it provisions threads/memory ("Intelligence") and opens an isolated Chrome/Edge window for the sign-in.
6. **Start OpenBot** → the app opens the product UI.

## Prove it works (the repo's own test)

A plain text answer isn't enough. Ask a Bot to:

> Open news.ycombinator.com and tell me the top story — then show it as a small bar chart.

Passing means you saw its browser open the site in the app's screen panel **and** a chart render in the transcript. Also try `https://httpbin.org/forms/post` for form-filling, then inspect **Admin → Audit** to see every recorded action.

## Where things live once started

| Surface | URL / location |
|---|---|
| Main app | `http://localhost:3010` (desktop build may serve from 3001) |
| API server | port 3001 |
| Bot's own browser/computer | containerized, visible in the channel's screen panel |
| Admin | `/admin/audit`, `/admin/boundaries` (deny rules), `/admin/credentials` (encrypted), `/admin/computers` |
| Coworker management | `/agents` — create, duplicate, pin, hide, delete |
| Source clone | `C:\Users\henry\OpenBot` (`examples/fintech/agents/` = example coworkers; edit `agents.yaml` to add your own) |

## Everyday commands (if you ever run it from the source clone instead)

```bash
cd C:\Users\henry\OpenBot
bash scripts/start.sh   # starts everything, waits for health checks
bash scripts/stop.sh    # stops everything incl. Bot computers (nothing is deleted)
```

Health check: `curl -s localhost:3001/api/capabilities` should return JSON containing `"mode":"intelligence"`.

## Free / cheap model options (for the AI provider step)

| Option | Cost | How to connect |
|---|---|---|
| **Ollama** (local) | 100% free, offline | Install Ollama, pull a model (`ollama pull llama3.1:8b`), then in the wizard's *OpenAI-compatible endpoint* row: base `http://localhost:11434/v1`, any API key, model = the tag pulled |
| **Groq** free tier | Free (rate-limited) | Key from console.groq.com → OpenAI-compatible row: `https://api.groq.com/openai/v1`, model `llama-3.3-70b-versatile`. Great tool-calling |
| **Google Gemini** free tier | Free (rate-limited) | Key from aistudio.google.com → OpenAI-compatible row: `https://generativelanguage.googleapis.com/v1beta/openai/`, model `gemini-2.5-flash` |
| **GitHub Models** | Free with GitHub account | Token from github.com/settings → base `https://models.github.ai/inference` |
| **OpenRouter** | Has `:free` models | Key from openrouter.ai → `https://openrouter.ai/api/v1`, pick a model ending in `:free` |
| **DeepSeek** | ~5M free trial tokens on signup, then paid (very cheap) | Key from platform.deepseek.com → base `https://api.deepseek.com/v1`, model `deepseek-chat` |

Note: the browser-driving test needs a model that is good at tool calls — small local models may fumble it; Groq's Llama 70B or Gemini Flash are the strongest free choices.

## Fixes applied on this machine (Sept 30, 2026)

1. **Docker credential error** (`docker-credential-desktop not found`): removed `"credsStore"` from `~/.docker/config.json` and added Docker's bin dir to the user PATH; pre-pulled all 18 digest-pinned images from the v0.0.15 release.
2. **"Failed to initialize thread"**: the app's credential vault (`C:\Users\henry\OpenBot\.secrets\INTELLIGENCE_API_KEY.dpapi`) held a garbage string saved during setup. Provisioned a real project key (`copilotkit login` → `project select --create` → `bun scripts/setup-learning.ts`) and wrote it into the vault (same DPAPI scheme).
3. **Server refused to start (PORT invalid)**: the CLI's `.env` rewrite dropped `PORT=3001` — restored. Also: launching the desktop app from a shell that has `PORT=0` set poisons the stack (bun's `--env-file` does not override inherited env). Launch the app normally (Start menu / double-click), not from such a shell.
4. **Bun version skew**: replaced Bun 1.4.2 with the repo-pinned **1.3.14** at `C:\Users\henry\.bun\bin\bun.exe` (1.4.2 fails on an `eventsource` require in the server's dependency graph).

## Slash commands & skills (fixed Sept 30, 2026)

- **`/config` is not an OpenBot feature** — the Bot suggested it because the underlying Claude Code CLI has one, but OpenBot's composer doesn't. Don't expect it. The model is chosen at the AI-provider step (re-run the app's setup to change provider/model). The current harness runs **Claude Sonnet 5** via your Anthropic connection.
- **`/` in the composer** opens a menu of **skills granted to that Bot**. Skills only appear for Bots they've been granted to (Admin → Plugins → grants). All 7 example workspace skills are now granted to the default harness Bot.
- **Skills + the Claude Code harness**: OpenBot injects the skill instruction as a system turn and sends the `/name` text through — Claude Code treats unknown `/name` as its own command. Fix applied: all 7 skills were installed as **native Claude Code custom commands** inside the harness container (`/root/.claude/commands/<slug>.md`), so `/whats-changed` etc. now execute properly. Verified: the Skill tool fires and the Bot runs the instruction.
- Document-based skills (`/find-a-document`, `/whats-changed`, `/who-owns-this`, `/check-a-claim`) report honestly when no sources are connected — connect Notion/Drive/MCP under **Admin → Plugins** to give them data. `/skill-creator` and `/bot-creator` work conversationally.
- Note: the installed command files live in the container's `/root/.claude/commands/`. If the harness container is ever recreated (`scripts/stop.sh` then `start.sh` keeps volumes but recreates containers), re-copy them from `%TEMP%\cpcommands\` via `docker cp`.

## Troubleshooting

- **App won't start / WSL errors** → the desktop app reports the exact blocker sentence; virtualization must be enabled in BIOS if WSL fails to boot.
- **Docker engine not running** → start Docker Desktop, wait for the whale icon, then retry.
- **"Model credential is not configured"** → paste your OpenAI/Anthropic key on the provider screen.
- **SmartScreen warning** → the build is unsigned; if Windows warns when first launching, click *More info → Run anyway*.
- **Updates** → watch https://github.com/CopilotKit/OpenBot/releases; newer desktop installers appear there.

## Useful links

- Repo: https://github.com/CopilotKit/OpenBot
- Docs in the clone: `C:\Users\henry\OpenBot\docs\` (configuration.md, architecture.md, deployment.md, coworkers.md)
- Product page: https://copilotkit.ai/openbot
