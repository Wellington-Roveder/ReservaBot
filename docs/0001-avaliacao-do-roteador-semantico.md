# ADR 0001: Avaliação do roteador semântico e escolha do limiar

- **Status:** aceito
- **Data:** 2026-11-14

## Contexto

O ReservaBot usa um roteador semântico local (`fastembed` com `paraphrase-multilingual-MiniLM-L12-v2`) para decidir, na primeira mensagem, se o pedido está no escopo de reservas antes de chamar o LLM. O limiar inicial foi `0.65`, uma estimativa conservadora. Nos primeiros testes manuais em português, entradas válidas apresentaram similaridade entre 0.52 e 0.62 e uma mensagem sem relação com o domínio ficou em torno de 0.35, o que levou ao valor `0.50`.

Faltava uma medida reproduzível para confirmar o limiar e identificar onde o roteador erra.

## Decisão

- Avaliar o roteador isoladamente, sem chamadas ao LLM, em um conjunto rotulado (`benchmarks/eval.csv`) com o script `benchmarks/eval_nlu.py`.
- Manter o limiar em `0.50`.
- Corrigir as intenções fracas ampliando e diversificando os exemplos de `intents.json`, em vez de mover o limiar global.
- Reavaliar após essa mudança com um conjunto novo de mensagens, para evitar ajuste ao conjunto atual.

## Conjunto de teste

São 80 mensagens em português, escritas manualmente e rotuladas com a intenção esperada: 40 dentro do escopo (`fazer_reserva` 8, `alterar_cancelar_reserva` 8, `consultar_reserva` 8, `duvidas_gerais` 6, `fornecer_dados` 6 e `saudacao` 4) e 40 fora do escopo (`fora_de_escopo`), incluindo pedidos sem relação com o restaurante e tentativas de sobrescrever as instruções do assistente. As mensagens válidas foram escritas como paráfrases, evitando repetir as frases de exemplo de `intents.json`.

## Metodologia

`benchmarks/eval_nlu.py` instancia o `SemanticNLU` com limiar `0` e aplica cada limiar sobre o score obtido, de modo que cada mensagem é processada uma única vez. São calculados:

- **acerto de intenção:** a intenção prevista coincide com o rótulo;
- **acerto de escopo:** a decisão de passar ou bloquear coincide com o rótulo;
- **falso positivo:** mensagem fora do escopo aprovada pelo gatekeeper;
- **falso negativo:** mensagem válida bloqueada pelo gatekeeper.

```bash
python -m benchmarks.eval_nlu benchmarks/eval.csv
```

A saída da última execução está em `benchmarks/resultados.txt`.

## Resultados

| Limiar | Acerto de intenção | Acerto de escopo | Falsos positivos | Falsos negativos |
| ------ | ------------------ | ---------------- | ---------------- | ---------------- |
| 0.30   | 51,2%              | 75,0%            | 18               | 2                |
| 0.35   | 56,2%              | 78,8%            | 14               | 3                |
| 0.40   | 61,3%              | 82,5%            | 10               | 4                |
| 0.45   | 63,7%              | 85,0%            | 8                | 4                |
| **0.50** | **67,5%**        | **86,2%**        | **4**            | **7**            |
| 0.55   | 66,2%              | 85,0%            | 2                | 10               |
| 0.60   | 66,2%              | 83,8%            | 0                | 13               |
| 0.65   | 61,3%              | 73,8%            | 0                | 21               |

No limiar `0.50`, 36 das 40 mensagens fora do escopo foram bloqueadas e 33 das 40 mensagens válidas foram aprovadas. O acerto por intenção nesse limiar foi:

| Intenção                   | Acertos |
| -------------------------- | ------- |
| `fora_de_escopo`           | 36/40   |
| `alterar_cancelar_reserva` | 6/8     |
| `consultar_reserva`        | 5/8     |
| `saudacao`                 | 3/4     |
| `fornecer_dados`           | 3/6     |
| `fazer_reserva`            | 1/8     |
| `duvidas_gerais`           | 0/6     |

## Escolha do limiar

Entre os limiares avaliados, `0.50` teve o maior acerto de escopo. O benchmark mostrou, porém, que as distribuições se sobrepõem: mensagens válidas com score entre 0.27 e 0.47 foram bloqueadas, e mensagens fora do escopo com score entre 0.51 e 0.57 foram aprovadas.

Bloquear um pedido válido é o erro mais caro, pois o cliente recebe uma recusa e a reserva se perde. Aprovar uma mensagem fora do escopo custa uma chamada à API e depende das camadas seguintes. Em `0.45`, há três falsos negativos a menos e quatro falsos positivos a mais, e a diferença no acerto de escopo é de uma única mensagem (85,0% contra 86,2%), dentro da variação esperada para um conjunto deste tamanho. O limiar foi mantido em `0.50`, e a correção planejada recai sobre os exemplos das intenções fracas, já que mover o limiar global afetaria todas as classes.

## Camadas após o roteador

O roteador é um filtro de custo e de telemetria, não uma camada de segurança. Mensagens que passam por ele ainda encontram:

- o system prompt, que define o papel de assistente de reservas e traz regras inegociáveis: não prometer descontos nem discutir valores, não fazer avaliações financeiras sobre o cardápio e não cobrar nem informar custo ou taxa pela reserva;
- o tool calling limitado às cinco ferramentas de reserva descritas em `services/tools.json`;
- as regras de negócio validadas em `repository/queries.py` (fuso, janela de duas horas, capacidade e bloqueio de duplicidade).

No conjunto de teste, 5 das 6 tentativas de sobrescrever as instruções foram bloqueadas pelo roteador e 1 foi aprovada (score 0.57). Em testes manuais, um pedido de desconto acompanhado da instrução de ignorar as regras foi recusado pelo LLM.

## Intenções com baixo acerto

`fazer_reserva` (1/8) e `duvidas_gerais` (0/6) tiveram o pior desempenho. As hipóteses, ainda não confirmadas, são:

- `fazer_reserva` reúne pedidos formulados de forma indireta ("segurar um lugar", "deixar uma mesa preparada"), com baixa similaridade em relação às frases diretas de exemplo. Quatro dos sete erros dessa intenção foram classificados como `alterar_cancelar_reserva`.
- `duvidas_gerais` agrupa assuntos distantes entre si (horário, endereço, taxas, cardápio, estacionamento). Como o roteador usa a maior similaridade contra cada exemplo, uma pergunta fora dos temas cobertos pelos exemplos fica com score baixo.

Errar a intenção não interrompe o fluxo, porque as ferramentas são escolhidas pelo LLM. Em testes manuais com cinco mensagens de intenção classificada incorretamente, quatro passaram pelo gatekeeper e foram atendidas corretamente. A quinta, "Dá para reservar um cantinho perto da janela?" (score 0.40), foi bloqueada e recebeu a resposta padrão de fora do escopo.

## Consequências e limitações

- O conjunto tem de 4 a 8 mensagens por intenção; uma única mensagem altera de 12 a 25 pontos percentuais no acerto daquela intenção. Os resultados por intenção são indicativos.
- O mesmo conjunto foi usado para comparar limiares e medir o desempenho, o que tende a deixar os números otimistas.
- O gatekeeper atua apenas na primeira mensagem. Mensagens curtas de meio de fluxo, como o envio de um nome ou telefone (`fornecer_dados`), não passam por ele em uso normal.
- Mensagens válidas formuladas de modo indireto podem ser bloqueadas e recebem uma resposta padrão genérica.
- Em testes manuais, as mensagens bloqueadas responderam em cerca de 0,1 s, e as que chamaram o LLM levaram de 4 a 14 s. O tempo de resposta é dominado pelo provedor do modelo, não pelo roteador.

## Próximos passos

- Ampliar e diversificar os exemplos de `intents.json` das intenções fracas.
- Reavaliar com um conjunto novo de mensagens e registrar o resultado em um novo ADR.
