import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)

# Garante importação da raiz do projeto
PASTA_EXP = Path(__file__).resolve().parent
RAIZ = PASTA_EXP.parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from services.nlu_config.nlu import SemanticNLU

CLASSES_ORDENADAS = [
    "fazer_reserva",
    "consultar_reserva",
    "cancelar_reserva",
    "horario_funcionamento",
    "fora_de_escopo",
]

MAPA_INTENTS_BASELINE = {
    "fazer_reserva": "fazer_reserva",
    "fornecer_dados": "fazer_reserva",
    "consultar_reserva": "consultar_reserva",
    "alterar_cancelar_reserva": "cancelar_reserva",
    "duvidas_gerais": "horario_funcionamento",
    "saudacao": "fora_de_escopo",
    "fora_de_escopo": "fora_de_escopo",
}


def extrair_embeddings(nlu: SemanticNLU, textos: list[str]) -> np.ndarray:
    """Usa o mesmo encoder já carregado no SemanticNLU e aplica normalização L2."""
    textos_limpos = [t.strip().lower() for t in textos]
    vetores = np.array(list(nlu.model.embed(textos_limpos)))
    return nlu._normalizar_l2(vetores)


def prever_com_threshold(
    clf: LogisticRegression, X: np.ndarray, threshold: float
) -> list[str]:
    """
    Aplica limiar de rejeição: se a maior probabilidade entre as intenções
    dentro do escopo for menor que `threshold`, força 'fora_de_escopo'.
    """
    probs = clf.predict_proba(X)
    classes = list(clf.classes_)
    idx_fora = classes.index("fora_de_escopo")

    predicoes = []
    for prob_linha in probs:
        # Pega a melhor intenção válida (excluindo fora_de_escopo)
        probs_validas = [
            (classes[i], prob_linha[i]) for i in range(len(classes)) if i != idx_fora
        ]
        melhor_intent, melhor_prob = max(probs_validas, key=lambda x: x[1])

        if melhor_prob >= threshold and melhor_prob >= prob_linha[idx_fora]:
            predicoes.append(melhor_intent)
        else:
            predicoes.append("fora_de_escopo")
    return predicoes


def medir_latencia_ms(funcao_predicao, itens: list) -> tuple[float, float]:
    """Mede latência p50 e p95 em milissegundos por mensagem."""
    tempos = []
    for item in itens:
        t0 = time.perf_counter()
        funcao_predicao(item)
        tempos.append((time.perf_counter() - t0) * 1000)
    return float(np.percentile(tempos, 50)), float(np.percentile(tempos, 95))


def main() -> None:
    pasta_splits = PASTA_EXP / "splits"
    pasta_artifacts = PASTA_EXP / "artifacts"
    pasta_artifacts.mkdir(parents=True, exist_ok=True)

    df_train = pd.read_csv(pasta_splits / "train.csv")
    df_val = pd.read_csv(pasta_splits / "val.csv")
    df_test = pd.read_csv(pasta_splits / "test.csv")

    print("Carregando encoder MiniLM-L12 e baseline SemanticNLU...")
    nlu_baseline = SemanticNLU(threshold=0.50)

    X_train = extrair_embeddings(nlu_baseline, df_train["mensagem"].tolist())
    y_train = df_train["esperado"].tolist()

    X_val = extrair_embeddings(nlu_baseline, df_val["mensagem"].tolist())
    y_val = df_val["esperado"].tolist()

    X_test = extrair_embeddings(nlu_baseline, df_test["mensagem"].tolist())
    y_test = df_test["esperado"].tolist()

    # ==========================================================
    # 1. AVALIAÇÃO DO BASELINE (Similaridade de Cosseno, t=0.50)
    # ==========================================================
    preds_baseline = [
        MAPA_INTENTS_BASELINE.get(
            nlu_baseline.classificar(msg)["intent"], "fora_de_escopo"
        )
        for msg in df_test["mensagem"]
    ]
    p50_base, p95_base = medir_latencia_ms(
        nlu_baseline.classificar, df_test["mensagem"].tolist()
    )

    # ==========================================================
    # 2. TUNING NA VALIDAÇÃO (LogisticRegression + Threshold)
    # ==========================================================
    melhor_f1_val = -1.0
    melhor_cfg = {"C": 1.0, "threshold": 0.50}
    melhor_clf = None

    for c_val in [0.1, 0.5, 1.0, 2.0, 5.0, 10.0]:
        clf = LogisticRegression(
            C=c_val,
            class_weight="balanced",
            max_iter=1000,
            random_state=42,
        )
        clf.fit(X_train, y_train)

        for thresh in [0.35, 0.40, 0.45, 0.50, 0.55, 0.60]:
            preds_val = prever_com_threshold(clf, X_val, thresh)
            f1_val = f1_score(y_val, preds_val, average="macro")
            if f1_val > melhor_f1_val:
                melhor_f1_val = f1_val
                melhor_cfg = {"C": c_val, "threshold": thresh}
                melhor_clf = clf

    # ==========================================================
    # 3. AVALIAÇÃO FINAL NO TESTE CEGO
    # ==========================================================
    preds_logreg = prever_com_threshold(melhor_clf, X_test, melhor_cfg["threshold"])

    def inferencia_completa_logreg(texto: str) -> str:
        vec = extrair_embeddings(nlu_baseline, [texto])
        return prever_com_threshold(melhor_clf, vec, melhor_cfg["threshold"])[0]

    p50_lr, p95_lr = medir_latencia_ms(
        inferencia_completa_logreg, df_test["mensagem"].tolist()
    )

    # Salva artefato treinado
    caminho_modelo = pasta_artifacts / "logreg_nlu.joblib"
    joblib.dump(
        {"model": melhor_clf, "threshold": melhor_cfg["threshold"]}, caminho_modelo
    )

    # ==========================================================
    # 4. RELATÓRIO COMPARATIVO E ANÁLISE DE ERROS
    # ==========================================================
    linhas_relatorio = [
        "=== COMPARATIVO DE MODELOS NLU (TESTE CEGO: 44 MENSAGENS) ===",
        f"Melhor config validação -> C={melhor_cfg['C']}, threshold={melhor_cfg['threshold']} (F1-Macro Val: {melhor_f1_val:.4f})",
        "",
        "--- 1. BASELINE: SemanticNLU (Cosseno + intents.json, threshold=0.50) ---",
        f"Accuracy: {accuracy_score(y_test, preds_baseline):.4f} | F1-Macro: {f1_score(y_test, preds_baseline, average='macro'):.4f}",
        f"Latência por mensagem -> p50: {p50_base:.2f} ms | p95: {p95_base:.2f} ms",
        classification_report(
            y_test, preds_baseline, labels=CLASSES_ORDENADAS, digits=4, zero_division=0
        ),
        "Matriz de Confusão (linhas=real, colunas=predito):",
        str(
            pd.DataFrame(
                confusion_matrix(y_test, preds_baseline, labels=CLASSES_ORDENADAS),
                index=CLASSES_ORDENADAS,
                columns=CLASSES_ORDENADAS,
            )
        ),
        "",
        "--- 2. CLASSIFICADOR TREINADO: LogisticRegression sobre MiniLM-L12 ---",
        f"Accuracy: {accuracy_score(y_test, preds_logreg):.4f} | F1-Macro: {f1_score(y_test, preds_logreg, average='macro'):.4f}",
        f"Latência por mensagem -> p50: {p50_lr:.2f} ms | p95: {p95_lr:.2f} ms",
        f"Tamanho do artefato: {caminho_modelo.stat().st_size / 1024:.2f} KB",
        classification_report(
            y_test, preds_logreg, labels=CLASSES_ORDENADAS, digits=4, zero_division=0
        ),
        "Matriz de Confusão (linhas=real, colunas=predito):",
        str(
            pd.DataFrame(
                confusion_matrix(y_test, preds_logreg, labels=CLASSES_ORDENADAS),
                index=CLASSES_ORDENADAS,
                columns=CLASSES_ORDENADAS,
            )
        ),
        "",
        "=== ANÁLISE DE ERROS NO TESTE CEGO ===",
    ]

    for i, row in df_test.iterrows():
        real = row["esperado"]
        p_base = preds_baseline[i]
        p_lr = preds_logreg[i]
        if p_base != real or p_lr != real:
            linhas_relatorio.append(
                f"- Msg: '{row['mensagem']}' | Real: {real} | Baseline: {p_base} | LogReg: {p_lr}"
            )

    relatorio_texto = "\n".join(linhas_relatorio)
    print(relatorio_texto)

    caminho_saida = PASTA_EXP / "comparativo_modelos.txt"
    caminho_saida.write_text(relatorio_texto, encoding="utf-8")


if __name__ == "__main__":
    main()
