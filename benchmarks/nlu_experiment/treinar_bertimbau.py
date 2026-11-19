import json
import platform
import time
from pathlib import Path

import datasets
import numpy as np
import pandas as pd
import sklearn
import torch
import transformers
from datasets import Dataset
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    recall_score,
)
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    DataCollatorWithPadding,
    Trainer,
    TrainingArguments,
    set_seed,
)

MODELO_BASE = "neuralmind/bert-base-portuguese-cased"
SEED = 42
MAX_LENGTH = 64
WARMUP_RUNS = 10
BENCHMARK_REPEATS = 5
MIN_RECALL_FORA_ESCOPO = 0.85

CLASSES_ORDENADAS = [
    "fazer_reserva",
    "consultar_reserva",
    "cancelar_reserva",
    "horario_funcionamento",
    "fora_de_escopo",
]
LABEL2ID = {label: idx for idx, label in enumerate(CLASSES_ORDENADAS)}
ID2LABEL = {idx: label for idx, label in enumerate(CLASSES_ORDENADAS)}


def validar_integridade_splits(
    df_train: pd.DataFrame, df_val: pd.DataFrame, df_test: pd.DataFrame
) -> str:
    """Garante ausência de NaNs, rótulos inválidos e vazamento exato entre splits."""
    splits = {"train": df_train, "val": df_val, "test": df_test}

    for nome, df in splits.items():
        if df[["mensagem", "esperado"]].isnull().any().any():
            raise ValueError(
                f"[{nome}] Contém valores nulos/NaN em mensagem ou esperado."
            )

        textos_vazios = df["mensagem"].astype(str).str.strip().eq("").sum()
        if textos_vazios > 0:
            raise ValueError(f"[{nome}] Contém {textos_vazios} mensagens vazias.")

        invalidos = set(df["esperado"].unique()) - set(CLASSES_ORDENADAS)
        if invalidos:
            raise ValueError(f"[{nome}] Rótulos inválidos encontrados: {invalidos}")

    norm = lambda s: set(s.astype(str).str.strip().str.lower())
    s_train, s_val, s_test = (
        norm(df_train["mensagem"]),
        norm(df_val["mensagem"]),
        norm(df_test["mensagem"]),
    )

    if s_train & s_test:
        raise AssertionError(
            f"Vazamento detectado entre train e test: {s_train & s_test}"
        )
    if s_val & s_test:
        raise AssertionError(f"Vazamento detectado entre val e test: {s_val & s_test}")
    if s_train & s_val:
        raise AssertionError(
            f"Vazamento detectado entre train e val: {s_train & s_val}"
        )

    dist_df = pd.DataFrame(
        {
            nome: df["esperado"].value_counts().reindex(CLASSES_ORDENADAS, fill_value=0)
            for nome, df in splits.items()
        }
    )
    dist_df["total"] = dist_df.sum(axis=1)
    return str(dist_df)


def compute_metrics(eval_pred):
    logits, labels = eval_pred
    preds = np.argmax(logits, axis=-1)
    return {
        "accuracy": accuracy_score(labels, preds),
        "f1_macro": f1_score(labels, preds, average="macro"),
    }


def prever_com_threshold(probs: np.ndarray, threshold: float) -> list[str]:
    idx_fora = LABEL2ID["fora_de_escopo"]
    predicoes = []
    for prob_linha in probs:
        probs_validas = [
            (CLASSES_ORDENADAS[i], prob_linha[i])
            for i in range(len(CLASSES_ORDENADAS))
            if i != idx_fora
        ]
        melhor_intent, melhor_prob = max(probs_validas, key=lambda x: x[1])
        if melhor_prob >= threshold and melhor_prob >= prob_linha[idx_fora]:
            predicoes.append(melhor_intent)
        else:
            predicoes.append("fora_de_escopo")
    return predicoes


def calibrar_threshold_orientado_a_custo(
    probs_val: np.ndarray, y_val: list[str]
) -> tuple[float, float, float]:
    """
    Seleciona o threshold priorizando segurança do gatekeeper:
    exige recall mínimo em 'fora_de_escopo' para evitar chamadas desnecessárias ao LLM,
    e entre os candidatos válidos maximiza o F1-Macro (desempate pelo maior threshold).
    """
    candidatos = [round(t, 2) for t in np.arange(0.35, 0.81, 0.05)]
    validos = []
    todos = []

    for thresh in candidatos:
        preds = prever_com_threshold(probs_val, thresh)
        f1_m = f1_score(y_val, preds, average="macro")
        rec_fora = recall_score(
            y_val, preds, labels=["fora_de_escopo"], average="macro", zero_division=0
        )
        todos.append((thresh, f1_m, rec_fora))
        if rec_fora >= MIN_RECALL_FORA_ESCOPO:
            validos.append((thresh, f1_m, rec_fora))

    pool = validos if validos else todos
    # Ordena por maior F1-Macro e, em caso de empate, maior threshold (mais conservador)
    melhor_thresh, melhor_f1, melhor_rec_fora = max(pool, key=lambda x: (x[1], x[0]))
    return melhor_thresh, melhor_f1, melhor_rec_fora


def medir_latencia_cpu_estavel(
    model: AutoModelForSequenceClassification,
    tokenizer: AutoTokenizer,
    textos: list[str],
    threshold: float,
) -> tuple[float, float, float]:
    """Executa warm-up e múltiplas passagens em CPU para métricas estáveis p50/p95/p99."""
    model_cpu = model.to("cpu").eval()
    tempos_ms = []

    with torch.no_grad():
        # Warm-up (descartado)
        for msg in textos[:WARMUP_RUNS]:
            inputs = tokenizer(
                msg, return_tensors="pt", truncation=True, max_length=MAX_LENGTH
            )
            out = model_cpu(**inputs)
            probs = torch.softmax(out.logits, dim=-1).numpy()
            _ = prever_com_threshold(probs, threshold)

        # Medição real em múltiplas passagens
        for _ in range(BENCHMARK_REPEATS):
            for msg in textos:
                t0 = time.perf_counter()
                inputs = tokenizer(
                    msg, return_tensors="pt", truncation=True, max_length=MAX_LENGTH
                )
                out = model_cpu(**inputs)
                probs = torch.softmax(out.logits, dim=-1).numpy()
                _ = prever_com_threshold(probs, threshold)
                tempos_ms.append((time.perf_counter() - t0) * 1000)

    return (
        float(np.percentile(tempos_ms, 50)),
        float(np.percentile(tempos_ms, 95)),
        float(np.percentile(tempos_ms, 99)),
    )


def main(pasta_base: Path | None = None) -> None:
    set_seed(SEED)
    pasta_exp = pasta_base or Path(__file__).resolve().parent
    pasta_splits = pasta_exp / "splits"
    pasta_saida_modelo = pasta_exp / "artifacts" / "bertimbau_nlu"
    pasta_checkpoints = pasta_exp / "artifacts" / "checkpoints_bert"
    pasta_saida_modelo.mkdir(parents=True, exist_ok=True)

    df_train = pd.read_csv(pasta_splits / "train.csv")
    df_val = pd.read_csv(pasta_splits / "val.csv")
    df_test = pd.read_csv(pasta_splits / "test.csv")

    tabela_distribuicao = validar_integridade_splits(df_train, df_val, df_test)

    for df in (df_train, df_val, df_test):
        df["label"] = df["esperado"].map(LABEL2ID)

    tokenizer = AutoTokenizer.from_pretrained(MODELO_BASE)

    def tokenizar(batch):
        return tokenizer(batch["mensagem"], truncation=True, max_length=MAX_LENGTH)

    ds_train = Dataset.from_pandas(df_train[["mensagem", "label"]]).map(
        tokenizar, batched=True
    )
    ds_val = Dataset.from_pandas(df_val[["mensagem", "label"]]).map(
        tokenizar, batched=True
    )
    ds_test = Dataset.from_pandas(df_test[["mensagem", "label"]]).map(
        tokenizar, batched=True
    )

    model = AutoModelForSequenceClassification.from_pretrained(
        MODELO_BASE,
        num_labels=len(CLASSES_ORDENADAS),
        id2label=ID2LABEL,
        label2id=LABEL2ID,
    )

    hiperparametros = {
        "learning_rate": 3e-5,
        "per_device_train_batch_size": 16,
        "per_device_eval_batch_size": 32,
        "num_train_epochs": 6,
        "weight_decay": 0.01,
        "warmup_steps": 8,
        "max_length": MAX_LENGTH,
        "seed": SEED,
    }

    training_args = TrainingArguments(
        output_dir=str(pasta_checkpoints),
        eval_strategy="epoch",
        save_strategy="epoch",
        learning_rate=hiperparametros["learning_rate"],
        per_device_train_batch_size=hiperparametros["per_device_train_batch_size"],
        per_device_eval_batch_size=hiperparametros["per_device_eval_batch_size"],
        num_train_epochs=hiperparametros["num_train_epochs"],
        weight_decay=hiperparametros["weight_decay"],
        warmup_ratio=hiperparametros["warmup_ratio"],
        load_best_model_at_end=True,
        metric_for_best_model="f1_macro",
        greater_is_better=True,
        save_total_limit=1,
        logging_steps=10,
        seed=SEED,
        report_to="none",
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=ds_train,
        eval_dataset=ds_val,
        processing_class=tokenizer,
        data_collator=DataCollatorWithPadding(tokenizer=tokenizer),
        compute_metrics=compute_metrics,
    )

    device_treino = str(trainer.args.device)
    gpu_nome = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    print(f"Iniciando fine-tuning de {MODELO_BASE} em {device_treino} ({gpu_nome})...")
    trainer.train()

    trainer.save_model(str(pasta_saida_modelo))
    tokenizer.save_pretrained(str(pasta_saida_modelo))

    # Calibração de threshold orientada a custo na validação
    logits_val = trainer.predict(ds_val).predictions
    probs_val = torch.softmax(torch.tensor(logits_val), dim=-1).numpy()
    y_val = df_val["esperado"].tolist()

    melhor_thresh, melhor_f1_val, melhor_rec_fora_val = (
        calibrar_threshold_orientado_a_custo(probs_val, y_val)
    )

    # Avaliação final no teste cego
    logits_test = trainer.predict(ds_test).predictions
    probs_test = torch.softmax(torch.tensor(logits_test), dim=-1).numpy()
    y_test = df_test["esperado"].tolist()
    preds_bert = prever_com_threshold(probs_test, melhor_thresh)

    # Latência estável em CPU (warm-up + múltiplas passagens)
    p50_cpu, p95_cpu, p99_cpu = medir_latencia_cpu_estavel(
        model, tokenizer, df_test["mensagem"].tolist(), melhor_thresh
    )

    # Tamanho recursivo exato (incluindo subdiretórios)
    tamanho_mb = sum(
        f.stat().st_size for f in pasta_saida_modelo.rglob("*") if f.is_file()
    ) / (1024 * 1024)

    metadados_reproducao = {
        "base_model": MODELO_BASE,
        "threshold": melhor_thresh,
        "min_recall_fora_escopo_target": MIN_RECALL_FORA_ESCOPO,
        "hyperparameters": hiperparametros,
        "environment": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "datasets": datasets.__version__,
            "scikit_learn": sklearn.__version__,
            "train_device": f"{device_treino} ({gpu_nome})",
        },
    }

    (pasta_saida_modelo / "nlu_config.json").write_text(
        json.dumps(metadados_reproducao, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    linhas = [
        "=== AVALIAÇÃO BERTIMBAU FINE-TUNED (TESTE CEGO: 44 MENSAGENS) ===",
        f"Modelo base: {MODELO_BASE}",
        f"Ambiente: Python {platform.python_version()} | PyTorch {torch.__version__} | Transformers {transformers.__version__} | Treino: {gpu_nome}",
        "",
        "--- Auditoria de Integridade e Distribuição dos Splits ---",
        tabela_distribuicao,
        "",
        (
            f"Threshold calibrado (validação, recall fora_de_escopo >= {MIN_RECALL_FORA_ESCOPO}): "
            f"{melhor_thresh} (F1-Macro Val: {melhor_f1_val:.4f} | Recall fora_de_escopo Val: {melhor_rec_fora_val:.4f})"
        ),
        f"Accuracy (Teste Cego): {accuracy_score(y_test, preds_bert):.4f} | F1-Macro (Teste Cego): {f1_score(y_test, preds_bert, average='macro'):.4f}",
        f"Latência CPU ({WARMUP_RUNS} warm-ups, {BENCHMARK_REPEATS}x passagens) -> p50: {p50_cpu:.2f} ms | p95: {p95_cpu:.2f} ms | p99: {p99_cpu:.2f} ms",
        f"Tamanho total do artefato (recursivo): {tamanho_mb:.2f} MB",
        "",
        classification_report(
            y_test, preds_bert, labels=CLASSES_ORDENADAS, digits=4, zero_division=0
        ),
        "Matriz de Confusão (linhas=real, colunas=predito):",
        str(
            pd.DataFrame(
                confusion_matrix(y_test, preds_bert, labels=CLASSES_ORDENADAS),
                index=CLASSES_ORDENADAS,
                columns=CLASSES_ORDENADAS,
            )
        ),
        "",
        "=== ERROS DO BERTIMBAU NO TESTE CEGO ===",
    ]

    for i, row in df_test.iterrows():
        if preds_bert[i] != row["esperado"]:
            linhas.append(
                f"- Msg: '{row['mensagem']}' | Real: {row['esperado']} | BERTimbau: {preds_bert[i]}"
            )

    relatorio = "\n".join(linhas)
    print(relatorio)
    (pasta_exp / "resultados_bertimbau.txt").write_text(relatorio, encoding="utf-8")


if __name__ == "__main__":
    main()
