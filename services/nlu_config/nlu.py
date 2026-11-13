import json
import os

import numpy as np
from fastembed import TextEmbedding


class SemanticNLU:
    def __init__(self, threshold: float = 0.65):
        self.threshold = threshold
        # Modelo multilíngue leve (~120MB) otimizado para similaridade semântica
        self.model = TextEmbedding(
            model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
        self.routes = self._carregar_rotas()

    @staticmethod
    def _normalizar_l2(vetores: np.ndarray) -> np.ndarray:
        normas = np.linalg.norm(vetores, axis=-1, keepdims=True)
        return vetores / np.maximum(normas, 1e-9)

    def _carregar_rotas(self) -> dict:
        # Resolve o caminho do intents.json relativo à pasta deste arquivo
        caminho_base = os.path.dirname(os.path.abspath(__file__))
        caminho_json = os.path.join(caminho_base, "intents.json")

        with open(caminho_json, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)

        rotas_vetorizadas = {}
        for intent, exemplos in dados.items():
            # Converte a lista de frases em matriz (N_exemplos x 384) e normaliza
            matriz = np.array(list(self.model.embed(exemplos)))
            rotas_vetorizadas[intent] = self._normalizar_l2(matriz)

        return rotas_vetorizadas

    def classificar(self, texto_usuario: str) -> dict:
        texto_limpo = texto_usuario.strip().lower()
        if not texto_limpo:
            return {"intent": "fora_de_escopo", "confidence": 0.0, "in_scope": False}

        # Pega o índice [0] para shape (384,) e aplica normalização L2
        vetor_bruto = np.array(list(self.model.embed([texto_limpo]))[0])
        vetor_usuario = self._normalizar_l2(vetor_bruto)

        melhor_intent = "fora_de_escopo"
        maior_score = -1.0

        for intent, matriz_exemplos in self.routes.items():
            scores = np.dot(matriz_exemplos, vetor_usuario)
            score_maximo_intent = float(np.max(scores))

            if score_maximo_intent > maior_score:
                maior_score = score_maximo_intent
                if score_maximo_intent >= self.threshold:
                    melhor_intent = intent

        confidence_final = max(0.0, min(1.0, maior_score))

        return {
            "intent": melhor_intent,
            "confidence": round(confidence_final, 4),
            "in_scope": melhor_intent != "fora_de_escopo",
        }
