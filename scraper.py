"""
ImovelWeb Scraper — core logic
Chamado pelo app.py (Streamlit) ou direto pelo terminal.
"""

import re
import time
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import stealth_sync


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# Campos obrigatórios por tipo (se algum faltar, o registro é descartado)
CAMPOS_OBRIGATORIOS = {
    "terreno":      ["preco", "area_m2", "endereco"],
    "apartamento":  ["preco", "endereco"],
    "casa":         ["preco", "endereco"],
    "comercial":    ["preco", "endereco"],
    "generico":     ["preco"],
}

# Campos coletados por tipo
CAMPOS_COLETADOS = {
    "terreno":      ["preco", "area_m2", "endereco"],
    "apartamento":  ["preco", "area_m2", "endereco", "quartos", "banheiros", "vagas"],
    "casa":         ["preco", "area_m2", "endereco", "quartos", "banheiros", "vagas"],
    "comercial":    ["preco", "area_m2", "endereco"],
    "generico":     ["preco", "area_m2", "endereco", "quartos", "banheiros", "vagas"],
}


# ---------------------------------------------------------------------------
# Paginação
# ---------------------------------------------------------------------------

def proxima_pagina_url(url_base: str, pagina: int) -> str:
    """
    Gera URL da próxima página com base na URL prefiltrada do usuário.
    Ex: /terrenos-venda-joinville-sc.html  →  /terrenos-venda-joinville-sc-pagina-2.html
    """
    url = url_base.split("?")[0]  # remove query string, se houver
    url = re.sub(r"-pagina-\d+\.html$", ".html", url)  # garante base limpa

    if pagina == 1:
        return url
    return url.replace(".html", f"-pagina-{pagina}.html")


# ---------------------------------------------------------------------------
# Extração de campos de um card
# ---------------------------------------------------------------------------

def _texto(el) -> str:
    return el.get_text(" ", strip=True) if el else ""


def _primeiro(*seletores, soup) -> str:
    for sel in seletores:
        el = soup.select_one(sel)
        if el:
            t = _texto(el)
            if t:
                return t
    return ""


def extrair_card(card, tipo: str) -> dict | None:
    """
    Extrai campos de um card de imóvel.
    Retorna None se os campos obrigatórios estiverem ausentes.
    """
    campos_desejados = CAMPOS_COLETADOS.get(tipo, CAMPOS_COLETADOS["generico"])
    campos_obrig = CAMPOS_OBRIGATORIOS.get(tipo, CAMPOS_OBRIGATORIOS["generico"])
    data = {}

    # --- URL do anúncio ---
    link = card.select_one("a[href]")
    if link:
        href = link.get("href", "")
        data["url"] = "https://www.imovelweb.com.br" + href if href.startswith("/") else href

    # --- PREÇO ---
    if "preco" in campos_desejados:
        preco_raw = _primeiro(
            "[data-qa='PRICE']",
            "[class*='price']",
            "[class*='Price']",
            "[class*='firstPrice']",
            soup=card,
        )
        # Limpa: mantém só dígitos e separadores
        m = re.search(r"R\$[\s\d.,]+", preco_raw.replace("\xa0", " "))
        data["preco"] = m.group(0).strip() if m else preco_raw.strip()

    # --- ENDEREÇO ---
    if "endereco" in campos_desejados:
        data["endereco"] = _primeiro(
            "[data-qa='POSTING_CARD_LOCATION']",
            "[class*='location']",
            "[class*='Location']",
            "[class*='address']",
            "[class*='Address']",
            soup=card,
        )

    # --- ÁREA ---
    if "area_m2" in campos_desejados:
        # Procura padrões como "450 m²", "450m2"
        texto_completo = card.get_text(" ")
        m = re.search(r"([\d,.]+)\s*m[²2²]", texto_completo, re.I)
        if m:
            data["area_m2"] = m.group(1).replace(",", ".")

        # Tenta também via seletores específicos se não achou
        if not data.get("area_m2"):
            for el in card.select("[data-qa*='SURFACE'], [data-qa*='AREA'], [class*='surface']"):
                t = _texto(el)
                m2 = re.search(r"([\d,.]+)", t)
                if m2:
                    data["area_m2"] = m2.group(1).replace(",", ".")
                    break

    # --- QUARTOS ---
    if "quartos" in campos_desejados:
        for el in card.select("[data-qa*='BEDROOMS'], [data-qa*='ROOM']"):
            m = re.search(r"\d+", _texto(el))
            if m:
                data["quartos"] = m.group(0)
                break
        if not data.get("quartos"):
            m = re.search(r"(\d+)\s*(quarto|dorm)", card.get_text(), re.I)
            if m:
                data["quartos"] = m.group(1)

    # --- BANHEIROS ---
    if "banheiros" in campos_desejados:
        for el in card.select("[data-qa*='BATHROOM']"):
            m = re.search(r"\d+", _texto(el))
            if m:
                data["banheiros"] = m.group(0)
                break
        if not data.get("banheiros"):
            m = re.search(r"(\d+)\s*banheir", card.get_text(), re.I)
            if m:
                data["banheiros"] = m.group(1)

    # --- VAGAS ---
    if "vagas" in campos_desejados:
        for el in card.select("[data-qa*='PARKING'], [data-qa*='GARAGE']"):
            m = re.search(r"\d+", _texto(el))
            if m:
                data["vagas"] = m.group(0)
                break
        if not data.get("vagas"):
            m = re.search(r"(\d+)\s*vaga", card.get_text(), re.I)
            if m:
                data["vagas"] = m.group(1)

    # --- Valida campos obrigatórios ---
    for campo in campos_obrig:
        if not data.get(campo):
            return None  # descarta o registro

    # Retorna só os campos desejados + url
    resultado = {"url": data.get("url", "")}
    for campo in campos_desejados:
        resultado[campo] = data.get(campo, "")

    return resultado


# ---------------------------------------------------------------------------
# Encontra os cards na página
# ---------------------------------------------------------------------------

def encontrar_cards(soup: BeautifulSoup) -> list:
    # Seletores por data-qa (mais estável)
    seletores_exatos = [
        "[data-qa='POSTING_CARD']",
        "[data-qa='posting-card']",
    ]
    for sel in seletores_exatos:
        cards = soup.select(sel)
        if cards:
            return cards

    # Seletores por data-id
    for tag in ("div", "article", "li", "section"):
        cards = soup.select(f"{tag}[data-id]")
        if cards:
            return cards

    # Seletores por classe parcial (styled-components gera nomes dinâmicos)
    parciais = [
        "postingCardLayout", "PostingCard", "posting-card",
        "postingCard", "property-card", "PropertyCard",
        "result-item", "ResultItem", "listingCard",
    ]
    for parte in parciais:
        cards = soup.select(f"[class*='{parte}']")
        if cards:
            return cards

    # Fallback inteligente: procura divs/articles que contenham preço (R$)
    # e algum indicador de área ou endereço — típico de cards de imóvel
    candidatos = []
    for el in soup.find_all(["div", "article", "li", "section"]):
        texto = el.get_text(" ")
        tem_preco = "R$" in texto
        tem_area = bool(re.search(r"\d+\s*m[²2]", texto, re.I))
        tem_link = el.find("a", href=re.compile(r"/[a-z]+-\d+\.html"))
        if tem_preco and (tem_area or tem_link):
            # Evita pegar containers pai (prefere o elemento mais específico)
            pai = el.find_parent(lambda p: p in candidatos)
            if not pai:
                candidatos.append(el)
    return candidatos


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def scrape(
    url_base: str,
    tipo: str,
    max_validos: int,
    headless: bool = True,
    delay: float = 2.5,
    log_fn=print,
) -> tuple[list[dict], str]:
    """
    Retorna (lista_de_imoveis, html_debug).
    html_debug é o HTML da última página quando nenhum card foi encontrado, ou "".
    """
    """
    Scrapa a URL prefiltrada do ImovelWeb até atingir `max_validos` registros.

    log_fn: função de log (print ou st.write para Streamlit)
    """
    tipo = tipo.lower()
    resultados = []
    html_debug = ""
    pagina = 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="pt-BR",
        )
        page = context.new_page()

        # Aplica stealth ANTES de qualquer navegação (remove fingerprints de automação)
        stealth_sync(page)

        # Bloqueia imagens e fontes para acelerar — mas permite JS (necessário pro Cloudflare)
        def bloquear_recursos(route):
            if route.request.resource_type in ("image", "font", "media"):
                route.abort()
            else:
                route.continue_()
        page.route("**/*", bloquear_recursos)

        while len(resultados) < max_validos:
            url = proxima_pagina_url(url_base, pagina)
            log_fn(f"🔍 Página {pagina}: {url}")

            try:
                # networkidle aguarda o Cloudflare challenge completar
                page.goto(url, wait_until="networkidle", timeout=45_000)
                time.sleep(delay)

                # Verifica se ainda está na tela de verificação de segurança
                titulo = page.title().lower()
                corpo = page.inner_text("body").lower() if page.query_selector("body") else ""
                if "verificação" in corpo or "verificacao" in corpo or "security check" in corpo:
                    log_fn("⏳ Aguardando verificação de segurança do Cloudflare...")
                    time.sleep(8)  # aguarda challenge resolver

                # Scroll lento para ativar lazy-load
                for _ in range(4):
                    page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
                    time.sleep(0.8)
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(0.5)

                html = page.content()

            except PlaywrightTimeout:
                log_fn(f"⚠️ Timeout na página {pagina}. Encerrando.")
                break

            soup = BeautifulSoup(html, "lxml")
            cards = encontrar_cards(soup)

            if not cards:
                log_fn("⚠️ Nenhum card encontrado. Retornando HTML para diagnóstico.")
                html_debug = html
                break

            novos = 0
            for card in cards:
                if len(resultados) >= max_validos:
                    break
                item = extrair_card(card, tipo)
                if item:
                    resultados.append(item)
                    novos += 1

            descartados = len(cards) - novos
            log_fn(
                f"   ✅ {novos} válidos | ⛔ {descartados} descartados | "
                f"Total acumulado: {len(resultados)}/{max_validos}"
            )

            # Verifica se há próxima página
            tem_proxima = soup.select_one(
                "[data-qa='PAGING_NEXT'], a[aria-label*='Próxima'], "
                "[class*='nextPage'], [rel='next']"
            )
            if not tem_proxima:
                log_fn("📄 Última página atingida.")
                break

            pagina += 1

        browser.close()

    log_fn(f"\n✔ Scraping concluído. {len(resultados)} registros válidos coletados.")
    return resultados, html_debug
