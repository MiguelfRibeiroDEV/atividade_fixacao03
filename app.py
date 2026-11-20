import pandas as pd  # type: ignore
import sqlite3

print("=== ETAPA 1: EXTRAÇÃO DE DADOS ===")

vendas_loja = pd.read_csv("vendas_loja.csv")
vendas_loja["origem"] = "Física"
vendas_loja["data_venda"] = "2026-09-01"

vendas_site = pd.read_excel("vendas_site.xlsx")
vendas_site["origem"] = "Online"
vendas_site["data_venda"] = "2026-09-02"

clientes = pd.read_json("clientes.json")

imposto_regiao = pd.read_json("aliquota.json", typ="series")
imposto_regiao.name = "aliquota_imposto"

with sqlite3.connect("catalogo.db") as conn:
    produtos = pd.read_sql("SELECT * FROM produtos", con=conn)

print("Dados extraídos com sucesso!")

print("\n=== ETAPA 3: TRATAMENTO E INTEGRAÇÃO DE DATAFRAMES ===")

vendas_consolidadas = pd.concat([vendas_loja, vendas_site], ignore_index=True)
series_datas = pd.to_datetime(vendas_consolidadas["data_venda"])
series_dias_semana = series_datas.dt.day_name()

print("\nSeries derivada (Dias da semana):")
print(series_dias_semana)


vendas_consolidadas["desconto"] = vendas_consolidadas["desconto"].fillna(0.0)
vendas_consolidadas = vendas_consolidadas.drop_duplicates()

df_completo = vendas_consolidadas.merge(
    produtos, on="produto_id", how="left", indicator="_merge_produto"
)
df_completo = df_completo.merge(
    clientes, on="cliente_id", how="left", indicator="_merge_cliente"
)

print("\n--- AUDITORIA DE INTEGRAÇÕES ---")
print("Cruzamento com PRODUTOS:")
print(df_completo["_merge_produto"].value_counts().to_string())
print("Cruzamento com CLIENTES:")
print(df_completo["_merge_cliente"].value_counts().to_string())

mask_sem_cadastro = (
    (df_completo["_merge_produto"] != "both")
    | (df_completo["_merge_cliente"] != "both")
)
vendas_sem_cadastro = df_completo[mask_sem_cadastro]

if vendas_sem_cadastro.empty:
    print("Nenhuma venda com inconsistência de cadastro.")
else:
    print(f"\n{len(vendas_sem_cadastro)} venda(s) com inconsistência de cadastro (removidas):")
    print(
        vendas_sem_cadastro[
            ["transacao_id", "cliente_id", "produto_id", "_merge_produto", "_merge_cliente"]
        ]
    )

df_completo = (
    df_completo[~mask_sem_cadastro]
    .drop(columns=["_merge_produto", "_merge_cliente"])
    .reset_index(drop=True)
)

regras_invalidas = (
    (df_completo["quantidade"] <= 0)
    | (df_completo["preco_unitario"].fillna(0) <= 0)
    | (df_completo["desconto"] > 1.0)
    | (df_completo["desconto"] < 0)
)
vendas_invalidas = df_completo[regras_invalidas]

print("\n--- SANITIZAÇÃO (REGRAS DE NEGÓCIO) ---")
print(f"Quantidade <= 0:      {(df_completo['quantidade'] <= 0).sum()}")
print(f"Preço zerado/nulo:    {(df_completo['preco_unitario'].fillna(0) <= 0).sum()}")
print(f"Desconto fora de 0-1: {((df_completo['desconto'] > 1.0) | (df_completo['desconto'] < 0)).sum()}")
if not vendas_invalidas.empty:
    print("Registros removidos:")
    print(vendas_invalidas[["transacao_id", "quantidade", "preco_unitario", "desconto"]])

df_completo = df_completo[~regras_invalidas].reset_index(drop=True)
print(f"Registros válidos após sanitização: {len(df_completo)}")

df_completo["imposto"] = df_completo["estado"].map(imposto_regiao)

sem_aliquota = df_completo["imposto"].isna().sum()
if sem_aliquota:
    print(f"ATENÇÃO: {sem_aliquota} venda(s) em estado sem alíquota cadastrada.")

print("DataFrame Puro:")
print(df_completo)
print("DataFrame Integrado e Tratado:")
print(df_completo[["transacao_id", "nome", "nome_produto", "origem", "desconto", "imposto"]])


print("\n=== ETAPA 4: AGREGAÇÃO E MÉTRICAS ===")

df_completo["faturamento_bruto"] = (
    df_completo["quantidade"] * df_completo["preco_unitario"]
)

df_completo["valor_desconto"] = (
    df_completo["faturamento_bruto"] * df_completo["desconto"]
)
df_completo["valor_imposto"] = (
    (df_completo["faturamento_bruto"] - df_completo["valor_desconto"])
    * df_completo["imposto"]
)
df_completo["faturamento_liquido"] = (
    df_completo["faturamento_bruto"]
    - df_completo["valor_desconto"]
    - df_completo["valor_imposto"]
)
df_completo["margem_pct"] = (
    df_completo["faturamento_liquido"] / df_completo["faturamento_bruto"] * 100
)

print("\n--- BRUTO x DESCONTO x IMPOSTO x LÍQUIDO (por transação) ---")
print(
    df_completo[
        [
            "transacao_id", "faturamento_bruto", "valor_desconto",
            "valor_imposto", "faturamento_liquido", "margem_pct",
        ]
    ].round(2)
)

bruto_total = df_completo["faturamento_bruto"].sum()
desc_total = df_completo["valor_desconto"].sum()
imp_total = df_completo["valor_imposto"].sum()
liq_total = df_completo["faturamento_liquido"].sum()
print("\n--- RESUMO DE FATURAMENTO E MARGEM ---")
print(f"Faturamento Bruto:    R$ {bruto_total:,.2f}")
print(f"(-) Descontos:        R$ {desc_total:,.2f}")
print(f"(-) Impostos:         R$ {imp_total:,.2f}")
print(f"Faturamento Líquido:  R$ {liq_total:,.2f}")
print(f"Margem líquida:       {liq_total / bruto_total:.2%}")

relatorio_final = pd.pivot_table(
    df_completo,
    values="faturamento_liquido",
    index="categoria",
    columns="origem",
    aggfunc="sum",
    fill_value=0.0,
    margins=True,
    margins_name="Total Geral"
)


relatorio_formatado = relatorio_final.map(lambda x: f"R$ {x:,.2f}")

print("\n--- RELATÓRIO FINAL: FATURAMENTO LÍQUIDO (R$) ---")
print(relatorio_formatado)

# 3. Análise de clientes e ticket médio (groupby cliente_id)
clientes_resumo = df_completo.groupby("cliente_id").agg(
    nome=("nome", "first"),
    qtd_compras=("transacao_id", "count"),
    faturamento_total=("faturamento_liquido", "sum"),
    ticket_medio=("faturamento_liquido", "mean"),
)

print("\n--- TICKET MÉDIO POR CLIENTE ---")
print(clientes_resumo.round(2))

top_clientes = clientes_resumo.nlargest(3, "faturamento_total")
print("\n--- TOP 3 CLIENTES DE MAIOR VALOR (faturamento líquido total) ---")
print(top_clientes.round(2))

desconto_medio = (
    df_completo.groupby("categoria")["desconto"].mean().sort_values(ascending=False)
)
print("\n--- DESCONTO MÉDIO POR CATEGORIA ---")
print(desconto_medio.map(lambda x: f"{x:.2%}"))
print(f"\nCategoria que exige mais incentivo: {desconto_medio.idxmax()} ({desconto_medio.max():.2%})")