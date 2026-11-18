# ReservaBot

[![CI](https://github.com/Wellington-Roveder/ReservaBot/actions/workflows/ci.yml/badge.svg)](https://github.com/Wellington-Roveder/ReservaBot/actions/workflows/ci.yml)

Agente conversacional para gestão de reservas de restaurante. O sistema permite **criar, consultar e cancelar reservas** por linguagem natural, com persistência em SQLite e observabilidade em tempo real da classificação semântica, das chamadas ao LLM, do custo estimado e do estado do banco.

[Acessar a demonstração ao vivo](https://reservas-wellingtonroveder.streamlit.app/)

## Visão geral

O ReservaBot combina um roteador semântico local com um modelo compatível com a API da OpenAI. Mensagens inicialmente fora do domínio de reservas são bloqueadas antes de uma chamada ao LLM. Quando a intenção está no escopo, ou quando já existe um fluxo multi-turno em andamento, o agente usa chamadas de ferramentas para consultar mesas, validar regras e operar as reservas.

## Arquitetura

```text
Usuário
  │
  ▼
Streamlit (app.py)
  │
  ├─ SemanticNLU
  │    ├─ Embeddings locais via fastembed
  │    ├─ Similaridade de cosseno com normalização L2
  │    └─ Gatekeeper de escopo e telemetria de intenção/confiança
  │
  ├─ OpenAISystem
  │    ├─ System prompt com data e dia da semana atuais
  │    ├─ Guardrails de atendimento e regras de negócio
  │    ├─ Tool calling carregado de services/tools.json
  │    ├─ Loop iterativo limitado para encadear ferramentas
  │    └─ Trace por turno (iterações, tokens e duração das ferramentas)
  │           │
  │           ▼
  │    repository/queries.py
  │           │
  │           ▼
  │    SQLite (ReservasMesa.db)
  │
  └─ Telemetry (services/telemetry.py)
       ├─ Custo estimado por tokens de entrada e saída
       └─ Logs estruturados em logs/traces.jsonl
```

### Gatekeeper semântico

`services/nlu_config/nlu.py` usa `fastembed` com o modelo `paraphrase-multilingual-MiniLM-L12-v2`. Os exemplos de intenção são transformados em embeddings e normalizados por L2; a classificação compara o embedding da mensagem com esses exemplos por produto escalar, equivalente à similaridade de cosseno após a normalização.

O limiar padrão do roteador é `0.50`. Na primeira mensagem, uma classificação fora do escopo gera uma resposta padrão sem consumir uma chamada à API do LLM. Em mensagens posteriores, o gatekeeper pode ser ignorado para preservar fluxos multi-turno já iniciados. A aplicação exibe a intenção, o score de confiança, o status do gatekeeper e a latência na barra lateral. A escolha do limiar e o desempenho do roteador estão descritos em [Avaliação do roteador](#avaliação-do-roteador).

### Orquestração com tool calling

`services/openai_service.py` integra o SDK da OpenAI com um endpoint compatível, como o OpenRouter, permitindo configurar o modelo por variáveis de ambiente. O `system_prompt` é montado dinamicamente com a data e o dia da semana no fuso `America/Sao_Paulo`.

As ferramentas descritas em `services/tools.json` cobrem listagem de mesas, busca de disponibilidade, criação, consulta e cancelamento de reservas. O serviço executa um loop `while` com limite de cinco iterações: cada resposta do modelo pode solicitar uma ou mais ferramentas, cujos resultados são devolvidos ao modelo até que uma resposta textual seja produzida ou o limite seja atingido.

O prompt também contém guardrails de operação, incluindo o funcionamento de terça a domingo, das 18:00 às 02:00, a consulta de mesas livres antes da criação de uma reserva e a solicitação de dados obrigatórios antes da execução.

A cada turno, o serviço acumula os tokens de todas as iterações e cronometra cada ferramenta, expondo o resultado em `ultimo_trace`. A seção [Observabilidade](#observabilidade) descreve como esses dados são exibidos e registrados.

### Dados e regras de negócio

`repository/` encapsula a persistência e as queries de negócio:

- SQLite com conexões por thread e context manager para commit/rollback.
- Datas e horários interpretados no fuso `America/Sao_Paulo`.
- Expediente de terça a domingo, das 18:00 às 02:00, incluindo horários de madrugada.
- Horários entre 00:00 e 01:59 pertencem ao dia do expediente informado e são tratados como o dia seguinte no instante real.
- Janela estrita de duas horas entre reservas; reservas exatamente a duas horas de distância podem ser encostadas.
- Validação de capacidade, formato de data/horário e horário já passado.
- Normalização de telefone, aceitando formatos com pontuação e código do país.
- Bloqueio de reservas simultâneas para o mesmo telefone.
- Cancelamento condicionado ao ID da reserva e ao telefone do titular.

### Observabilidade

`app.py` implementa a interface em Streamlit. Além do chat, a barra lateral mostra:

- intenção detectada e score de confiança;
- indicação de passagem, bloqueio ou bypass do gatekeeper;
- latência da solicitação;
- `trace_id`, número de iterações do loop, tokens consumidos e custo estimado em USD;
- status e tempo de cada ferramenta executada;
- reservas presentes no SQLite, com telefone mascarado para exibição.

Cada turno também grava uma linha JSON em `logs/traces.jsonl`, com metadados operacionais e sem texto livre nem dados pessoais. O custo é calculado em `services/telemetry.py`, com sobrescrita por `PRICE_INPUT_PER_M` e `PRICE_OUTPUT_PER_M` no `.env` e tarifa zero para modelos `:free`. Decisão, resultados e limitações em [ADR 0004: Tracing estruturado e observabilidade](docs/adr/0004-tracing-estruturado-e-observabilidade.md).

## Avaliação do roteador

O roteador foi avaliado em 80 mensagens rotuladas (40 dentro e 40 fora do escopo), sem chamadas ao LLM. No limiar `0.50`:

- **86,2% de acerto na decisão de escopo:** 36 de 40 mensagens fora do escopo bloqueadas e 33 de 40 mensagens válidas aprovadas;
- **67,5% de acerto de intenção**, com desempenho fraco em pedidos indiretos (`fazer_reserva`, 1/8) e dúvidas gerais (`duvidas_gerais`, 0/6). Como o LLM escolhe as ferramentas, esse erro não interrompe o fluxo;
- o roteador funciona como filtro de custo e telemetria. O system prompt, o tool calling restrito e as validações de `repository/queries.py` seguem como camadas seguintes.

Conjunto de teste, metodologia, tabela de limiares, erros e limitações em [ADR 0001: Avaliação do roteador semântico](docs/adr/0001-avaliacao-do-roteador-semantico.md).

## Avaliação do agente

O agente foi avaliado ponta a ponta em 16 cenários, com chamadas reais ao LLM (`nvidia/nemotron-3-ultra-550b-a55b:free`), SQLite em memória e relógio congelado. Resultados:

- **93,75% de acerto na seleção de ferramentas** (15 de 16 casos);
- **91,67% de acerto na extração de argumentos** (11 de 12 casos);
- **83,33% de conclusão de tarefas** (5 de 6 reservas gravadas);
- os guardrails bloquearam *prompt injection*, pedidos de desconto, expediente fechado e cancelamento sem confirmação de titularidade. A única falha foi a reserva às 00:30 (TC03), em que o agente não chamou nenhuma ferramenta por ambiguidade na regra de madrugada do *system prompt*.

Por usar a API real, a suíte roda sob demanda, antes de releases ou trocas de modelo, e não a cada commit:

```bash
python -m benchmarks.eval_agent
```

Cenários, métricas e análise das falhas em [ADR 0003: Avaliação ponta a ponta do agente](docs/adr/0003-avaliacao-ponta-a-ponta-agente.md).

## Estrutura do projeto

```text
.
├── app.py                              # Interface Streamlit e fluxo de atendimento
├── benchmarks/
│   ├── eval_nlu.py                     # Avaliação do roteador semântico
│   ├── eval.csv                        # 80 mensagens rotuladas
│   ├── resultados.txt                  # Saída da avaliação do roteador
│   ├── eval_agent.py                   # Avaliação ponta a ponta do agente (LLM Eval)
│   ├── eval_agent_cases.json           # 16 cenários estruturados de teste do agente
│   └── resultados_eval_agent.txt       # Saída da avaliação do agente
├── docs/
│   └── adr/
│       ├── 0001-avaliacao-do-roteador-semantico.md
│       ├── 0002-revisao-de-seguranca.md
│       ├── 0003-avaliacao-ponta-a-ponta-agente.md
│       └── 0004-tracing-estruturado-e-observabilidade.md
├── repository/
│   ├── database.py                     # Pool simples de conexões SQLite por thread
│   ├── queries.py                      # Schema, seed, validações e operações de reserva
│   └── __init__.py
├── services/
│   ├── openai_service.py               # Cliente compatível com OpenAI e loop de tools
│   ├── telemetry.py                    # Cálculo de custo e logs estruturados (JSONL)
│   ├── tools.json                      # Contratos das funções disponíveis ao modelo
│   ├── nlu_config/
│   │   ├── nlu.py                      # Classificador semântico local
│   │   └── intents.json                # Exemplos de intenções
│   └── __init__.py
├── tests/
│   ├── conftest.py                     # Fixtures dos testes
│   ├── test_nlu.py                     # Testes do roteador semântico
│   ├── test_openai_service.py          # Testes do loop e das falhas de tools
│   ├── test_queries.py                 # Testes das regras e queries SQLite
│   └── test_telemetry.py               # Testes de precificação e logs JSONL
├── assets/
│   └── icone.png                       # Ícone usado pela interface
├── .env.example                        # Exemplo de configuração local
├── requirements.txt                    # Dependências Python
└── LICENSE                             # Licença MIT
```

## Qualidade e testes

A suíte automatizada usa `pytest` e cobre:

- classificação de intenções, limiar e mensagens fora do escopo;
- orquestração do serviço de IA, encadeamento de ferramentas e tratamento de erros;
- queries SQLite, validações de horários, madrugada, capacidade, telefones, conflitos e cancelamentos;
- precificação de tokens, tratamento de variáveis vazias no `.env` e escrita JSONL sem duplicação de handlers.

Execute os testes com:

```bash
pytest
```

As avaliações do roteador e do agente são executadas separadamente, conforme descrito em [Avaliação do roteador](#avaliação-do-roteador) e [Avaliação do agente](#avaliação-do-agente).

## Execução local

### 1. Clonar o repositório

```bash
git clone https://github.com/Wellington-Roveder/ReservaBot
cd ReservaBot
```

### 2. Criar e ativar um ambiente virtual

Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Instalar as dependências

```bash
pip install -r requirements.txt
```

### 4. Configurar as variáveis de ambiente

Copie `.env.example` para `.env` e preencha o endpoint e as credenciais do provedor compatível com a API da OpenAI:

```dotenv
BASE_URL=
API_KEY=
MODEL_NAME=
```

`DATABASE_NAME` também pode ser configurado para definir o arquivo SQLite; o exemplo usa `ReservasMesa.db`.

`PRICE_INPUT_PER_M` e `PRICE_OUTPUT_PER_M` são opcionais e sobrescrevem a tarifa de referência usada na estimativa de custo.

### 5. Executar a aplicação

```bash
streamlit run app.py
```

Na inicialização, as tabelas de mesas e reservas são criadas caso não existam, e as mesas são populadas apenas quando o cadastro ainda está vazio.

## Segurança

O histórico do repositório foi verificado com gitleaks, as dependências com pip-audit e o código passou por uma revisão automatizada de segurança. Os dois achados de severidade alta decorrem de o demo ser público e sem login, e os riscos foram aceitos de forma consciente. Detalhes em [ADR 0002: Revisão de segurança](docs/adr/0002-revisao-de-seguranca.md).

## Licença

Este projeto está disponível sob a licença [MIT](LICENSE).