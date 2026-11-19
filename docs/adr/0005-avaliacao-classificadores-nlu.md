# ADR 0005: Avaliação comparativa de classificadores NLU e decisão de runtime

- **Status:** aceito
- **Data:** 2026-11-19

## Contexto

O roteador semântico atual (`services/nlu_config/nlu.py`) compara embeddings do `paraphrase-multilingual-MiniLM-L12-v2` contra exemplos estáticos de `intents.json`, por similaridade de cosseno e com limiar `0.50`. A abordagem é leve e funciona, mas trata as 384 dimensões do vetor com o mesmo peso e rejeita mal as mensagens fora de escopo que contêm palavras-chave do domínio, como "reservar quarto de hotel". O desempenho do roteador no conjunto original está no [ADR 0001](0001-avaliacao-do-roteador-semantico.md).

Para saber se um treinamento supervisionado justificaria mudar a arquitetura de produção, conduzi um experimento controlado que compara três abordagens sobre um conjunto curado de 289 mensagens em português brasileiro.

## Metodologia

O script `benchmarks/nlu_experiment/preparar_dataset.py` parte de 305 exemplos brutos distribuídos em 5 classes: `fazer_reserva`, `consultar_reserva`, `cancelar_reserva`, `horario_funcionamento` e `fora_de_escopo`. Removi 11 exemplos com similaridade de cosseno igual ou superior a 0,92 em relação ao `intents.json`, para evitar *data leakage* que favorecesse o baseline, e mais 5 paráfrases quase idênticas dentro do próprio conjunto. Restaram 289 exemplos, divididos de forma estratificada e determinística (`seed=42`) em 202 para treino (70%), 43 para validação (15%) e 44 para teste cego (15%).

As três abordagens foram avaliadas no mesmo teste cego de 44 mensagens:

1. **Baseline (`SemanticNLU`):** similaridade de cosseno contra o `intents.json`, com `threshold=0.50`.
2. **`LogisticRegression` sobre embeddings congelados:** camada linear regularizada (L2, `C=1.0`, `class_weight="balanced"`) treinada sobre os vetores de 384 dimensões do MiniLM-L12. O limiar foi calibrado na validação (`threshold=0.35`).
3. **Fine-tuning completo do BERTimbau:** ajuste de todos os 110 milhões de parâmetros de `neuralmind/bert-base-portuguese-cased`, com 6 épocas, `lr=3e-5`, `warmup_steps=8` e `weight_decay=0.01`. O limiar foi calibrado pelo custo do erro, exigindo recall de pelo menos 0,85 em `fora_de_escopo` na validação, o que resultou em `threshold=0.40`.

## Resultados

| Abordagem | Accuracy | F1-Macro | Recall `fora_de_escopo` | Latência p50 (CPU) | Artefato |
| --- | --- | --- | --- | --- | --- |
| Baseline (`SemanticNLU`) | 79,55% | 0,8081 | 60,00% (9/15) | 9,41 ms | 0 KB (JSON) |
| `LogisticRegression` + MiniLM | 86,36% | 0,8698 | 80,00% (12/15) | 14,67 ms | 8,80 KB |
| BERTimbau com fine-tuning | 81,82% | 0,7964 | 100,00% (15/15) | 111,92 ms | 416,20 MB |

O recall do baseline em `fora_de_escopo` (60%) é bem menor que os 36 de 40 mensagens bloqueadas (90%) do [ADR 0001](0001-avaliacao-do-roteador-semantico.md). Os conjuntos são diferentes: o curado deste experimento inclui mensagens fora de escopo que reaproveitam o vocabulário do domínio, como "reservar quarto de hotel", que são justamente o ponto fraco da similaridade de cosseno com limiar fixo. Os dois resultados não são comparáveis diretamente.

A regressão logística teve a melhor accuracy e o melhor F1-Macro, por uma margem considerável sobre o baseline, e custou apenas 5 ms a mais por inferência. O BERTimbau foi o único a rejeitar todas as mensagens fora de escopo, mas ficou abaixo do baseline em F1-Macro, o que indica que o ajuste de 110 milhões de parâmetros com cerca de 200 exemplos de treino não se sustenta.

## Decisão

Descartei o BERTimbau para o runtime de produção. Com poucos dados, o fine-tuning completo piorou o F1-Macro (0,7964 contra 0,8698 da regressão logística), multiplicou a latência em CPU por 7,6 (111,92 ms contra 14,67 ms) e exigiria colocar PyTorch e Transformers no container, cerca de 2 GB de dependências mais 416,20 MB de pesos.

Mantive o motor atual, baseado em `fastembed` com o MiniLM-L12, sem alterar `services/nlu_config/nlu.py`. O classificador linear vencedor (8,80 KB, F1-Macro de 0,8698) e o dataset curado ficam no Hugging Face Hub como artefatos versionados e reproduzíveis, prontos para a migração quando houver mais de 1.000 interações reais rotuladas.

## Consequências e limitações

A decisão se apoia em métricas reprodutíveis, não gera regressão de latência nem custo extra de infraestrutura e deixa públicos o Dataset Card e o Model Card.

O conjunto de teste tem apenas 44 mensagens, e o recall de `fora_de_escopo` se baseia em 15 delas, então diferenças de um ou dois casos mexem bastante nos percentuais. O encoder congelado também é pouco sensível a abreviações informais extremas do português, como "qro reserva mesa hj pra 2". O fallback conversacional do agente cobre esse caso.