# ADR 0004: Tracing estruturado e observabilidade de custos

- **Status:** aceito
- **Data:** 2026-11-17

## Contexto

O painel lateral do ReservaBot exibia apenas a intenção detectada, a confiança do roteador semântico local e a latência total da solicitação. Quando o fluxo envolvia chamadas ao LLM e encadeamento de ferramentas no loop do `OpenAISystem`, eu não tinha visibilidade sobre quantas iterações aconteciam, quanto tempo o SQLite consumia frente à inferência remota, nem qual era o consumo de tokens e o custo estimado por interação.

Também faltava um registro persistente das execuções para auditar falhas e gargalos, sem complicar a aplicação em Streamlit e sem violar as regras de privacidade do [ADR 0002](0002-revisao-de-seguranca.md).

## Decisão

Implementei uma camada leve de observabilidade dividida em três partes: um módulo de telemetria (`services/telemetry.py`), a instrumentação do loop do agente (`services/openai_service.py`) e o painel na interface (`app.py`).

No módulo de telemetria, a função `calcular_custo_estimado` separa tokens de entrada (`prompt_tokens`) e de saída (`completion_tokens`), cobrados por milhão. A precificação segue três níveis de prioridade: sobrescrita explícita no `.env` (`PRICE_INPUT_PER_M` e `PRICE_OUTPUT_PER_M`), tarifa zero automática para modelos com sufixo `:free` e, por último, uma tabela interna de referência com fallback padrão de $0,15 / $0,60 por milhão.

Os logs são gravados em JSON Lines (`logs/traces.jsonl`) por um logger nativo isolado (`reservabot.traces`), com escrita *thread-safe* e uma trava contra duplicação de `FileHandler` nos *reruns* do Streamlit. Cada turno gera uma linha JSON independente com `trace_id`, intenção, decisão do gatekeeper, iterações do LLM, ferramentas acionadas com a duração de cada uma, tokens, custo estimado e latência total.

No `OpenAISystem`, o loop do `while` passou a acumular os tokens de todas as iterações e a cronometrar cada ferramenta com `time.perf_counter()`, expondo os metadados em `self.ultimo_trace`. O retorno textual de `gerar_resposta` não mudou, então os testes existentes continuam válidos. Na barra lateral do `app.py`, o novo painel mostra em tempo real o `trace_id`, o número de iterações, os tokens consumidos, o custo estimado em USD e o status e o tempo de cada ferramenta executada.

Os traces registram apenas metadados operacionais e métricas técnicas. Nenhum texto livre das mensagens nem dado pessoal (nome e telefone) vai para o `traces.jsonl`, o que mantém a política de retenção mínima do demo público e a conformidade com a LGPD.

## Resultados e observações

Nos testes locais com o modelo `nvidia/nemotron-3-ultra-550b-a55b:free`, a decomposição de latência e de tokens confirmou o comportamento esperado da arquitetura.

Em uma solicitação completa com consulta ao banco (`trace_id: 2c80d013`), a requisição levou 10.074 ms no total, dos quais 9.983 ms foram gastos nas chamadas ao LLM e apenas 0,97 ms na consulta `buscar_mesas_livres` no SQLite. O gargalo está na inferência remota, não na persistência.

Em 2 iterações do loop, o consumo foi de 3.966 `prompt_tokens` contra 434 `completion_tokens`. O reenvio do *system prompt*, do histórico e dos esquemas de ferramentas a cada iteração responde por cerca de 90% do volume de tokens.

Adicionei 7 testes unitários em `tests/test_telemetry.py`, que validam a prioridade de precificação, o tratamento de variáveis vazias no `.env` e a escrita JSONL sem duplicação de handlers.

## Consequências e limitações

O diretório `logs/` foi para o `.gitignore`. Em ambientes efêmeros como o Streamlit Cloud, o `traces.jsonl` persiste apenas durante o ciclo de vida do contêiner. Em produção, o mesmo formato pode ser redirecionado para um coletor centralizado, como OpenTelemetry, Datadog ou CloudWatch, ou configurado com `RotatingFileHandler`.

O custo estimado reflete tarifas públicas de referência por milhão de tokens. Ele serve para monitoramento comparativo e para detectar regressões de contexto, mas não substitui o faturamento oficial do provedor da API.