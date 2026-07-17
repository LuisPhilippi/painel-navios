"""
Scraper de status de navios - Praticagem São Francisco (São Francisco do Sul / Itapoá)
========================================================================================

IMPORTANTE: essa página carrega a tabela de navios via JavaScript (depois que a
página inicial já carregou). Por isso este script usa o Playwright, que abre um
"navegador robô" de verdade (sem tela, rodando em segundo plano), espera o
JavaScript rodar e só então lê a tabela - diferente de uma biblioteca simples
tipo `requests`, que só baixa o HTML "cru" e não veria a tabela preenchida.

Colunas esperadas na tabela:
Navio | Nº IMO | Tipo de Navio | Agência | Comp. | Boca | Calado | GRT |
Callsign | Data de Chegada | Data da Última Manobra | Manobra | Data de Manobra |
Berço | Situação

Como usar:
    python scraper_psf.py navios.txt

Dependências:
    pip install playwright beautifulsoup4 lxml
    playwright install --with-deps chromium
"""

import sys
import re
import json
import unicodedata
import difflib
from datetime import datetime

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URL = "https://webpilot.praticagemsaofrancisco.com.br/webpilot/integracao/itmanobrassfs.aspx?chave_api=55021FC5-3800-4E11-8B7D-C4726E8E07F8"

DATA_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
HORA_RE = re.compile(r"(\d{2}:\d{2})")


def normalizar(texto: str) -> str:
    texto = texto.strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    texto = re.sub(r"\s+", " ", texto)
    return texto


def parse_data_hora(texto: str):
    if not texto:
        return None
    data_match = DATA_RE.search(texto)
    hora_match = HORA_RE.search(texto)
    if not data_match:
        return None
    data_str = data_match.group(1)
    hora_str = hora_match.group(1) if hora_match else "00:00"
    try:
        return datetime.strptime(f"{data_str} {hora_str}", "%d/%m/%Y %H:%M")
    except ValueError:
        return None


def encontrar_tabela_de_navios(soup):
    for tabela in soup.find_all("table"):
        headers_tabela = [th.get_text(strip=True).lower() for th in tabela.find_all("th")]
        if any("navio" in h for h in headers_tabela):
            return tabela, headers_tabela
    return None, []


def obter_html_renderizado():
    """Abre a página num navegador headless, espera carregar, e devolve o HTML já com os dados."""
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page()
        pagina.goto(URL, wait_until="networkidle", timeout=30000)
        try:
            pagina.wait_for_selector("table", timeout=10000)
        except Exception:
            print("[aviso] nenhuma <table> apareceu em 10s de espera extra.", file=sys.stderr)
        html = pagina.content()
        navegador.close()
        return html


def buscar_movimentacoes():
    html = obter_html_renderizado()
    soup = BeautifulSoup(html, "lxml")

    tabela, headers_tabela = encontrar_tabela_de_navios(soup)
    if tabela is None:
        todas = soup.find_all("table")
        print(f"[debug] {len(todas)} tabela(s) na página, nenhuma com coluna 'Navio'.", file=sys.stderr)
        for i, t in enumerate(todas):
            hs = [th.get_text(strip=True) for th in t.find_all("th")]
            print(f"[debug] tabela {i}: {hs}", file=sys.stderr)
        raise RuntimeError(
            "Não encontrei nenhuma tabela com coluna 'Navio' na página, mesmo após renderizar o JavaScript."
        )

    registros = []
    for tr in tabela.find_all("tr")[1:]:
        colunas = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if not colunas:
            continue
        registro = dict(zip(headers_tabela, colunas))

        nome = (registro.get("navio") or "").strip()
        if not nome:
            continue

        data_chegada_texto = registro.get("data de chegada") or ""
        data_chegada_dt = parse_data_hora(data_chegada_texto)

        registros.append({
            "navio": nome,
            "imo": registro.get("nº imo") or registro.get("n imo") or registro.get("imo"),
            "agencia": registro.get("agência") or registro.get("agencia"),
            "data_chegada_texto": data_chegada_texto or None,
            "data_chegada_iso": data_chegada_dt.isoformat() if data_chegada_dt else None,
            "manobra": registro.get("manobra"),
            "data_manobra": registro.get("data de manobra"),
            "berco": registro.get("berço") or registro.get("berco"),
            "situacao": registro.get("situação") or registro.get("situacao"),
        })

    return registros


def cruzar_com_lista(navios_site, navios_acompanhados, limiar=0.82):
    encontrados = []
    nomes_site_normalizados = {normalizar(n["navio"]): n for n in navios_site}
    nao_encontrados = []

    for nome_buscado in navios_acompanhados:
        chave_buscada = normalizar(nome_buscado)

        if chave_buscada in nomes_site_normalizados:
            encontrados.append({"navio_planilha": nome_buscado, **nomes_site_normalizados[chave_buscada]})
            continue

        candidatos = difflib.get_close_matches(
            chave_buscada, nomes_site_normalizados.keys(), n=1, cutoff=limiar
        )
        if candidatos:
            encontrados.append({"navio_planilha": nome_buscado, **nomes_site_normalizados[candidatos[0]]})
        else:
            nao_encontrados.append(nome_buscado)

    return encontrados, nao_encontrados


def main():
    navios_site = buscar_movimentacoes()
print(f"[debug] {len(navios_site)} navio(s) lidos da tabela no total.", file=sys.stderr)
    for n in navios_site[:15]:
        print(f"[debug]   - {n['navio']}", file=sys.stderr)
    if len(sys.argv) < 2:
        print(json.dumps(navios_site, indent=2, ensure_ascii=False))
        return

    with open(sys.argv[1], encoding="utf-8") as f:
        navios_acompanhados = [linha.strip() for linha in f if linha.strip()]

    encontrados, nao_encontrados = cruzar_com_lista(navios_site, navios_acompanhados)

    resultado = {
        "gerado_em": datetime.now().isoformat(),
        "encontrados": encontrados,
        "nao_encontrados": nao_encontrados,
    }

    with open("resultado_psf.json", "w", encoding="utf-8") as f:
        json.dump(resultado, f, indent=2, ensure_ascii=False)

    print(f"{len(encontrados)} navio(s) encontrado(s) e atualizado(s).")
    for item in encontrados:
        print(f"  - {item['navio_planilha']}: situação = {item.get('situacao')}")
    if nao_encontrados:
        print(f"{len(nao_encontrados)} navio(s) da lista NÃO encontrados:")
        for n in nao_encontrados:
            print(f"  - {n}")
    print("Resultado completo salvo em resultado_psf.json")


if __name__ == "__main__":
    main()
