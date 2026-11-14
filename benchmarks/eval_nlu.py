"""Avalia o roteador semântico (SemanticNLU) em mensagens rotuladas.

Uso (na raiz do projeto):
    python benchmarks/eval_nlu.py benchmarks/eval.csv

CSV (UTF-8), duas colunas: mensagem, intenção esperada.
A intenção usa os nomes do intents.json; mensagens fora do domínio
são rotuladas como `fora_de_escopo`. Cabeçalho é opcional.
"""

import csv
import sys
from collections import Counter, defaultdict

from services.nlu_config.nlu import SemanticNLU

FORA = "fora_de_escopo"
LIMIARES = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]
CABECALHOS = {"intent", "intencao", "intenção", "label", "esperado", "rotulo", "rótulo"}
ALIASES_FORA = {
    "fora_do_escopo",
    "fora_de_escopo",
    "fora",
}  # o CSV pode usar qualquer grafia


def carregar(caminho):
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        texto = f.read()
    delim = (
        ";" if texto.splitlines()[0].count(";") >= 1 else ","
    )  # planilhas BR exportam com ;
    linhas = [
        r
        for r in csv.reader(texto.splitlines(), delimiter=delim)
        if len(r) >= 2 and r[0].strip()
    ]
    if linhas and linhas[0][1].strip().lower() in CABECALHOS:
        linhas = linhas[1:]
    return [
        (r[0].strip(), FORA if r[1].strip() in ALIASES_FORA else r[1].strip())
        for r in linhas
    ]


def prever(intent, score, limiar):
    return intent if score >= limiar else FORA


def main(caminho):
    dados = carregar(caminho)
    # threshold 0 => devolve sempre a melhor intenção + score;
    # o limiar é aplicado aqui, e cada mensagem é embutida uma única vez.
    nlu = SemanticNLU(threshold=0.0)
    res = []
    for msg, esperado in dados:
        out = nlu.classificar(msg)
        res.append((msg, esperado, out["intent"], out["confidence"]))

    cont = Counter(e for _, e, _, _ in res)
    print(f"{len(res)} mensagens")
    for k, v in sorted(cont.items()):
        print(f"  {k}: {v}")

    print(
        f"\n{'limiar':>6} {'acur.intenção':>14} {'acur.escopo':>12} {'falsos+':>8} {'falsos-':>8}"
    )
    print("  falso+ = fora do escopo que passou | falso- = mensagem válida bloqueada")
    for t in LIMIARES:
        ok_int = ok_esc = fp = fn = 0
        for _, esp, intent, score in res:
            pred = prever(intent, score, t)
            ok_int += pred == esp
            ok_esc += (pred != FORA) == (esp != FORA)
            fp += pred != FORA and esp == FORA
            fn += pred == FORA and esp != FORA
        n = len(res)
        print(f"{t:>6.2f} {ok_int / n:>14.1%} {ok_esc / n:>12.1%} {fp:>8} {fn:>8}")

    t = 0.50
    por_intent = defaultdict(lambda: [0, 0])
    for _, esp, intent, score in res:
        por_intent[esp][1] += 1
        por_intent[esp][0] += prever(intent, score, t) == esp
    print(f"\nAcerto por intenção (limiar {t:.2f}):")
    for k, (ok, n) in sorted(por_intent.items()):
        print(f"  {k}: {ok}/{n} ({ok / n:.0%})")

    print(f"\nErros no limiar {t:.2f}:")
    for msg, esp, intent, score in res:
        pred = prever(intent, score, t)
        if pred != esp:
            print(f"  esperado={esp} previsto={pred} score={score:.2f} | {msg}")


if __name__ == "__main__":
    main(sys.argv[1])
