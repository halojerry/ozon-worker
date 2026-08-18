# POUNDING Ozon Listing Assistant (ozon-worker)

> An all-in-one cross-border e-commerce tool that turns "source from 1688 → AI image generation → auto-list on Ozon" into a few commands. It replaces the tedious manual workflow of product hunting, translating, image making, attribute filling, and moderation waiting.

Current version: **v0.29.2** (see [`CHANGELOG.md`](CHANGELOG.md))

---

## ✨ Core Features

- **Fully automated listing pipeline**: 1688 product → category matching → pricing → attribute filling → AI image generation (10 images) → Russian localization → validation → upload to Ozon → moderation tracking — no manual intervention
- **Self-learning category mapping**: every successful listing writes back to the category mapping cache (`category_mapping` learning table + external synonym config), getting smarter over time
- **Ozon follow-selling, dual mode**: `hand` (anti-infringement, rebuilt via CREATE) / `api` (copied via import-by-sku), with automatic fallback
- **Competitor attribute priority**: follow-selling fills attributes from the competitor's Russian values; missing weight/dimensions fall back to competitor data
- **Image model routing**: main image / social_proof use gpt-image-2, other nodes use banana; timeout auto-degrades, prompts are externalized as hot-reloadable JSON
- **Hazardous-goods safe fallback**: required hazard-class attributes only ever get "non-hazardous" defaults — never blindly filled
- **Zero-tolerance for Chinese characters**: title/description/attributes/manufacturer name are fully Russianized; anything containing Chinese is rejected before upload (Ozon hard requirement)
- **Pricing engine**: CNY-store pricing (commission, logistics, margin), automatic weight/dimension correction, pre-queue sanity checks reject invalid envelopes
- **Observability**: structured JSON logging, trace IDs (trace_id/task_id), Sentry reporting for task exceptions/timeouts, persisted task progress
- **Auto-update**: the Skill silently checks for new versions on every command; sha256 verification + backup + rollback on failure

## 🏗️ System Architecture

A two-stage design with strictly separated responsibilities:

```
┌────────────────────── Customer Local ──────────────────────┐
│  Skill (pounding-ozon-probe)                               │
│  1688/Ozon CDP scraping · image search · envelope assembly │
│  Does NOT list anything                                    │
│  Output: GraphInput envelope {draft, source, extensions}   │
└─────────────────────────────┬──────────────────────────────┘
                              │ HTTPS
┌─────────────────────────────▼──────────────────────────────┐
│  Worker (cloud Docker + PostgreSQL)                        │
│  Auth → category match → pricing → attributes → AI images  │
│  → validation → upload → moderation → learning (~24 nodes) │
└────────────────────────────────────────────────────────────┘
```

- **Skill**: runs locally on the customer's machine; auto-launches Chrome (CDP scraping, keeps login state); core libraries compiled to native binaries with Cython (source protection); only 3 dependencies — `requests` / `websocket-client` / `Pillow`
- **Worker**: cloud FastAPI + LangGraph workflow; 30 concurrent tasks by default (`MAX_CONCURRENT` configurable); PG queue with checkpoint recovery and zombie-task cleanup
- **Contract**: `GraphInput` three-layer envelope (`worker/src/graphs/state.py`), see [`docs/CONTRACT-v4.md`](docs/CONTRACT-v4.md)

### Three Business Pipelines

| Pipeline | Command | Flow |
|----------|---------|------|
| **A. 1688 sourcing & listing** | `graph --url <1688 URL>` | scrape 1688 → assemble envelope → Worker full pipeline |
| **B. Ozon follow-selling** | `follow --ozon-url <Ozon URL>` | scrape competitor → image-search same item on 1688 → follow pipeline (anti-infringement / copy modes) |
| **C. Ozon product discovery** | `discover --keyword "..."` | collect Ozon products → blue-ocean scoring → 1688 matching + profit calc → confirm & submit |

## 🤝 How Skill and Worker Work Together

The two stages hand off a single **`GraphInput` envelope** over one HTTPS request. The Skill only "collects and assembles"; the Worker only "consumes and lists":

```
Agent (ZCode / Claude Code, etc.)
  │  invokes CLI commands (graph / follow / discover / ...)
  ▼
Skill (customer local)
  │  1688 / Ozon CDP scraping · image search · variant collapsing · envelope assembly
  │  ⚠️ Does NOT list — only produces a GraphInput envelope
  ▼
POST /api/v1/submit_task (GraphInput JSON)
  ▼
Worker (cloud Docker)
  │  category match → pricing → attributes → AI images → Russianize → validate → upload → moderation → learning write-back
  ▼
returns task_id → Agent polls with `query <task_id>` → reports to user
```

### Division of Responsibility

| | Skill | Worker |
|---|---|---|
| **Location** | Customer local | Cloud Docker |
| **Role** | Tool invoked by the Agent (ZCode / Claude Code, etc.) | Consumes envelopes and lists products |
| **Entry point** | `skill/SKILL.md` (Agent operating manual) | `worker/src/main.py` (FastAPI + CLI) |
| **Responsibilities** | 1688 / Ozon CDP scraping, image search, envelope assembly | Category → pricing → attributes → images → validation → upload → self-learning |
| **Hard boundary** | **Never lists** (calls no Ozon listing API) | **Never scrapes** (only consumes envelopes, no 1688 collection) |

### The GraphInput Envelope (Three Layers)

Defined in `worker/src/graphs/state.py`; full contract in [`docs/CONTRACT-v4.md`](docs/CONTRACT-v4.md):

```json
{
  "token": "sk-...",
  "ozon_client_id": "4718259",
  "ozon_api_key": "...",
  "envelope": {
    "draft": {
      "item_id": "980815374096",
      "title": "Pet automatic water dispenser...",
      "images": ["https://cbu01.alicdn.com/..."],
      "weight": 227,
      "dimensions": {"length": 120, "width": 80, "height": 60},
      "purchase_cost": 5.5,
      "purchase_url": "https://detail.1688.com/offer/980815374096.html",
      "attributes": {"Brand": "...", "Material": "..."},
      "ozon_category": {"description_category_id": "17028929", "type_id": "504866264"}
    },
    "source": {"purchase_url": "...", "purchase_cost": 5.5},
    "extensions": {
      "margin_rate": 0.25,
      "commission_rate": 0.10,
      "fx_buffer": 0.05,
      "follow_sell": true
    }
  }
}
```

Key conventions:

- **`draft`** — product data: `title` / `images[]` / `weight` (grams) / `dimensions` (mm; 1688 cm already ×10 in the Skill layer) / `purchase_cost` (CNY, already includes 1688 domestic freight)
- **`source`** — sourcing info; **`extensions`** — pricing config + `follow_sell` flag (routes the Worker into the follow pipeline)
- **Single-product listing**: the Skill collapses multi-variant items into a single product (one 1688 item = one Ozon product card); the Worker needs zero changes
- **Multi-SKU**: `variants` holds at most 1 element (already collapsed in the Skill layer); other fields are flattened under `draft`

### End-to-End Sequence of One Listing

1. The Agent receives a user request → picks a pipeline via "intent routing" (see below)
2. The Agent invokes a Skill command (`graph` / `follow` / `discover` / ...) → the Skill scrapes locally via Chrome CDP and assembles the envelope
3. The Skill POSTs the `GraphInput` to Worker `/api/v1/submit_task` → gets back a `task_id` (UUID)
4. The Worker runs ~24 LangGraph nodes in the cloud: auth → category match → pricing → attributes → AI image generation (10) → Russianization & sanitization → Ozon validation → upload → moderation polling → category-mapping learning write-back
5. The Agent polls with `query <task_id>` → reports the result to the user (Ozon product link, profit margin, moderation status)

## 🤖 How an Agent Invokes the Toolchain

The Agent's operating manual is **`skill/SKILL.md`** — intent-routing decision table, command reference, decision boundaries, and common off-limit behaviors. An Agent must read it before doing anything.

### Trigger Rules

Invoke the `pounding-ozon-probe` skill when the user message contains: a 1688 / Ozon link, "list / upload / publish", "follow sell / copy", "product discovery / blue ocean / recommend", or "search by image".

### Intent Routing (Decision Table Summary)

Judge the user's intent first, then pick a pipeline. **Re-judge before every operation** — never default by conversation inertia:

| User input | Pipeline | Command |
|---|---|---|
| 1688 product link | A. Direct listing | `graph --url <1688 URL>` |
| Ozon product link | B. Follow-selling | `follow --ozon-url <URL>` |
| Ozon search / category page | C. Collect & discover | `discover --url <URL>` |
| Image (no URL) | D1. Search by image | `image_search --image <URL>` → show candidates → user confirms → `graph` |
| "Trend / hot / bestseller" + category | E. Trend discovery | agent runs web_search + LLM to distill keywords → `discover --keyword <kw>` (no `trend` command; web_search before discover is mandatory) |
| Multiple URLs / batch | F. Batch processing | `batch_test --urls-file urls.txt --submit` |
| "Blue ocean", no URL | C. Follow-discovery | `discover --keyword "..."` |
| "Discover / list", no URL | D. Discover & list | `discover --keyword "..."` → show candidates → confirm submit |
| Ambiguous reference / count mismatch / re-list | — | Ask clarifying questions; never guess |

### Decision Boundaries

| Operation | Policy |
|---|---|
| `check`, `set_store`, `set_token`, `set_ak` (environment prep) | Execute automatically, no confirmation needed |
| `graph` / `follow` (user gave an explicit URL) | Execute automatically |
| Final submit after `discover`, batch operations | **Require explicit user confirmation** |
| Profit margins, quality of candidate products | Show, don't judge — let the user decide |

### Hard Rules for Agents

- **Only use the commands in SKILL.md** — never hand-write Python or scrape with requests/urllib
- Respect the architecture boundary: no listing calls in the Skill, no 1688 scraping in the Worker
- Wait for explicit user confirmation before any "submit / list"
- Pipeline E (trend discovery) must never skip the web_search step — use agent web_search + LLM to distill keywords, then `discover --keyword` (the `trend` command was removed in v0.31)

## 🚀 Quick Start

### 1. Deploy the Worker (cloud, one-time)

```bash
cd deploy
cp .env.example .env        # fill in PGDATABASE_URL / SUPABASE_URL / SUPABASE_KEY, etc.
bash deploy.sh              # one-click deploy (Docker Compose: Worker + PostgreSQL, auto data init)
```

Update: `bash deploy/update.sh` (git pull → rebuild → restart) or `bash deploy/cos-update.sh` (one-click upgrade reading the COS manifest; sha256 verification + automatic rollback). Full guide: [`docs/DEPLOY.md`](docs/DEPLOY.md).

### 2. Install the Skill (customer local)

```bash
pip install -r requirements.txt        # Python ≥3.12; Chrome auto-launches, no Playwright needed
python3.12 scripts/cli.py check        # environment check: launch Chrome, verify login & credentials
```

Credentials (1688 AK / Ozon API key / store config) live in local `data/config/` — configure with `set_token` / `set_ak` / `set_store`.

### 3. List Your First Product

```bash
# Source & list from 1688
python3.12 scripts/cli.py graph --url "https://detail.1688.com/offer/xxx.html"

# Follow-sell an Ozon product
python3.12 scripts/cli.py follow --ozon-url "https://www.ozon.ru/product/xxx/"

# Check task status
python3.12 scripts/cli.py query <task-id>
```

## 📦 Command Reference

| Command | Description |
|---|---|
| `check` | Environment check + auto-launch Chrome + credential verification |
| `graph --url <1688 URL>` | Source & list from 1688 (pipeline A) |
| `follow --ozon-url <URL>` | Ozon follow-selling (pipeline B) |
| `discover --keyword "..."` | Blue-ocean discovery with table analysis + batch sourcing (pipeline C) |
| `image_search --image <URL>` | Search products by image (1688 web image search) |
| `get_ak` | Auto-fetch the 1688 AK in a browser |
| `batch_test --urls-file urls.txt` | Batch-process a list of URLs |
| `query <task-id>` | Query task status / duration / product details |
| `cleanup` | Disk cleanup: `--profile-cache` (regenerable Chrome profile cache) / `--cache` (disk cache) / `--temp` (orphan .json.tmp) / `--old-results --days N` (expired results), `--dry-run` preview |

> Full Agent manual: [`skill/SKILL.md`](skill/SKILL.md). Maintainer code notes: [`skill/README.md`](skill/README.md).

## 🔌 Worker API Endpoints

| Feature | Path | Method |
|---|---|---|
| Submit task | `/api/v1/submit_task` | POST |
| Auth verification | `/api/v1/auth/verify` | POST |
| Task status | `/api/v1/task_status/{id}` | GET |
| Cancel task | `/api/v1/cancel_task/{id}` | POST |
| Task statistics | `/api/v1/task_statistics` | GET |
| Progress | `/progress/{run_id}` | GET |
| Health check | `/api/v1/health` | GET |
| API docs (Swagger) | `/api/v1/docs` | GET |

(Legacy paths remain compatible. Auth uses the `token` field in the request body, verified against the Supabase `tokens` table.)

## 🗂️ Directory Structure

```
ozon-worker/
├── skill/                  # Customer local: scraping + envelope assembly (Python ≥3.12)
│   ├── SKILL.md            # ⭐ Agent operating manual
│   ├── scripts/cli.py      # CLI entry (check/graph/follow/discover/...)
│   └── scripts/lib/        # CDP client, 1688/Ozon APIs, cache, credential store
├── worker/                 # Cloud: LangGraph listing workflow (Docker)
│   ├── src/main.py         # FastAPI + CLI entry
│   ├── src/graphs/         # main graph + ~24 nodes
│   ├── src/storage/        # PG queue / checkpoints
│   ├── src/utils/          # Ozon client, pricing, image gen, logging
│   ├── assets/             # category tree JSON, logistics rates, Ozon API docs
│   └── config/             # LLM / image prompt config (hot-reloadable)
├── deploy/                 # Docker Compose + one-click deploy/update scripts
├── docs/                   # architecture, contract, deployment, logging, PRDs
├── scripts/ci.sh           # local CI (lint → test → build)
├── AGENTS.md               # ⭐ workspace navigation for AI agents (read before editing)
└── CHANGELOG.md            # version changelog
```

## 📚 Documentation Index

| Doc | Content |
|---|---|
| [`skill/SKILL.md`](skill/SKILL.md) | Agent manual (Chrome launch, sourcing, follow-selling, image search, batch) |
| [`docs/CONTRACT-v4.md`](docs/CONTRACT-v4.md) | Skill ↔ Worker API contract v4.0 (endpoints, envelope, error codes, node contract) |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | Full Worker deployment guide (Docker, Nginx, HTTPS, ops) |
| [`docs/WORKER-TOPOLOGY.md`](docs/WORKER-TOPOLOGY.md) | Worker topology + error mapping + data flow + code-change quick reference |
| [`docs/LOGGING.md`](docs/LOGGING.md) | Logging architecture + viewing commands + troubleshooting |
| [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) | Branch naming + commit conventions + release flow |
| [`AGENTS.md`](AGENTS.md) | Workspace navigation + recent updates + known pitfalls (read before editing code) |

## 🧪 Testing

```bash
# Worker unit tests (mock mode, no PG/GPU needed)
cd worker && PYTHONPATH=src python3 tests/test_full_pipeline_mock_images.py

# Worker full test suite (requires PG)
cd worker && PYTHONPATH=src python3 -m pytest tests/ -v

# Skill single-node smoke test
cd skill && python3.12 scripts/cli.py graph --url "<1688 URL>"
```

## 🔄 Versioning & Updates

- Version number: `VERSION` file (semver)
- Changelog: `CHANGELOG.md`
- Worker: upgrade via `deploy/update.sh` or `deploy/cos-update.sh`
- Skill: silently checks for new versions on every command; `SKILL_AUTO_UPDATE=0` reverts to manual

## ⚠️ Known Constraints

- **Single-product listing**: one 1688 item = one Ozon product card (variants are collapsed by the Skill layer)
- **Brand forced to "no brand"**: every product defaults to `Нет бренда`; brand names are not written
- **Description sanitization**: Latin/Chinese characters, URLs, phone numbers, and marketing words are stripped before upload
- **Hazardous-goods attributes**: required attr 9782 only ever receives "non-hazardous" safe defaults

## 📄 License

Private repository. Development conventions: [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md).
