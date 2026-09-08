# soev.ai

> **Sovereign AI for the public sector.**

soev.ai is a self-hosted AI platform built for Dutch public sector organizations. It is a fork of [**Open WebUI**](https://github.com/open-webui/open-webui), preserving the full upstream feature set and adding the integrations, compliance, and operational tooling needed for regulated environments.

<<<<<<< HEAD
![Open WebUI Banner](./banner.png)

A product by [Gradient-DS](https://gradient-ds.com). Learn more at [soev.ai](https://soev.ai).
=======
Open WebUI is **a home for AI**, a self-hosted AI platform that's **[extensible](https://docs.openwebui.com/features/extensibility/plugin/)**, **[feature-rich](https://docs.openwebui.com/features/)**, user-friendly, and built to run **[entirely offline](https://openwebui.com/sovereign-ai)**. With support for **Ollama** and **OpenAI-compatible APIs**, it gives you a powerful, provider-agnostic interface for both local and cloud-based models.

Passionate about open-source AI? [Join our team →](https://careers.openwebui.com/)

![Open WebUI Demo](./demo.png)

> [!TIP]  
> **Looking for an [Enterprise Plan](https://docs.openwebui.com/enterprise)?** – **[Speak with Our Sales Team Today!](https://docs.openwebui.com/enterprise)**

For more information, be sure to check out our [Open WebUI Documentation](https://docs.openwebui.com/).

## Key Features of Open WebUI ⭐

- 🚀 **Effortless Setup**: Install seamlessly via pip, uv, Docker, or Kubernetes (kubectl, kustomize, or helm), with `:ollama` and `:cuda` tagged images available for container deployments.

- 🤝 **Broad Model & API Integration**: Connect any OpenAI-compatible API alongside local Ollama models. Point the API URL at **LMStudio, GroqCloud, Mistral, OpenRouter, vLLM, and more** to mix and match providers freely.

- 🔐 **Granular RBAC & User Groups**: Administrators define detailed roles, groups, and permissions, giving each user exactly the access they need. Secure by default, with tailored experiences per group.

- 🧩 **Plugin Support**: Extend Open WebUI with **Filters**, **Actions**, **Pipes**, **Tools**, and **Skills**. Connect external services through **MCP**, **MCPO**, and **OpenAPI tool servers**. Build custom integrations, rate limits, approval flows, data connections, and more.

- 🤖 **Models & Agents**: Wrap any base model with custom instructions, tools, and knowledge to build specialized agents. Supports dynamic variables, per-user/group access control, and community preset imports via [Open WebUI Community](https://openwebui.com/).

- 📝 **Notes**: A dedicated workspace for content outside conversations. Draft with a rich editor, use AI to rewrite selected text, and attach notes to any chat for full-context injection.

- 📢 **Channels**: Real-time shared spaces where your team and AI models collaborate in one timeline. Tag models to draft or critique, with threads, reactions, pins, and access control.

- 🧠 **Persistent Memory**: The AI remembers facts about you across conversations, carrying context from one chat to the next.

- ✅ **Live Workflow & Message Flow**: Watch the AI build and work through checklists in real time. Queue messages while the AI is still responding; they send automatically when it's ready.

- 📅 **Calendar & AI Scheduling**: Built-in personal and shared calendars with month/week/day views, recurring events, color coding, attendees, and reminders. Models manage your schedule conversationally through native function calling.

- ⏱️ **Automations**: Schedule prompts to run on recurring schedules, with runs surfaced on your calendar and each completed run linking back to the chat it produced.

- 📱 **Responsive Design & PWA**: Seamless experience across desktop, laptop, and mobile, with a Progressive Web App for native app-like feel and offline access on localhost.

- ✒️🔢 **Full Markdown and LaTeX Support**: Comprehensive Markdown and LaTeX capabilities for enriched interaction.

- 🎤📹 **Hands-Free Voice/Video Call**: Integrated voice and video calls with multiple Speech-to-Text providers (Local Whisper, OpenAI, Deepgram, Azure) and Text-to-Speech engines (Azure, ElevenLabs, OpenAI, Transformers, WebAPI).

- 💾 **Persistent Artifact Storage**: Built-in key-value storage API for artifacts, enabling journals, trackers, leaderboards, and collaborative tools with personal and shared data scopes.

- 📚 **Local RAG Integration**: Retrieval Augmented Generation backed by 9 vector databases and multiple content-extraction engines (Tika, Docling, Document Intelligence, Mistral OCR, PaddleOCR-vl, external loaders). Supports hybrid search (BM25 + vector) with reranking and full-context mode. Load documents into chat or pull them from your library with the `#` command.

- 🔍 **Web Search for RAG**: Search the web through dozens of providers including `SearXNG`, `Google PSE`, `Brave Search`, `Kagi`, `Mojeek`, `Tavily`, `Perplexity`, `Firecrawl`, `serpstack`, `serper`, `Serply`, `DuckDuckGo`, `SearchApi`, `SerpApi`, `Bing`, `Jina`, `Exa`, `Sougou`, `Azure AI Search`, and `Ollama Cloud`, injecting results directly into the conversation.

- 🌐 **Web Browsing Capability**: Pull websites into chat with the `#` command followed by a URL, or let the model fetch them on its own when needed.

- 🎨 **Image Generation & Editing**: Create and edit images with multiple engines including OpenAI DALL·E, Gemini, ComfyUI (local), and AUTOMATIC1111 (local), supporting both generation and prompt-based editing.

- ⚙️ **Multi-Model Conversations**: Engage several models at once, harnessing their individual strengths in parallel for the best possible responses.

- 📊 **Usage Analytics & Model Evaluation**: Admin dashboards track message volume, token consumption, and cost across users and models. Evaluate models with a built-in arena, A/B testing, and ELO-based leaderboards.

- 🗄️ **Flexible Database & Storage**: Choose SQLite (with optional encryption) or PostgreSQL, and store files locally or on S3, Google Cloud Storage, or Azure Blob Storage.

- 🧬 **Advanced Vector Database Support**: Pick from 9 vector databases: ChromaDB, PGVector, Qdrant, Milvus, Elasticsearch, OpenSearch, Pinecone, S3Vector, and Oracle 23ai.

- 🪪 **Enterprise Authentication & Provisioning**: Full LDAP/Active Directory integration, SSO via trusted headers and OAuth providers, and SCIM 2.0 automated provisioning for identity providers like Okta, Azure AD, and Google Workspace.

- ☁️ **Cloud-Native File Integration**: Native Google Drive and OneDrive/SharePoint file picking for seamless document import from enterprise cloud storage.

- 🔭 **Production Observability**: Built-in OpenTelemetry support for traces, metrics, and logs, plugging into your existing monitoring stack.

- ⚖️ **Horizontal Scalability**: Redis-backed session management and WebSocket support for multi-worker, multi-node deployments behind load balancers.

- 🌐🌍 **Multilingual Support**: Use Open WebUI in your preferred language with i18n support. We're actively seeking contributors to expand language coverage!

- 🌟 **Continuous Updates**: We're committed to improving Open WebUI with regular updates, fixes, and new features.

- 🛡️ **Transparent Security Process**: Security reports are triaged, fixed, and published as open advisories through a documented responsible-disclosure process. See our [Security Policy](https://github.com/open-webui/open-webui/security).

Want to learn more about Open WebUI's features? Check out our [Open WebUI documentation](https://docs.openwebui.com/features) for a comprehensive overview!

## The Open WebUI Ecosystem 🌐

Open WebUI is the core, surrounded by companion apps and infrastructure that extend what your AI can do, where it can reach, and how you run it:

- 💻 **Open WebUI Computer** ([open-webui/computer](https://github.com/open-webui/computer)): A standalone, mobile-first computer and coding agent that runs on the machine you own. Files, terminal, and git in a browser tab, reachable from your phone. Connect it into Open WebUI as a model, or reach it from Telegram, WhatsApp, and more.

- ⚡ **Open Terminal** and **Terminals (Enterprise)** ([open-webui/open-terminal](https://github.com/open-webui/open-terminal) & [open-webui/terminals](https://github.com/open-webui/terminals)): A self-hosted computing environment that plugs into Open WebUI, giving the AI a place to write code, run it, read output, fix errors, and iterate inside the chat. Terminals gives you per-user isolated containers with separate credentials, resource limits, and network rules. Automatic lifecycle management on Docker or Kubernetes.

- 🔄 **oikb** ([open-webui/oikb](https://github.com/open-webui/oikb)): Feed your Knowledge Bases from 45+ sources (GitHub, Confluence, ServiceNow, Salesforce, Jira, Slack, SharePoint, Notion, and more), keeping the tools your team already uses continuously in sync.

- 🖥️ **Native Desktop App** ([open-webui/desktop](https://github.com/open-webui/desktop)): Run Open WebUI as a native app on macOS, Windows, and Linux. System-wide Spotlight chat bar with screenshot capture, push-to-talk voice, and optional fully-local inference via a built-in llama.cpp engine.

Want to learn more? Check out our [Open WebUI documentation](https://docs.openwebui.com) for more details!
>>>>>>> upstream/main

---

## Why a fork

- **Data sovereignty** — public sector deployments need a source-available, hardened, self-hostable stack.
- **Compliance** — GDPR-driven retention, export, and archival flows are first-class.
- **Ecosystem fit** — tight integration with Microsoft 365 (OneDrive, Graph), Google Workspace, and our external agents and pipelines.
- **Upstream compatibility** — we sync regularly with Open WebUI and prefer additive changes (separate files, feature flags, conditional mounts) so upstream improvements keep flowing in.

## What soev.ai adds

On top of everything Open WebUI already offers:

- **Cloud sync** — Native OneDrive and Google Drive knowledge base sync with incremental updates and permission lifecycle.
- **Typed knowledge bases** — `local` / `onedrive` / `google_drive` / `custom` with appropriate UI per type.
- **External agents API** — Reverse proxy at `/api/v1/agent/`, OpenAI-compatible, for routing to a configurable agent service.
- **External pipeline & integration providers** — Pluggable upload / parse / retrieval providers via env config.
- **GDPR features** — User data export (zip), configurable data retention (TTL with warning emails), per-user archival.
- **TOTP 2FA** — With admin enforcement, recovery codes, and partial-JWT login flow.
- **SSO invite auto-provisioning** — Microsoft Graph email invites with SSO pre-provisioning.
- **Granular feature flags** — Disable any custom UI feature per deployment.
- **Helm chart** — Production-ready Kubernetes deployment.
- **Security hardening** — Trivy + Bandit + pip-audit in CI, slim Docker image, replay-token fixes.

For everything else (chat UI, model providers, RAG, voice, image gen, multi-model conversations, …) see the [Open WebUI documentation](https://docs.openwebui.com/).

## Quickstart

```bash
# Docker (fastest)
docker compose up -d
```

Open [http://localhost:3000](http://localhost:3000) after startup.

For local development:

```bash
npm install
pip install -e ".[dev]"

# Terminal 1: backend on :8080
open-webui dev

# Terminal 2: frontend on :5173 (proxies to backend)
npm run dev
```

See [`.env.example`](./.env.example) for configuration. soev.ai–specific options (cloud sync, agent API, feature flags, retention) are annotated in the file. For the full installation matrix (pip, Docker, Kubernetes, GPU variants) see the [Open WebUI install docs](https://docs.openwebui.com/getting-started/).

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](./CONTRIBUTING.md) before opening a PR.

In short: fork this repo, branch from `dev`, PR back to `dev`. Maintainers handle the `dev → test → main` promotion.

## Security

Please report vulnerabilities privately — see [SECURITY.md](./SECURITY.md). Do not open public issues for security reports.

## License & credits

soev.ai is distributed under the same [BSD-3-Clause-modified license](./LICENSE) as Open WebUI. Open WebUI branding is preserved per the license terms.

All upstream code and features remain the work of the Open WebUI project and its community. We are grateful to [Timothy Jaeryang Baek](https://github.com/tjbck) and the Open WebUI contributors for the foundation this fork is built on. Bugs and features that affect upstream behaviour are best contributed at [open-webui/open-webui](https://github.com/open-webui/open-webui) so the wider community benefits.

Copyright © 2023– Open WebUI Inc. soev.ai customizations © Gradient-DS.
