"""
ImovelWeb Scraper — Interface Streamlit

Executar com:
    streamlit run app.py
"""

import io
import subprocess
import sys
import threading
import queue
import pandas as pd
import streamlit as st

from scraper import scrape, CAMPOS_COLETADOS


# ---------------------------------------------------------------------------
# Instala o Chromium automaticamente se não estiver presente (Streamlit Cloud)
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner="Instalando navegador (apenas na primeira execução)...")
def instalar_playwright():
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
        capture_output=True, text=True
    )
    return result.returncode

instalar_playwright()


# ---------------------------------------------------------------------------
# Config da página
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="ImovelWeb Scraper",
    page_icon="🏠",
    layout="centered",
)

st.title("🏠 ImovelWeb Scraper")
st.caption("Cole o link de busca prefiltrada do ImovelWeb e colete os dados que importam.")

# ---------------------------------------------------------------------------
# Formulário de entrada
# ---------------------------------------------------------------------------

with st.form("form_scraper"):
    url = st.text_input(
        "🔗 URL da busca prefiltrada",
        placeholder="https://www.imovelweb.com.br/terrenos-venda-joinville-sc.html",
        help="Cole a URL exatamente como aparece no navegador após aplicar os filtros desejados.",
    )

    col1, col2 = st.columns(2)

    with col1:
        tipo = st.selectbox(
            "🏗️ Tipo de imóvel",
            options=["terreno", "apartamento", "casa", "comercial", "generico"],
            help="Define quais campos serão coletados e quais são obrigatórios.",
        )

    with col2:
        max_validos = st.number_input(
            "📊 Linhas válidas desejadas",
            min_value=1,
            max_value=5000,
            value=50,
            step=10,
            help="O scraper para quando atingir esse número de registros com todos os campos obrigatórios preenchidos.",
        )

    headless = st.checkbox("Rodar browser em segundo plano (recomendado)", value=True)

    campos_info = CAMPOS_COLETADOS.get(tipo, [])
    st.info(f"**Campos coletados para '{tipo}':** {', '.join(campos_info)}\n\n"
            f"Registros sem todos os campos obrigatórios são descartados automaticamente.")

    submitted = st.form_submit_button("🚀 Iniciar Scraping", use_container_width=True)


# ---------------------------------------------------------------------------
# Execução do scraping
# ---------------------------------------------------------------------------

if submitted:
    if not url.strip():
        st.error("Por favor, informe a URL de busca.")
        st.stop()

    if "imovelweb.com.br" not in url:
        st.error("A URL deve ser do site imovelweb.com.br.")
        st.stop()

    st.divider()
    st.subheader("⏳ Progresso")

    log_container = st.empty()
    progress_bar = st.progress(0, text="Iniciando...")

    logs: list[str] = []
    result_queue: queue.Queue = queue.Queue()
    log_lock = threading.Lock()

    def log_fn(msg: str):
        with log_lock:
            logs.append(msg)
            # Atualiza o log visível (últimas 12 linhas)
            log_container.code("\n".join(logs[-12:]), language=None)

    def run_scraper():
        try:
            dados = scrape(
                url_base=url.strip(),
                tipo=tipo,
                max_validos=int(max_validos),
                headless=headless,
                log_fn=log_fn,
            )
            result_queue.put(("ok", dados))
        except Exception as e:
            result_queue.put(("erro", str(e)))

    # Roda em thread para não travar o Streamlit
    thread = threading.Thread(target=run_scraper, daemon=True)
    thread.start()

    # Aguarda com feedback visual
    dot = 0
    while thread.is_alive():
        dots = "." * (dot % 4 + 1)
        progress_bar.progress(0, text=f"Coletando{dots}")
        dot += 1
        import time; time.sleep(0.5)

    thread.join()
    progress_bar.progress(100, text="Concluído!")

    status, payload = result_queue.get()

    if status == "erro":
        st.error(f"Erro durante o scraping:\n\n{payload}")
        st.stop()

    dados: list[dict] = payload

    # ---------------------------------------------------------------------------
    # Resultados
    # ---------------------------------------------------------------------------

    st.divider()
    st.subheader(f"✅ Resultados — {len(dados)} registros coletados")

    if not dados:
        st.warning(
            "Nenhum registro válido foi coletado. Possíveis causas:\n"
            "- URL incorreta ou expirada\n"
            "- Site bloqueou o acesso (tente com browser visível)\n"
            "- Nenhum imóvel com todos os campos obrigatórios na página\n\n"
            "Verifique o arquivo **debug.html** gerado na pasta do projeto."
        )
        st.stop()

    df = pd.DataFrame(dados)

    # Reordena colunas: coloca url por último
    cols = [c for c in df.columns if c != "url"] + (["url"] if "url" in df.columns else [])
    df = df[cols]

    st.dataframe(df, use_container_width=True, height=400)

    # ---------------------------------------------------------------------------
    # Download
    # ---------------------------------------------------------------------------

    col_csv, col_json = st.columns(2)

    with col_csv:
        csv_bytes = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button(
            label="⬇️ Baixar CSV",
            data=csv_bytes,
            file_name="imoveis.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with col_json:
        json_bytes = df.to_json(orient="records", force_ascii=False, indent=2).encode("utf-8")
        st.download_button(
            label="⬇️ Baixar JSON",
            data=json_bytes,
            file_name="imoveis.json",
            mime="application/json",
            use_container_width=True,
        )

    # ---------------------------------------------------------------------------
    # Estatísticas rápidas
    # ---------------------------------------------------------------------------

    st.divider()
    st.subheader("📈 Resumo")

    total = len(df)
    st.metric("Total de registros", total)

    if "preco" in df.columns:
        # Extrai valores numéricos dos preços para estatísticas
        def extrair_numero(s):
            if pd.isna(s):
                return None
            s = str(s).replace(".", "").replace(",", ".")
            m = __import__("re").search(r"[\d]+\.?\d*", s.replace("R$", "").replace(" ", ""))
            return float(m.group(0)) if m else None

        precos = df["preco"].apply(extrair_numero).dropna()
        if not precos.empty:
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Preço mínimo", f"R$ {precos.min():,.0f}".replace(",", "."))
            col_b.metric("Preço médio",  f"R$ {precos.mean():,.0f}".replace(",", "."))
            col_c.metric("Preço máximo", f"R$ {precos.max():,.0f}".replace(",", "."))

    if "area_m2" in df.columns:
        def extrair_area(s):
            if pd.isna(s) or s == "":
                return None
            m = __import__("re").search(r"[\d,.]+", str(s))
            return float(m.group(0).replace(",", ".")) if m else None

        areas = df["area_m2"].apply(extrair_area).dropna()
        if not areas.empty:
            col_d, col_e, col_f = st.columns(3)
            col_d.metric("Área mínima",  f"{areas.min():,.0f} m²".replace(",", "."))
            col_e.metric("Área média",   f"{areas.mean():,.0f} m²".replace(",", "."))
            col_f.metric("Área máxima",  f"{areas.max():,.0f} m²".replace(",", "."))
