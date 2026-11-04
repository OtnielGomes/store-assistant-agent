# store-assistant-agent

Customer-facing virtual assistant for Empório da Música, a musical instrument store in Campo Grande (MS). Domain vocabulary lives in [GLOSSARY.md](./GLOSSARY.md) and architectural decisions in [docs/adr/](./docs/adr/).

## Setup

Copy the sample environment and start PostgreSQL, the API, and the web app:

```bash
cp .env.example .env
docker compose up --build
```

Open http://127.0.0.1:3000 and talk to Lia. The API listens on http://127.0.0.1:8000 (`GET /health`, `POST /chat`).

`.env.example` lists every setting. Model defaults are `gpt-6-luna`, reasoning effort `none`, temperature `0`, and embeddings `text-embedding-3-small`. Startup fails when `OPENAI_TEMPERATURE` is set and `OPENAI_REASONING_EFFORT` is not `none`. Put a real `OPENAI_API_KEY` in `.env` before expecting a live reply. LangSmith tracing follows `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, and `LANGSMITH_PROJECT`.

Compose publishes Postgres on `127.0.0.1:54330` and overrides `DATABASE_URL` inside the API container so it uses the `db` service. Host-side API tests use the published database and a scripted chat model, so they do not call OpenAI:

```bash
cd api
DATABASE_URL=postgresql://lia:lia@127.0.0.1:54330/lia uv sync
DATABASE_URL=postgresql://lia:lia@127.0.0.1:54330/lia uv run ruff check src tests
DATABASE_URL=postgresql://lia:lia@127.0.0.1:54330/lia uv run pytest
```

The web app, from `web/`:

```bash
pnpm install
pnpm lint
pnpm build
pnpm typecheck
```

## Data

`data/` holds the material provided by the store:

- Six CSVs (`products`, `categories`, `orders`, `order_items`, `customers`, `promotions`): the Facts, loaded into PostgreSQL.
- `políticas_da_loja.pdf`: the internal policy manual (version 2.1, June 2025): the Policies.

The raw files are never edited. Every adjustment below happens when the data is loaded.

### Date shift (+224 days)

The CSVs are a snapshot taken on Monday 2026-03-23 (orders placed from 2025-10-15 to 2026-03-22). The loader adds 224 days (exactly 32 weeks, so weekdays are preserved) to every date column: `orders.order_date`, `orders.estimated_delivery`, and `products.created_at`. After the shift the snapshot is Monday 2026-11-02 and the orders span 2026-05-27 to 2026-11-01. See [ADR-0003](./docs/adr/0003-shift-csv-dates-to-present.md).

### Known data anomalies

These inconsistencies are kept on purpose. They reproduce situations a real store faces, and the Assistant never corrects or explains them on its own: an Attendant resolves them through a Handoff. Dates below are already shifted.

| # | Anomaly | Records |
|---|---|---|
| 1 | Coming-soon Product present in orders | Product 130 in order 3 (delivered) and order 14 (pending) |
| 2 | Discontinued Product with units in stock | Product 136 (13 units) |
| 3 | Shipped order past its estimated delivery | Order 8 (shipped 2026-09-27, estimated 2026-10-10) |
| 4 | Campo Grande orders estimated at 10–15 days, while the manual promises 1–3 business days in the metropolitan area | Orders 1, 2, 3, 4, 5, 7, 8 |
| 5 | Categories the manual says the store sells but that have no Products | Categories 6, 7, 8 (wind and orchestral strings), treated as Unstocked Categories |
| 6 | Black Friday promotion below the manual's 15–30% range (inactive) | Promotion 1 (10%) |
| 7 | Same Product with more than one promotion (all inactive) | Products 93, 96, 99 |
| 8 | Manual lists two phone numbers and a misspelled e-mail | Section 1.2 lists (67) 3341-4444 as "Telefone / WhatsApp" and `contato@empóriodamusica.com.br`; section 7 names (67) 3321-4500 as the WhatsApp channel |

Product 96 (active, zero stock) is not an anomaly: it is the catalog's legitimate temporarily unavailable Product.

An Order Total is the amount recorded on the Order. The Assistant reports that amount and does not compare it with item prices or with the PIX discount.

For anomaly 8, the Assistant gives customers only the WhatsApp channel (67) 3321-4500 and the e-mail `contato@emporiodamusica.com.br`.

## Golden set evals

`evals/golden/v1.jsonl` is the source of truth: 40 customer conversations (10 catalog/price/Availability, 5 Promotions, 8 Orders, 8 Policies, 5 Handoffs, 4 out of scope). Each line records the customer message or messages, the frozen instant (default Tuesday 2026-11-03 10:00 in America/Campo_Grande; the after-hours case sets its own), and the expected outcomes: tool, required reply values, whether a Handoff must be created, and that the reply must not contain a phone or e-mail.

The live run uploads that file to the LangSmith dataset `lia-golden-v1` (replacing remote examples), calls OpenAI once per case, and records one experiment. Deterministic checks cover the tool, the required values, the Handoff, and contact details. An LLM judge scores correctness and tone in Portuguese. The command prints passed cases, the three hard gates, and the experiment URL.

The verdict passes only when at least 36 of 40 cases pass and there are zero wrong prices, zero personal-data leaks, and zero missed mandatory Handoffs. Otherwise the command exits non-zero. A wrong price is a forbidden amount from the case (for example a recomputed Order Total or a Coming-soon catalog price) or some other money amount stated in place of a required price. A phone or e-mail anywhere in the reply, including the store's own contact, is a personal-data leak. `ci.yml` does not call OpenAI. `.github/workflows/evals.yml` runs this command on manual dispatch with the `OPENAI_API_KEY` and `LANGSMITH_API_KEY` repository secrets.

From `api/`, with Postgres reachable at `DATABASE_URL` and real keys in the environment:

```bash
uv run python -m lia_api.evals
```

`uv run pytest tests/test_golden_set.py` checks the file, the deterministic evaluators, and the verdict math without calling OpenAI or LangSmith.
