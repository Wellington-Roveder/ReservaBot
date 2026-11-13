"""Mostra, para cada frase, os 3 exemplos do intents.json mais parecidos e o score.

Rodar da raiz do projeto:  python diagnostico_nlu.py
"""

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.getcwd())
from services.nlu_config.nlu import SemanticNLU  # noqa: E402

FRASES = [
    "qual o horário da minha reserva?",
    "qual o tempo em são paulo?",
    "quem ganhou o jogo ontem?",
    "me conta uma piada",
    "qual a capital da França?",
]

n = SemanticNLU()
caminho = os.path.join("services", "nlu_config", "intents.json")
with open(caminho, encoding="utf-8") as f:
    dados = json.load(f)

for frase in FRASES:
    v = n._normalizar_l2(np.array(list(n.model.embed([frase.lower()]))[0]))
    ranking = []
    for intent, exemplos in dados.items():
        scores = n.routes[intent] @ v
        for ex, s in zip(exemplos, scores):
            ranking.append((float(s), intent, ex))
    ranking.sort(reverse=True)
    print(f"\n{frase}   ->  {n.classificar(frase)}")
    for s, intent, ex in ranking[:3]:
        print(f"  {s:.3f}  {intent:<26} <- {ex}")
