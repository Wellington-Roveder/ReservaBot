import json
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from openai import OpenAI

from repository import queries

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
        -Seu atendimento deve ser cordial e educado
        - Funcionamos de terça a domingo, das 18:00 às 02:00.
        - Antes de chamar 'criar_reserva', sempre consulte 'buscar_mesas_livres' primeiro.
        - Se o cliente não informar telefone, nome, data, horário ou número de pessoas, peça os dados faltantes antes de executar a reserva.
        -O Dia da Reserva sempre sera o primeiro horario, reservas que viram o dia contam como dia anterior
        Regras Inegociaveis:
        -Nunca Oferecer promessas de descontos.Nem discutir valores.
        -Nunca Fazer avaliaçoes financeiras sobre o cardapio
        -Nunca cobrar ou dizer que a reserva tem algum custo ou taxa
        """

    def gerar_resposta(self, historico_mensagem: list) -> str:
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
            mensagem_ia = response.choices[0].message

            if not mensagem_ia.tool_calls:
                return mensagem_ia.content

            mensagens_envio.append(mensagem_ia)

            for tool_call in mensagem_ia.tool_calls:
                name = tool_call.function.name
                funcao = mapa_funcoes.get(name)

                # Qualquer falha vira um resultado de erro devolvido ao modelo,
                # que pode se corrigir, em vez de derrubar o chat.
                try:
                    if not funcao:
                        resultado = {"erro": f"Função '{name}' não encontrada."}
                    else:
                        args = json.loads(tool_call.function.arguments)
                        resultado = funcao(**args)
                except json.JSONDecodeError:
                    resultado = {
                        "erro": f"Argumentos de '{name}' em JSON inválido. Tente novamente."
                    }
                except TypeError as e:
                    resultado = {"erro": f"Argumentos incorretos para '{name}': {e}"}
                except Exception:
                    logger.exception("Falha ao executar a tool '%s'", name)
                    resultado = {"erro": f"Falha interna ao executar '{name}'."}

                mensagens_envio.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": name,
                        "content": json.dumps(resultado, ensure_ascii=False),
                    }
                )

        return "Desculpe, atingi o limite de tentativas para processar sua solicitação. Pode tentar novamente?"
