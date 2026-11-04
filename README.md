# store-assistant-agent

Assistente virtual de atendimento do Empório da Música, loja de instrumentos musicais em Campo Grande (MS). O vocabulário do domínio está em [GLOSSARY.md](./GLOSSARY.md) e as decisões de arquitetura em [docs/adr/](./docs/adr/).

## Como copiar e rodar

É preciso Git e Docker com Compose. Para os testes da API no host, Python 3.13 e [uv](https://docs.astral.sh/uv/). Para a aplicação web fora do Compose, Node.js e pnpm 10.15.1.

```bash
git clone https://github.com/OtnielGomes/store-assistant-agent.git
cd store-assistant-agent
cp .env.example .env
```

No PowerShell, a cópia do ambiente é `Copy-Item .env.example .env`.

Coloque uma `OPENAI_API_KEY` real no `.env` antes de esperar uma resposta ao vivo. O rastreamento no LangSmith segue `LANGSMITH_TRACING`, `LANGSMITH_API_KEY` e `LANGSMITH_PROJECT`. O `.env.example` lista todas as configurações. Os padrões do modelo são `gpt-6-luna`, esforço de raciocínio `none` e embeddings `text-embedding-3-small`. A temperatura só é enviada quando `OPENAI_TEMPERATURE` está definida, porque o `gpt-6-luna` rejeita `0`. A inicialização falha quando `OPENAI_TEMPERATURE` está definida e `OPENAI_REASONING_EFFORT` não é `none`.

Suba o PostgreSQL, a carga dos dados, a API e a aplicação web:

```bash
docker compose up --build
```

Abra http://127.0.0.1:3000 e converse com a Lia. A API escuta em http://127.0.0.1:8000 (`GET /health`, `POST /chat`). Para encerrar, `docker compose down` na raiz do repositório.

O Compose publica o Postgres em `127.0.0.1:54330` e substitui `DATABASE_URL` dentro do container da API para usar o serviço `db`. Os testes da API no host usam esse banco publicado e um modelo de chat roteirizado, então não chamam a OpenAI:

```bash
cd api
DATABASE_URL=postgresql://lia:lia@127.0.0.1:54330/lia uv sync
DATABASE_URL=postgresql://lia:lia@127.0.0.1:54330/lia uv run ruff check src tests
DATABASE_URL=postgresql://lia:lia@127.0.0.1:54330/lia uv run pytest
```

No PowerShell, a partir de `api/`:

```powershell
$env:DATABASE_URL = "postgresql://lia:lia@127.0.0.1:54330/lia"
uv sync
uv run ruff check src tests
uv run pytest
```

A aplicação web, a partir de `web/`:

```bash
pnpm install
pnpm lint
pnpm build
pnpm typecheck
```

## Pedidos para consultar o status

A Lia só informa o pedido com o número e os quatro últimos dígitos do telefone cadastrado. Estes cinco cobrem os cinco status.

| Pedido | Status | Cliente | Últimos 4 dígitos |
|---|---|---|---|
| 1 | Entregue | Pedro Henrique Oliveira | 1098 |
| 8 | Enviado | Ana Carolina Ferreira | 7821 |
| 9 | Confirmado | Juliana Almeida Rodrigues | 5678 |
| 13 | Pendente | Fernanda Ribeiro Souza | 7890 |
| 17 | Cancelado | Matheus Ramos Correia | 8765 |

Exemplo: "Qual o status do pedido 8? Os quatro últimos dígitos são 7821."

## Dados

`data/` guarda o material fornecido pela loja:

- Seis CSVs (`products`, `categories`, `orders`, `order_items`, `customers`, `promotions`): os fatos, carregados no PostgreSQL.
- `políticas_da_loja.pdf`: o manual interno de políticas (versão 2.1, junho de 2025): as políticas.

Os arquivos originais nunca são editados. Todo ajuste abaixo acontece na carga dos dados.

### Deslocamento das datas (+224 dias)

Os CSVs são um retrato tirado na segunda-feira 2026-03-23 (pedidos feitos de 2025-10-15 a 2026-03-22). A carga soma 224 dias (exatamente 32 semanas, então os dias da semana se preservam) a toda coluna de data: `orders.order_date`, `orders.estimated_delivery` e `products.created_at`. Depois do deslocamento, o retrato é a segunda-feira 2026-11-02 e os pedidos vão de 2026-05-27 a 2026-11-01. Veja o [ADR-0003](./docs/adr/0003-shift-csv-dates-to-present.md).

### Anomalias conhecidas

Estas inconsistências são mantidas de propósito. Elas reproduzem situações que uma loja real enfrenta, e a assistente nunca as corrige nem as explica por conta própria: um atendente as resolve por um encaminhamento. As datas abaixo já estão deslocadas.

| # | Anomalia | Registros |
|---|---|---|
| 1 | Produto em breve presente em pedidos | Produto 130 no pedido 3 (entregue) e no pedido 14 (pendente) |
| 2 | Produto descontinuado com unidades em estoque | Produto 136 (13 unidades) |
| 3 | Pedido enviado com a previsão de entrega já passada | Pedido 8 (enviado em 2026-09-27, previsão 2026-10-10) |
| 4 | Pedidos de Campo Grande com previsão de 10 a 15 dias, enquanto o manual promete 1 a 3 dias úteis na região metropolitana | Pedidos 1, 2, 3, 4, 5, 7, 8 |
| 5 | Categorias que o manual diz que a loja vende, mas que não têm produtos | Categorias 6, 7 e 8 (sopros e cordas orquestrais), tratadas como categorias temporariamente sem produtos |
| 6 | Promoção de Black Friday abaixo da faixa de 15–30% do manual (inativa) | Promoção 1 (10%) |
| 7 | Mesmo produto com mais de uma promoção (todas inativas) | Produtos 93, 96 e 99 |
| 8 | O manual lista dois telefones e um e-mail com erro de grafia | A seção 1.2 lista (67) 3341-4444 como "Telefone / WhatsApp" e `contato@empóriodamusica.com.br`; a seção 7 indica (67) 3321-4500 como o canal de WhatsApp |

O produto 96 (ativo, estoque zero) não é uma anomalia: é o produto legitimamente indisponível no momento.

O total do pedido é o valor registrado nele. A assistente informa esse valor e não o compara com os preços dos itens nem com o desconto do PIX.

Na anomalia 8, a assistente informa ao cliente apenas o WhatsApp (67) 3321-4500 e o e-mail `contato@emporiodamusica.com.br`.

## Avaliações do conjunto de ouro

`evals/golden/v1.jsonl` é a fonte da verdade: 41 conversas de clientes (11 de catálogo/preço/disponibilidade, 5 de promoções, 8 de pedidos, 8 de políticas, 5 de encaminhamentos, 4 fora de escopo). Cada linha registra a mensagem ou as mensagens do cliente, o instante congelado (padrão: terça-feira 2026-11-03 10:00 em America/Campo_Grande; o caso fora do expediente define o seu) e os resultados esperados: ferramenta, valores obrigatórios na resposta, se um encaminhamento deve ser criado, e que a resposta não pode conter telefone nem e-mail.

A execução ao vivo envia esse arquivo para o conjunto de dados `lia-golden-v1` no LangSmith (substituindo os exemplos remotos), chama a OpenAI uma vez por caso e registra um experimento. As verificações determinísticas cobrem a ferramenta, os valores obrigatórios, o encaminhamento, os dados de contato e o texto simples (sem markdown e sem menção a seção ou ao manual). Um juiz de LLM avalia correção e tom em português. O comando imprime os casos aprovados, as três travas rígidas e a URL do experimento.

O veredito só passa quando pelo menos 37 dos 41 casos passam e não há preço errado, vazamento de dado pessoal nem encaminhamento obrigatório perdido. Caso contrário, o comando termina com código diferente de zero. Preço errado é um valor proibido pelo caso (por exemplo, um total de pedido recalculado ou o preço de catálogo de um produto em breve) ou algum outro valor em dinheiro dito no lugar de um preço obrigatório. Telefone ou e-mail em qualquer ponto da resposta, inclusive o contato da própria loja, é vazamento de dado pessoal. O `ci.yml` não chama a OpenAI. O `.github/workflows/evals.yml` roda este comando sob disparo manual, com os segredos `OPENAI_API_KEY` e `LANGSMITH_API_KEY` do repositório.

A partir de `api/`, com o Postgres acessível em `DATABASE_URL` e as chaves reais no ambiente:

```bash
uv run python -m lia_api.evals
```

`uv run pytest tests/test_golden_set.py` confere o arquivo, os avaliadores determinísticos e a conta do veredito sem chamar a OpenAI nem o LangSmith.

### Três experimentos ao vivo

O mesmo conjunto de 41 perguntas foi executado três vezes no LangSmith, com `uv run python -m lia_api.evals` a partir de `api/`. A meta do veredito é 37/41, sem preço errado, sem vazamento de dado pessoal e sem encaminhamento obrigatório perdido.

| Experimento | O que mudou | Aprovados |
|---|---|---|
| `lia-golden-v1-555ac1bc` | Desenvolvimento do agente. Simulações de consulta, inclusive preço. | 10/41 |
| `lia-golden-v1-319b3b13` | Ajuste do prompt, dos fluxos de chat e da linguagem da Lia. | 35/41 |
| `lia-golden-v1-571301ac` | Nova correção, de novo sobre as mesmas 41 perguntas. | 40/41 |

O primeiro experimento não passou. No gráfico, o juiz de português ficou perto de 30%. Os dois seguintes subiram para 35 e depois 40 aprovados, acima da meta de 37.

![Comparação dos três experimentos no LangSmith](images/Compara%C3%A7%C3%A3o%20dos%203%20experimetos%20do%20langsmith.png)

![Terceiro experimento, com as barras de feedback mais altas](images/3%20experimeto%20prototipofinal.png)

## Demonstração

A conversa abaixo foi feita na interface local, com horário simulado.

![Saudação da Lia](images/Sauda%C3%A7%C3%A3o.png)

![Violões disponíveis até R$ 1.000](images/exemplo%20de%20pergunta%201.png)

![Endereço da loja](images/exemplo%20de%20pergunta%202.png)

![Preço do Takamine GD20 e promoção encerrada](images/exemplo%20pergunta%203.png)

![Prazo de devolução por arrependimento](images/exemplo%20de%20pergunta%204.png)

![Consulta do pedido 8 com os quatro últimos dígitos](images/exemplo%20pergunta%205.png)
