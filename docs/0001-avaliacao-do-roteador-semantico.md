# ADR 0001: Avaliação do roteador semântico e escolha do limiar

- **Status:** aceito
- **Data:** 2026-11-14

## Contexto

O ReservaBot usa um roteador semântico local (`fastembed` com `paraphrase-multilingual-MiniLM-L12-v2`) para decidir, na primeira mensagem, se o pedido está no escopo de reservas antes de chamar o LLM. O limiar inicial foi `0.65`, uma estimativa conservadora. Nos primeiros testes manuais em português, entradas válidas apresentaram similaridade entre 0.52 e 0.62 e uma mensagem sem relação com o domínio ficou em torno de 0.35, o que levou ao valor `0.50`.

Esses testes manuais usaram frases diretas e eram poucos. Faltava uma medida reproduzível para confirmar o limiar e identificar onde o roteador erra.

## Decisão

- Avaliar o roteador isoladamente, sem chamadas ao LLM, em um conjunto rotulado (`benchmarks/eval.csv`) com o script `benchmarks/eval_nlu.py`.
- Manter o limiar em `0.50`, de forma provisória.
- Corrigir as intenções fracas ampliando e diversificando os exemplos de `intents.json`, em vez de mover o limiar global.
- Reavaliar após essa mudança com um conjunto novo de mensagens, para evitar ajuste ao conjunto atual.

## Conjunto de teste

São 80 mensagens em português, escritas manualmente por uma só pessoa e rotuladas com a intenção esperada: 40 dentro do escopo (`fazer_reserva` 8, `alterar_cancelar_reserva` 8, `consultar_reserva` 8, `duvidas_gerais` 6, `fornecer_dados` 6 e `saudacao` 4) e 40 fora do escopo (`fora_de_escopo`), incluindo pedidos sem relação com o restaurante e tentativas de sobrescrever as instruções do assistente.

As mensagens válidas foram escritas como paráfrases, evitando repetir as frases de exemplo de `intents.json`. Nenhuma mensagem do conjunto é igual a um exemplo. Entre as mensagens fora do escopo, porém, três são variações próximas de exemplos existentes ("cotação do euro" e "cotação do dólar", "molho branco" e "bolo de chocolate", "capital da Argentina" e "capital do Brasil"), o que torna esses bloqueios mais fáceis.

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

No limiar `0.50`, 36 das 40 mensagens fora do escopo foram bloqueadas e 33 das 40 mensagens válidas foram aprovadas. O limiar inicial de `0.65` teria bloqueado 21 mensagens válidas e acertado só 73,8% do escopo.

O acerto por intenção no limiar `0.50` foi:

| Intenção                   | Acertos |
| -------------------------- | ------- |
| `fora_de_escopo`           | 36/40   |
| `alterar_cancelar_reserva` | 6/8     |
| `consultar_reserva`        | 5/8     |
| `saudacao`                 | 3/4     |
| `fornecer_dados`           | 3/6     |
| `fazer_reserva`            | 1/8     |
| `duvidas_gerais`           | 0/6     |

Com 80 mensagens, cada mensagem vale 1,25 ponto percentual, e o resultado de 86,2% tem margem de erro de cerca de 7 pontos para mais ou para menos.

## Escolha do limiar

Entre os limiares avaliados, `0.50` teve o maior acerto de escopo. O benchmark mostrou, porém, que as distribuições se sobrepõem: mensagens válidas com score entre 0.27 e 0.47 foram bloqueadas, e mensagens fora do escopo com score entre 0.51 e 0.57 foram aprovadas.

Os dois erros não custam o mesmo. Bloquear um pedido válido faz o cliente receber uma recusa e a reserva se perde. Aprovar uma mensagem fora do escopo custa uma chamada à API e ainda encontra as camadas seguintes. Por esse critério, `0.45` seria um candidato, pois reduz os falsos negativos de 7 para 4, ao custo de 4 falsos positivos a mais.

O limiar foi mantido em `0.50` de forma provisória, por dois motivos: a diferença entre `0.45` e `0.50` é de uma única mensagem no acerto de escopo (85,0% contra 86,2%), dentro da margem de erro de um conjunto deste tamanho; e a causa principal dos falsos negativos está nos exemplos das intenções, não no limiar global, que afeta todas as classes. Se os falsos negativos persistirem depois de ampliar os exemplos, `0.45` será reavaliado.

## Camadas após o roteador

O roteador é um filtro de custo e de telemetria, não uma camada de segurança. Mensagens que passam por ele ainda encontram:

- o system prompt, que define o papel de assistente de reservas e traz regras inegociáveis: não prometer descontos nem discutir valores, não fazer avaliações financeiras sobre o cardápio e não cobrar nem informar custo ou taxa pela reserva;
- o tool calling limitado às cinco ferramentas de reserva descritas em `services/tools.json`;
- as regras de negócio validadas em `repository/queries.py` (fuso, janela de duas horas, capacidade e bloqueio de duplicidade).

No conjunto de teste, 5 das 6 tentativas de sobrescrever as instruções foram bloqueadas pelo roteador e 1 foi aprovada (score 0.57). Em testes manuais, um pedido de desconto acompanhado da instrução de ignorar as regras foi recusado pelo LLM.

## Análise dos erros

**Mensagens válidas bloqueadas (7 falsos negativos).** Três são perguntas reais de cliente sobre o restaurante (estacionamento, cachorro e opções vegetarianas), duas são de `fornecer_dados` (um nome e o nome de uma empresa), uma é de `fazer_reserva` ("um cantinho perto da janela") e uma é de `consultar_reserva` (agendamento feito pelo WhatsApp).

**Mensagens fora do escopo aprovadas (4 falsos positivos).** Receita de lasanha (0.51), apostas (0.55), tradução de parágrafo (0.56) e um pedido de senha de administrador acompanhado de instrução para ignorar as regras (0.57).

**Intenções com baixo acerto.** `fazer_reserva` (1/8) e `duvidas_gerais` (0/6) tiveram o pior desempenho. A inspeção de `intents.json` é consistente com as hipóteses, que serão confirmadas na reavaliação:

- Os 10 exemplos de `fazer_reserva` são frases diretas ("quero reservar uma mesa"). Os erros dessa intenção são pedidos indiretos ("segurar um lugar", "separar uma mesa", "deixar uma mesa preparada"), e quatro dos sete foram classificados como `alterar_cancelar_reserva`. Uma exceção ainda sem explicação: "Gostaria de fazer uma reserva para comemorar meu aniversário", que é direta e foi para `consultar_reserva` (score 0.61).
- Os 9 exemplos de `duvidas_gerais` tratam só de horário de funcionamento, endereço e custo da reserva. As perguntas do teste (estacionamento, crianças, cachorro, cardápio vegetariano, regra de atraso, escolha do local da mesa) não têm exemplo equivalente.

**O score não é uma confiança.** Algumas intenções erradas tiveram score alto (de 0.68 a 0.81). O score é a similaridade com o exemplo mais próximo: serve para decidir o escopo, mas não mede a certeza da intenção. Errar a intenção não interrompe o fluxo, porque as ferramentas são escolhidas pelo LLM. Em testes manuais com cinco mensagens de intenção classificada incorretamente, quatro passaram pelo gatekeeper e foram atendidas corretamente. A quinta, "Dá para reservar um cantinho perto da janela?" (score 0.40), foi bloqueada e recebeu a resposta padrão de fora do escopo.

## Consequências e limitações

- O conjunto tem de 4 a 8 mensagens por intenção; uma única mensagem altera de 12 a 25 pontos percentuais no acerto daquela intenção. Os resultados por intenção são indicativos.
- O mesmo conjunto foi usado para comparar limiares e medir o desempenho, o que tende a deixar os números otimistas.
- O conjunto foi escrito por uma só pessoa, a mesma que definiu as intenções, e três mensagens fora do escopo são variações próximas de exemplos existentes.
- Pelo menos um rótulo é discutível ("Vocês trabalham com reserva antecipada?", rotulada como `fazer_reserva`). O conjunto fica congelado como v1; correções de rótulo, se houver, entram no conjunto novo.
- O gatekeeper atua apenas na primeira mensagem. Mensagens curtas de meio de fluxo, como o envio de um nome ou telefone (`fornecer_dados`), não passam por ele em uso normal. Excluindo essa intenção, o acerto de escopo em `0.50` seria de 87,8% (65 de 74 mensagens).
- Mensagens válidas formuladas de modo indireto podem ser bloqueadas e recebem uma resposta padrão genérica.
- Em testes manuais, as mensagens bloqueadas responderam em cerca de 0,1 s, e as que chamaram o LLM levaram de 4 a 14 s. O tempo de resposta é dominado pelo provedor do modelo, não pelo roteador.

## Próximos passos

- Ampliar e diversificar os exemplos de `intents.json`: pedidos indiretos em `fazer_reserva` e mais assuntos em `duvidas_gerais` (estacionamento, crianças, animais, cardápio, atraso, escolha de mesa). Os exemplos novos devem ser frases novas, **não** as mensagens do conjunto atual, para não contaminar a reavaliação.
- Reavaliar com um conjunto novo de mensagens e registrar o resultado em um novo ADR. Meta: reduzir os falsos negativos e melhorar `fazer_reserva` e `duvidas_gerais`, sem mudar o limiar na mesma rodada.
- Avaliar uma base de conhecimento do restaurante (RAG) para as perguntas sobre estacionamento, cardápio e regras, que o roteador por exemplos tende a bloquear.