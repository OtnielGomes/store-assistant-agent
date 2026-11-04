Português: [README.pt-BR.md](./README.pt-BR.md)

# store-assistant-agent

Virtual customer-service assistant for Empório da Música, a musical-instrument store in Campo Grande (MS), Brazil. Domain vocabulary is in [GLOSSARY.md](./GLOSSARY.md) and architecture decisions in [docs/adr/](./docs/adr/).

## Clone and run

You need Git and Docker with Compose. For API tests on the host, Python 3.13 and [uv](https://docs.astral.sh/uv/). For the web app outside Compose, Node.js and pnpm 10.15.1.

```bash
git clone https://github.com/OtnielGomes/store-assistant-agent.git
cd store-assistant-agent
cp .env.example .env
```

On PowerShell, copy the env file with `Copy-Item .env.example .env`.

Put a real `OPENAI_API_KEY` in `.env` before expecting a live reply. LangSmith tracing follows `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, and `LANGSMITH_PROJECT`. `.env.example` lists every setting. Model defaults are `gpt-6-luna`, reasoning effort `none`, and embeddings `text-embedding-3-small`. Temperature is sent only when `OPENAI_TEMPERATURE` is set, because `gpt-6-luna` rejects `0`. Startup fails when `OPENAI_TEMPERATURE` is set and `OPENAI_REASONING_EFFORT` is not `none`.

Start PostgreSQL, data load, the API, and the web app:

```bash
docker compose up --build
```

Open http://127.0.0.1:3000 and chat with Lia. The API listens at http://127.0.0.1:8000 (`GET /health`, `POST /chat`). To stop, run `docker compose down` at the repository root.

Compose publishes Postgres on `127.0.0.1:54330` and overrides `DATABASE_URL` inside the API container to use the `db` service. Host API tests use that published database and a scripted chat model, so they do not call OpenAI:

```bash
cd api
DATABASE_URL=postgresql://lia:lia@127.0.0.1:54330/lia uv sync
DATABASE_URL=postgresql://lia:lia@127.0.0.1:54330/lia uv run ruff check src tests
DATABASE_URL=postgresql://lia:lia@127.0.0.1:54330/lia uv run pytest
```

On PowerShell, from `api/`:

```powershell
$env:DATABASE_URL = "postgresql://lia:lia@127.0.0.1:54330/lia"
uv sync
uv run ruff check src tests
uv run pytest
```

For the web app, from `web/`:

```bash
pnpm install
pnpm lint
pnpm build
pnpm typecheck
```

## Orders to look up status

Lia only discloses an order with its number and the last four digits of the registered phone. These five cover all five statuses.

| Order | Status | Customer | Last 4 digits |
|---|---|---|---|
| 1 | Delivered | Pedro Henrique Oliveira | 1098 |
| 8 | Shipped | Ana Carolina Ferreira | 7821 |
| 9 | Confirmed | Juliana Almeida Rodrigues | 5678 |
| 13 | Pending | Fernanda Ribeiro Souza | 7890 |
| 17 | Cancelled | Matheus Ramos Correia | 8765 |

Example: "Qual o status do pedido 8? Os quatro últimos dígitos são 7821."

## Data

`data/` holds material supplied by the store:

- Six CSVs (`products`, `categories`, `orders`, `order_items`, `customers`, `promotions`): the facts, loaded into PostgreSQL.
- `políticas_da_loja.pdf`: the internal policy manual (version 2.1, June 2025): the policies.

Original files are never edited. Every adjustment below happens at load time.

### Date shift (+224 days)

The CSVs are a snapshot taken on Monday 2026-03-23 (orders from 2025-10-15 through 2026-03-22). Load adds 224 days (exactly 32 weeks, so weekdays stay aligned) to every date column: `orders.order_date`, `orders.estimated_delivery`, and `products.created_at`. After the shift, the snapshot is Monday 2026-11-02 and orders run from 2026-05-27 through 2026-11-01. See [ADR-0003](./docs/adr/0003-shift-csv-dates-to-present.md).

### Known anomalies

These inconsistencies are kept on purpose. They reproduce situations a real store faces, and the assistant never fixes or explains them on her own: an agent resolves them through a handoff. Dates below are already shifted.

| # | Anomaly | Records |
|---|---|---|
| 1 | Coming-soon product present in orders | Product 130 in order 3 (delivered) and order 14 (pending) |
| 2 | Discontinued product with units in stock | Product 136 (13 units) |
| 3 | Shipped order with delivery estimate already past | Order 8 (shipped 2026-09-27, estimate 2026-10-10) |
| 4 | Campo Grande orders with 10–15 day estimate while the manual promises 1–3 business days in the metro area | Orders 1, 2, 3, 4, 5, 7, 8 |
| 5 | Categories the manual says the store sells but that have no products | Categories 6, 7, and 8 (wind and orchestral strings), treated as temporarily unstocked categories |
| 6 | Black Friday promotion below the manual’s 15–30% band (inactive) | Promotion 1 (10%) |
| 7 | Same product with more than one promotion (all inactive) | Products 93, 96, and 99 |
| 8 | Manual lists two phones and a misspelled e-mail | Section 1.2 lists (67) 3341-4444 as "Telefone / WhatsApp" and `contato@empóriodamusica.com.br`; section 7 gives (67) 3321-4500 as the WhatsApp channel |

Product 96 (active, zero stock) is not an anomaly: it is legitimately unavailable at the moment.

Order total is the amount recorded on the order. The assistant states that amount and does not compare it to item prices or the PIX discount.

For anomaly 8, the assistant tells the customer only WhatsApp (67) 3321-4500 and e-mail `contato@emporiodamusica.com.br`.

## Golden-set evaluations

`evals/golden/v1.jsonl` is the source of truth: 41 customer conversations (11 catalog/price/availability, 5 promotions, 8 orders, 8 policies, 5 handoffs, 4 out of scope). Each line records the customer message or messages, the frozen instant (default: Tuesday 2026-11-03 10:00 in America/Campo_Grande; the after-hours case defines its own), and expected outcomes: tool, required values in the reply, whether a handoff must be created, and that the reply must not contain phone or e-mail.

A live run uploads that file to the `lia-golden-v1` LangSmith dataset (replacing remote examples), calls OpenAI once per case, and records an experiment. Deterministic checks cover tool, required values, handoff, contact data, and plain text (no markdown and no mention of section or manual). An LLM judge scores correctness and tone in Portuguese. The command prints passed cases, the three hard gates, and the experiment URL.

The verdict passes only when at least 37 of 41 cases pass and there is no wrong price, personal-data leak, or missed mandatory handoff. Otherwise the command exits non-zero. Wrong price is a value forbidden by the case (for example, a recalculated order total or the catalog price of a coming-soon product) or some other money amount stated in place of a required price. Phone or e-mail anywhere in the reply, including the store’s own contact, is a personal-data leak. `ci.yml` does not call OpenAI. `.github/workflows/evals.yml` runs this command on manual dispatch, with repository secrets `OPENAI_API_KEY` and `LANGSMITH_API_KEY`.

From `api/`, with Postgres reachable at `DATABASE_URL` and real keys in the environment:

```bash
uv run python -m lia_api.evals
```

`uv run pytest tests/test_golden_set.py` checks the file, deterministic evaluators, and verdict math without calling OpenAI or LangSmith.

### Three live experiments

The same set of 41 questions was run three times in LangSmith with `uv run python -m lia_api.evals` from `api/`. The verdict target is 37/41, with no wrong price, no personal-data leak, and no missed mandatory handoff.

| Experiment | What changed | Passed |
|---|---|---|
| `lia-golden-v1-555ac1bc` | Agent development. Consultation simulations, including price. | 10/41 |
| `lia-golden-v1-319b3b13` | Prompt, chat flows, and Lia’s language adjusted. | 35/41 |
| `lia-golden-v1-571301ac` | Further fix, again on the same 41 questions. | 40/41 |

The first experiment did not pass. On the chart, the Portuguese judge sat near 30%. The next two runs rose to 35 and then 40 passed, above the target of 37.

![Comparação dos três experimentos no LangSmith](images/Compara%C3%A7%C3%A3o%20dos%203%20experimetos%20do%20langsmith.png)

![Terceiro experimento, com as barras de feedback mais altas](images/3%20experimeto%20prototipofinal.png)

## Demo

The conversation below was captured in the local UI with simulated time.

![Saudação da Lia](images/Sauda%C3%A7%C3%A3o.png)

![Violões disponíveis até R$ 1.000](images/exemplo%20de%20pergunta%201.png)

![Endereço da loja](images/exemplo%20de%20pergunta%202.png)

![Preço do Takamine GD20 e promoção encerrada](images/exemplo%20pergunta%203.png)

![Prazo de devolução por arrependimento](images/exemplo%20de%20pergunta%204.png)

![Consulta do pedido 8 com os quatro últimos dígitos](images/exemplo%20pergunta%205.png)
