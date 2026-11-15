# ADR 0002: Revisão de segurança e riscos aceitos no demo público

- **Status:** aceito
- **Data:** 2026-11-14

## Contexto

O ReservaBot é um projeto de portfólio com demo público no Streamlit Cloud e código aberto no GitHub. O demo foi projetado para ser testado por qualquer visitante, sem login. Antes de divulgá-lo, foi feita uma revisão de segurança para verificar a existência de segredos expostos, avaliar o desenho de acesso às reservas e auditar as dependências.

## Verificações realizadas

| Verificação | Ferramenta | Resultado |
| --- | --- | --- |
| Revisão de segurança do código | Subagente de revisão automatizada, security-audit e security-review | Dois achados de severidade alta, descritos abaixo. Nenhum segredo completo, chave privada ou token conhecido encontrado |
| Segredos no histórico do Git | gitleaks detect, modo verboso | 21 commits analisados, cerca de 130 KB, nenhum vazamento encontrado |
| Auditoria de dependências | pip-audit | Nenhuma vulnerabilidade conhecida encontrada nas dependências do projeto |

## Achados

| # | Severidade | Arquivos | Descrição | Confiança |
| --- | --- | --- | --- | --- |
| 1 | Alta | `app.py`, linhas 146 a 157 | Reservas completas visíveis na interface pública, sem autenticação ou autorização | 10/10 |
| 2 | Alta | `services/tools.json`, linhas 83 a 119; `repository/queries.py`, linhas 258 a 316; `services/openai_service.py`, linhas 66 a 112 | Consulta e cancelamento autorizados apenas por telefone e ID da reserva, o que permite acesso e cancelamento indevidos | 9/10 |

## Decisão

Os dois achados decorrem de uma escolha de projeto: o ReservaBot é um demo público e demonstrativo, sem autenticação de usuários. O desenho foi mantido e os riscos foram aceitos de forma consciente.

**Achado 1.** A exposição das reservas na interface é um risco de privacidade, pois um visitante pode digitar dados pessoais reais que ficam visíveis para os demais. O telefone é mascarado na tabela, que exibe apenas os quatro últimos dígitos. Com essa mascaragem, o risco restante foi aceito para o demo.

**Achado 2.** O controle por ID e telefone é o mecanismo de acesso escolhido para o demo. Ele limita o acesso a quem conhece os dois dados, mas não identifica o usuário e, portanto, não equivale a autenticação.

**Segredos.** Nenhum segredo foi encontrado pela revisão do código nem pelo gitleaks. As credenciais BASE_URL, API_KEY e MODEL_NAME ficam nos secrets do Streamlit Cloud e em variáveis de ambiente locais, fora do repositório.

## Limitações da revisão

- A revisão automatizada é um apoio e não substitui uma revisão manual.
- A varredura do gitleaks cobriu os 21 commits do clone local.
- A auditoria de dependências reflete a base de vulnerabilidades conhecida no momento da execução e deve ser repetida quando as dependências mudarem.

## O que mudaria em produção

- Autenticação do cliente pelo próprio canal de atendimento, como o WhatsApp, ou código de confirmação enviado ao telefone.
- Identificadores de reserva não sequenciais e com expiração.
- Limite de tentativas e mensagens de erro iguais para ID inexistente e telefone incorreto.
- Painel de reservas restrito a funcionários autenticados.
- Registro de tentativas de acesso e alerta para padrões suspeitos.
- Tratamento de dados pessoais conforme a LGPD, com coleta mínima, prazo de retenção e mascaramento em logs.
