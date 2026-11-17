# ADR 0003: Avaliação ponta a ponta do agente

- **Status:** aceito
- **Data:** 2026-11-17

## Contexto

No [ADR 0001](0001-avaliacao-do-roteador-semantico.md), avaliei isoladamente o roteador semântico local (`SemanticNLU`) e comprovei que o limiar `0.50` atinge 86,2% de acerto na decisão de escopo. O roteador, porém, é só o filtro inicial. Calcular datas relativas, normalizar telefones, encadear `buscar_mesas_livres` antes de `criar_reserva` e respeitar os guardrails do [ADR 0002](0002-revisao-de-seguranca.md) são tarefas do loop de *tool calling* executado pelo `OpenAISystem`.

Os testes unitários com mocks (`tests/test_openai_service.py`) validam o encanamento do código, mas não medem se o LLM escolhido extrai os parâmetros corretos nem se evita inventar chamadas de ferramentas em cenários ambíguos ou adversariais.

## Decisão

Implementei uma suíte de avaliação ponta a ponta, determinística e desacoplada do banco de produção. O dataset (`benchmarks/eval_agent_cases.json`) reúne 16 cenários: criação completa em turno único, fluxo multi-turno, regra de madrugada (00:00 a 01:59), normalização de telefone (`+55` e máscaras), consulta de reservas, cancelamento seguro em dois passos, bloqueio de duplicidade e de expediente fechado, e resistência a *prompt injection*.

O script `benchmarks/eval_agent.py` aponta o `SqlitePool` (`repository/database.py`) para `:memory:` a cada caso, inicializa as tabelas, as 13 mesas padrão e o *seed* do cenário, e nunca toca no `ReservasMesa.db`. Um *context manager* envolve as funções de `repository/queries.py` para registrar o histórico exato de chamadas e argumentos gerados pelo LLM, enquanto a execução real acontece no SQLite em memória, o que permite validar o efeito colateral persistido.

Para eliminar falhas que dependam de quando o benchmark roda, o *harness* congela o relógio em quarta-feira, `2026-11-18 14:00` (`America/Sao_Paulo`), tanto no *system prompt* quanto nas validações de `queries.py`.

## Métricas

A *Tool Selection Accuracy* mede o percentual de cenários em que o agente chamou a sequência esperada de ferramentas sem invocar nenhuma proibida, como criar reserva sem os dados obrigatórios ou cancelar sem confirmação de titularidade. A *Argument Extraction Accuracy* mede o percentual de cenários com extração estruturada em que o agente converteu corretamente data (`AAAA-MM-DD`), horário (`HH:MM`), quantidade de pessoas e telefone normalizado. A *Task Completion Rate* mede o percentual de cenários de agendamento válido em que a reserva foi de fato gravada na tabela `reservas` do SQLite em memória.

## Como executar

```bash
python -m benchmarks.eval_agent
```

O relatório completo da execução fica em `benchmarks/resultados_eval_agent.txt`.

## Resultados

A avaliação foi executada com o modelo `nvidia/nemotron-3-ultra-550b-a55b:free`. A saída da última execução está em `benchmarks/resultados_eval_agent.txt`.

| Métrica | Casos | Taxa |
| --- | --- | --- |
| Tool Selection Accuracy | 15/16 | 93,75% |
| Argument Extraction Accuracy | 11/12 | 91,67% |
| Task Completion Rate | 5/6 | 83,33% |

Os guardrails de segurança tiveram aderência total. O agente recusou chamadas de ferramentas diante de *prompt injection* (TC15), pedidos de desconto ou taxa (TC14) e expediente fechado (TC07 e TC08). Também bloqueou o cancelamento direto sem confirmação de titularidade (TC10) e só executou `cancelar_reserva` depois da confirmação explícita no segundo turno (TC11).

A extração e o encadeamento também funcionaram bem. O modelo chamou `buscar_mesas_livres` antes de `criar_reserva` em todos os fluxos de criação aprovados, normalizou corretamente telefones com `+55` (TC02) e converteu datas no formato DD/MM e horários por extenso (TC16).

A única falha foi o TC03, a reserva na madrugada às 00:30. Ao receber "amanhã à 00:30", o agente não chamou nenhuma ferramenta. A instrução atual do *system prompt* diz que reservas que viram o dia (entre 00:00 e 02:00) contam como dia anterior, e isso deixou o modelo em dúvida sobre qual data enviar ou se o horário estaria fora do expediente.

## Consequências e próximos passos

Vou refinar o *system prompt* em `services/openai_service.py` para deixar explícito que horários entre 00:00 e 01:59 pertencem ao encerramento do expediente noturno e devem receber exatamente a data informada pelo cliente, já que `queries._instante()` trata a virada de dia no backend.

Diferente do `eval_nlu.py`, que é local e instantâneo, esta suíte faz chamadas reais à API do LLM. Por isso ela deve rodar sob demanda, antes de releases ou trocas de modelo, e não a cada commit no CI.