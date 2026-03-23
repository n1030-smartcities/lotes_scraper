"""
ImovelWeb Scraper — Interface Streamlit

Executar com:
    streamlit run app.py
"""

import glob
import os
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

def _browser_instalado() -> bool:
    cache = os.path.expanduser("~/.cache/ms-playwright")
    # Procura qualquer executável chromium (headless shell ou full)
    padrao = os.path.join(cache, "chromium*", "**", "chrom*")
    return bool(glob.glob(padrao, recursive=True))


def garantir_browser():
    if _browser_instalado():
        return
    with st.spinner("Instalando Chromium (apenas na primeira execução)..."):
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True,
        )
    if result.returncode != 0:
        st.error(
            f"Falha ao instalar o Chromium:\n\n```\n{result.stderr[-1500:]}\n```"
        )
        st.stop()


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

    garantir_browser()

    st.divider()
    st.subheader("⏳ Progresso")

    log_container = st.empty()
    progress_bar = st.progress(0, text="Iniciando...")

    # Filas de comunicação entre thread e UI (sem chamar st.* de dentro da thread)
    log_queue: queue.Queue = queue.Queue()
    result_queue: queue.Queue = queue.Queue()

    # headless obrigatório: Streamlit Cloud não tem servidor de display (X11)
    tem_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    def run_scraper():
        try:
            dados = scrape(
                url_base=url.strip(),
                tipo=tipo,
                max_validos=int(max_validos),
                headless=not tem_display,
                log_fn=lambda msg: log_queue.put(msg),  # só enfileira, não toca na UI
            )
            result_queue.put(("ok", dados))
        except Exception as e:
            result_queue.put(("erro", str(e)))

    import time

    thread = threading.Thread(target=run_scraper, daemon=True)
    thread.start()

    # Loop principal: lê logs da fila e atualiza UI no thread correto
    logs: list[str] = []
    dot = 0
    while thread.is_alive():
        while not log_queue.empty():
            logs.append(log_queue.get_nowait())
        if logs:
            log_container.code("\n".join(logs[-12:]), language=None)
        progress_bar.progress(0, text=f"Coletando{'.' * (dot % 4 + 1)}")
        dot += 1
        time.sleep(0.5)

    # Drena logs restantes após a thread encerrar
    while not log_queue.empty():
        logs.append(log_queue.get_nowait())
    if logs:
        log_container.code("\n".join(logs[-12:]), language=None)

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
