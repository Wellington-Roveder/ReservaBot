import json
import logging
import os
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from openai import OpenAI

from repository import queries
from services.telemetry import calcular_custo_estimado

load_dotenv()
logger = logging.getLogger(__name__)

# Fuso do restaurante. No Windows, ZoneInfo precisa do pacote `tzdata` (pip install tzdata).
FUSO_RESTAURANTE = ZoneInfo("America/Sao_Paulo")

dias_semana = [
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
]


class OpenAISystem:
    def __init__(self):
        self.client = OpenAI(
            base_url=os.getenv("BASE_URL"), api_key=os.getenv("API_KEY")
        )
        self.model = os.getenv("MODEL_NAME")
        self.tools = self._carregar_tools()
        self.ultimo_trace: dict = {}

    def _carregar_tools(self):
        # Resolve o caminho do tools.json relativo à pasta do openai_service.py
        caminho_base = os.path.dirname(os.path.abspath(__file__))
        caminho_json = os.path.join(caminho_base, "tools.json")

        with open(caminho_json, "r", encoding="utf-8") as arquivo:
            return json.load(arquivo)

    def _montar_system_prompt(self) -> str:
        agora = datetime.now(FUSO_RESTAURANTE)
        dia_semana = dias_semana[agora.weekday()]
        data_hoje = f"{agora.strftime('%Y-%m-%d')} ({dia_semana})"

        return f"""
        Você é o assistente virtual de reservas de um restaurante.
        Data atual: {data_hoje}. Use esta data como referência para calcular datas relativas como 'hoje' ou 'amanhã'.

        Regras de atendimento:
        - Seu atendimento deve ser cordial e educado.
        - Funcionamos de terça a domingo, das 18:00 às 02:00 (horários entre 00:00 e 01:59 pertencem ao encerramento do expediente noturno e são válidos).
        - Antes de chamar 'criar_reserva', sempre consulte 'buscar_mesas_livres' primeiro.
        - Se o cliente não informar telefone, nome, data, horário ou número de pessoas, peça os dados faltantes antes de executar a reserva.
        - Ao chamar 'buscar_mesas_livres' ou 'criar_reserva' para horários entre 00:00 e 01:59, passe exatamente a data do dia informado pelo cliente (ex: se o cliente pedir "amanhã às 00:30", passe a data de amanhã no formato AAAA-MM-DD; o sistema já trata a virada de dia internamente).

        Regras Inegociáveis:
        - Nunca oferecer promessas de descontos, nem discutir valores.
        - Nunca fazer avaliações financeiras sobre o cardápio.
        - Nunca cobrar ou dizer que a reserva tem algum custo ou taxa.

        DIRETRIZ DE SEGURANÇA E PRIVACIDADE PARA CONSULTA E CANCELAMENTO:
        1. NUNCA revele dados completos (como nome completo ou ID interno) logo após chamar 'buscar_reservas_cliente'.
        2. Sempre peça confirmação de titularidade antes de agir:
           - Se retornar 1 reserva: peça para o cliente confirmar o nome cadastrado ou a data/horário antes de confirmar detalhes ou cancelar (ex: "Encontrei uma reserva para este número. Para confirmar, em qual nome ela foi feita?").
           - Se retornar mais de 1 reserva: liste apenas a data, horário e quantidade de pessoas de cada uma, sem expor os nomes, e pergunte qual delas pertence ao cliente e qual o nome do titular (ex: "Encontrei duas reservas: dia 10/12 às 20:00 (4 pessoas) e dia 15/12 às 19:30 (2 pessoas). Qual delas é a sua e qual o nome registrado?").
        3. Só execute 'cancelar_reserva' após o cliente confirmar explicitamente qual reserva deseja cancelar E o nome informado bater com o registro da reserva escolhida.
        """

    def _registrar_ultimo_trace(
        self,
        trace_id: str,
        inicio_turno: float,
        iteracoes: int,
        tools_executadas: list[dict],
        prompt_tokens: int,
        completion_tokens: int,
        limite_atingido: bool = False,
    ) -> None:
        latencia_ms = round((time.perf_counter() - inicio_turno) * 1000, 2)
        total_tokens = prompt_tokens + completion_tokens
        self.ultimo_trace = {
            "trace_id": trace_id,
            "modelo": self.model,
            "iteracoes_llm": iteracoes,
            "tools_acionadas": tools_executadas,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "custo_estimado_usd": calcular_custo_estimado(
                self.model, prompt_tokens, completion_tokens
            ),
            "latencia_llm_ms": latencia_ms,
            "limite_iteracoes_atingido": limite_atingido,
        }

    def gerar_resposta(
        self, historico_mensagem: list, trace_id: str | None = None
    ) -> str:
        inicio_turno = time.perf_counter()
        trace_id = trace_id or str(uuid.uuid4())[:8]

        mapa_funcoes = {
            "listar_mesas": queries.listar_mesas,
            "buscar_mesas_livres": queries.buscar_mesas_livres,
            "criar_reserva": queries.criar_reserva,
            "buscar_reservas_cliente": queries.buscar_reservas_cliente,
            "cancelar_reserva": queries.cancelar_reserva,
        }

        mensagens_envio = [
            {"role": "system", "content": self._montar_system_prompt()}
        ] + historico_mensagem

        max_iteracoes = 5
        iteracoes = 0
        prompt_tokens_total = 0
        completion_tokens_total = 0
        tools_executadas: list[dict] = []

        while iteracoes < max_iteracoes:
            iteracoes += 1

            response = self.client.chat.completions.create(
                model=self.model,
                messages=mensagens_envio,
                tools=self.tools,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=500,
            )

            usage = getattr(response, "usage", None)
            if usage is not None:
                prompt_tokens_total += int(getattr(usage, "prompt_tokens", 0) or 0)
                completion_tokens_total += int(
                    getattr(usage, "completion_tokens", 0) or 0
                )

            mensagem_ia = response.choices[0].message

            if not mensagem_ia.tool_calls:
                self._registrar_ultimo_trace(
                    trace_id=trace_id,
                    inicio_turno=inicio_turno,
                    iteracoes=iteracoes,
                    tools_executadas=tools_executadas,
                    prompt_tokens=prompt_tokens_total,
                    completion_tokens=completion_tokens_total,
                )
                return mensagem_ia.content

            mensagens_envio.append(mensagem_ia)

            for tool_call in mensagem_ia.tool_calls:
                name = tool_call.function.name
                funcao = mapa_funcoes.get(name)
                inicio_tool = time.perf_counter()
                sucesso_tool = True

                # Qualquer falha vira um resultado de erro devolvido ao modelo,
                # que pode se corrigir, em vez de derrubar o chat.
                try:
                    if not funcao:
                        sucesso_tool = False
                        resultado = {"erro": f"Função '{name}' não encontrada."}
                    else:
                        args = json.loads(tool_call.function.arguments)
                        resultado = funcao(**args)
                        if (
                            isinstance(resultado, dict)
                            and resultado.get("status") == "erro"
                        ):
                            sucesso_tool = False
                except json.JSONDecodeError:
                    sucesso_tool = False
                    resultado = {
                        "erro": f"Argumentos de '{name}' em JSON inválido. Tente novamente."
                    }
                except TypeError as e:
                    sucesso_tool = False
                    resultado = {"erro": f"Argumentos incorretos para '{name}': {e}"}
                except Exception:
                    sucesso_tool = False
                    logger.exception("Falha ao executar a tool '%s'", name)
                    resultado = {"erro": f"Falha interna ao executar '{name}'."}

                duracao_tool_ms = round((time.perf_counter() - inicio_tool) * 1000, 2)
                tools_executadas.append(
                    {
                        "nome": name,
                        "duracao_ms": duracao_tool_ms,
                        "sucesso": sucesso_tool,
                    }
                )

                mensagens_envio.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": name,
                        "content": json.dumps(resultado, ensure_ascii=False),
                    }
                )

        self._registrar_ultimo_trace(
            trace_id=trace_id,
            inicio_turno=inicio_turno,
            iteracoes=iteracoes,
            tools_executadas=tools_executadas,
            prompt_tokens=prompt_tokens_total,
            completion_tokens=completion_tokens_total,
            limite_atingido=True,
        )
        return "Desculpe, atingi o limite de tentativas para processar sua solicitação. Pode tentar novamente?"
