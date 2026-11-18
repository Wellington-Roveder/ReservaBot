import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd
from fastembed import TextEmbedding
from sklearn.model_selection import train_test_split

LIMIAR_SIMILARIDADE_LEAKAGE = 0.92
RANDOM_STATE = 42


def normalizar_texto(texto: str) -> str:
    return " ".join(str(texto).strip().lower().split())


def normalizar_rotulo(rotulo: str) -> str:
    return str(rotulo).strip().lower().replace(" ", "_")


def carregar_exemplos_intents(caminho_intents: Path) -> list[str]:
    with open(caminho_intents, "r", encoding="utf-8") as f:
        dados = json.load(f)
    frases = []
    for exemplos in dados.values():
        frases.extend(normalizar_texto(ex) for ex in exemplos)
    return frases


def embed_normalizado(modelo: TextEmbedding, textos: list[str]) -> np.ndarray:
    vetores = np.array(list(modelo.embed(textos)))
    normas = np.linalg.norm(vetores, axis=-1, keepdims=True)
    return vetores / np.maximum(normas, 1e-9)


def carregar_dataset(caminho: Path) -> pd.DataFrame:
    linhas = []

    with open(caminho, "r", encoding="utf-8-sig", newline="") as arquivo:
        reader = csv.reader(arquivo)
        next(reader)

        for linha in reader:
            mensagem = ",".join(linha[:-1])
            esperado = linha[-1]

            linhas.append({"mensagem": mensagem, "esperado": esperado})

    return pd.DataFrame(linhas)


def main() -> None:
    pasta_exp = Path(__file__).resolve().parent
    raiz = pasta_exp.parent.parent
    caminho_csv = pasta_exp / "dataset_nlu_completo.csv"
    caminho_intents = raiz / "services" / "nlu_config" / "intents.json"
    pasta_splits = pasta_exp / "splits"

    df = carregar_dataset(caminho_csv)
    df["mensagem_norm"] = df["mensagem"].apply(normalizar_texto)
    df["esperado"] = df["esperado"].apply(normalizar_rotulo)

    total_bruto = len(df)

    df = df.drop_duplicates(subset=["mensagem_norm"]).reset_index(drop=True)
    apos_exatas = len(df)

    modelo = TextEmbedding(
        model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    vetores_df = embed_normalizado(modelo, df["mensagem_norm"].tolist())

    exemplos_intents = carregar_exemplos_intents(caminho_intents)
    vetores_intents = embed_normalizado(modelo, exemplos_intents)
    sim_intents = np.max(np.dot(vetores_df, vetores_intents.T), axis=1)

    mask_sem_leakage_intents = sim_intents < LIMIAR_SIMILARIDADE_LEAKAGE
    removidos_intents = int((~mask_sem_leakage_intents).sum())

    df = df[mask_sem_leakage_intents].reset_index(drop=True)
    vetores_df = vetores_df[mask_sem_leakage_intents]

    manter_indices = []
    for i in range(len(df)):
        if not manter_indices:
            manter_indices.append(i)
            continue
        sims = np.dot(vetores_df[manter_indices], vetores_df[i])
        if float(np.max(sims)) < LIMIAR_SIMILARIDADE_LEAKAGE:
            manter_indices.append(i)

    removidos_near_dup = len(df) - len(manter_indices)
    df = df.iloc[manter_indices].reset_index(drop=True)

    df_final = df[["mensagem", "esperado"]]

    # 3. Stratified Split: 70% treino, 15% validação, 15% teste
    df_train, df_temp = train_test_split(
        df_final,
        test_size=0.30,
        stratify=df_final["esperado"],
        random_state=RANDOM_STATE,
    )
    df_val, df_test = train_test_split(
        df_temp,
        test_size=0.50,
        stratify=df_temp["esperado"],
        random_state=RANDOM_STATE,
    )

    df_train.to_csv(pasta_splits / "train.csv", index=False)
    df_val.to_csv(pasta_splits / "val.csv", index=False)
    df_test.to_csv(pasta_splits / "test.csv", index=False)

    print("=== RELATÓRIO DE PREPARAÇÃO DO DATASET ===")
    print(f"Total bruto:                        {total_bruto}")
    print(f"Removidos (duplicatas exatas):      {total_bruto - apos_exatas}")
    print(f"Removidos (leakage intents.json):   {removidos_intents}")
    print(f"Removidos (near-duplicates >=0.92): {removidos_near_dup}")
    print(f"Total limpo final:                  {len(df_final)}")
    print("-" * 44)
    print(
        f"Treino (70%): {len(df_train)} | Validação (15%): {len(df_val)} | Teste (15%): {len(df_test)}"
    )
    print("\nDistribuição por classe no dataset final:")
    print(df_final["esperado"].value_counts().to_string())


if __name__ == "__main__":
    main()
