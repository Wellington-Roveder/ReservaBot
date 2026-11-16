# ADR 0002: Revisão de segurança e riscos aceitos no demo público

- **Status:** aceito (atualizado em 2026-11-16 com mitigação conversacional)
- **Data:** 2026-11-14

## Contexto

O ReservaBot é um projeto de portfólio com demo público no Streamlit Cloud e código aberto no GitHub. O demo foi pensado para ser testado por qualquer visitante, sem login. Antes de divulgá-lo, fiz uma revisão de segurança para verificar se havia segredos expostos, avaliar como o acesso às reservas está desenhado e auditar as dependências.

## Verificações realizadas

A revisão automatizada do código (um subagente com as skills security-audit e security-review) apontou dois achados de severidade alta, descritos abaixo, e não encontrou nenhum segredo completo, chave privada ou token conhecido. O gitleaks, executado em modo verboso sobre 21 commits (cerca de 130 KB), também não encontrou vazamentos. O pip-audit não encontrou vulnerabilidades conhecidas nas dependências do projeto.

## Achados

**1. Reservas visíveis na interface pública (severidade alta, confiança 10/10).** Em `app.py`, linhas 146 a 157, as reservas aparecem na interface pública sem autenticação ou autorização. O risco já está parcialmente mitigado para a LGPD no demo.

**2. Consulta por telefone sem verificação (severidade alta, confiança 9/10).** A consulta por telefone retorna todas as reservas vinculadas ao número, expondo os IDs, de modo que basta conhecer o telefone para acessar e cancelar reservas. O problema está distribuído em `services/tools.json` (linhas 83 a 119), `repository/queries.py` (linhas 258 a 316) e `services/openai_service.py` (linhas 66 a 112).

## Decisão

Os dois achados vêm de uma escolha de projeto: o ReservaBot é um demo público, sem autenticação de usuários. Mantive o desenho e aceitei os riscos residuais de forma consciente, aplicando camadas de mitigação proporcionais ao escopo do portfólio.

No achado 1, o risco é de privacidade, caso um visitante insira dados pessoais reais. Para adequar o demo às boas práticas da LGPD sem exigir login, apliquei três medidas: um aviso na interface orientando o uso exclusivo de dados fictícios, o mascaramento do telefone (mantendo os quatro últimos dígitos) e do sobrenome na tabela pública, e uma rotina que remove automaticamente os registros de teste após 24 horas. Com essas camadas, o risco restante foi aceito.

No achado 2, inicialmente o cancelamento dependia apenas da posse do telefone. Posteriormente, para evitar exposição indevida de dados e cancelamentos acidentais ou não autorizados sem introduzir complexidade de backend (como OTP ou login), adicionei uma camada de mitigação no comportamento do agente e nos metadados das ferramentas (detalhada abaixo).

Nenhum segredo foi encontrado, nem pela revisão do código nem pelo gitleaks. As credenciais BASE_URL, API_KEY e MODEL_NAME ficam nos secrets do Streamlit Cloud e em variáveis de ambiente locais, fora do repositório.

## Mitigação aplicada ao Achado 2 (2026-11-16)

Para reduzir o risco de engenharia social simples e vazamento de informações na consulta por telefone sem alterar a arquitetura do banco de dados, foram aplicadas três medidas combinadas:

- **Bloqueio de exposição direta (Prompt Engineering):** Em `services/openai_service.py`, o agente foi instruído a nunca exibir nomes ou IDs internos logo após consultar `buscar_reservas_cliente`. Quando há múltiplas reservas para o mesmo telefone, o agente lista apenas dados parciais (data, horário e quantidade de pessoas).
- **Verificação de titularidade conversacional:** Antes de detalhar ou executar `cancelar_reserva`, o agente exige que o usuário confirme a data/horário específico e o nome cadastrado na reserva, atuando como um segundo fator leve baseado em conhecimento.
- **Restrições nos esquemas de função (Tool Metadata):** Em `services/tools.json`, as descrições de `buscar_reservas_cliente` e `cancelar_reserva` foram reforçadas para proibir a execução automática do cancelamento em caso de ambiguidade ou ausência de confirmação explícita do titular.
- **Risco residual aceito:** Como o controle depende da aderência do LLM às instruções do prompt e não de uma trava determinística no backend (que continuaria vulnerável a chamadas diretas ou prompt injection avançado), o risco residual permanece aceito estritamente para o ambiente de demonstração.

## Limitações da revisão

A revisão automatizada serve como apoio e não substitui uma revisão manual. A varredura do gitleaks cobriu os 21 commits do clone local. A auditoria de dependências reflete a base de vulnerabilidades conhecida no momento da execução e deve ser repetida sempre que as dependências mudarem.

## O que mudaria em produção

- Autenticação do cliente pelo próprio canal de atendimento, como o WhatsApp, ou por código de confirmação enviado ao telefone.
- Validação determinística no backend exigindo correspondência de dados adicionais (como nome ou código de confirmação) antes de retornar ou alterar registros, sem depender apenas de guardrails de prompt.
- Identificadores de reserva não sequenciais e com expiração.
- Limite de tentativas e mensagens de erro idênticas para ID inexistente e telefone incorreto.
- Painel de reservas restrito a funcionários autenticados.
- Registro das tentativas de acesso, com alerta para padrões suspeitos.
- Tratamento de dados pessoais conforme a LGPD, com coleta mínima, prazo de retenção e mascaramento em logs.
