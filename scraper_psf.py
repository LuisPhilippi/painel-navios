"""
Scraper de status de navios - Praticagem São Francisco (São Francisco do Sul / Itapoá)
========================================================================================

Essa página carrega a tabela de navios via JavaScript, dentro de uma estrutura
de abas (Movimentações / Atracados / Fundeados / Esperados). A tabela real de
dados usa células <td> (não <th>) como cabeçalho, e fica aninhada dentro da
tabela de abas. Por isso este script:

1. Usa o Playwright para abrir um navegador robô de verdade e deixar o
   JavaScript rodar
2. Espera um tempo extra de segurança
3. Procura, em TODOS os frames e TODAS as tabelas (incluindo aninhadas), a que
   tem uma célula exatamente igual a "Navio" na primeira linha - evitando
   confundir com tabelas de abas tipo "Navios Atracados", que só têm a palavra
   "navio" dentro de um rótulo maior

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


PALAVRAS_CHAVE = ["NAVIO", "IMO", "CHEGADA", "MANOBRA", "SITUACAO", "BERCO", "CALLSIGN", "CALADO", "AGENCIA"]


def celulas_da_linha(tr):
    """Pega o texto de cada célula da linha, seja ela <th> ou <td>."""
    return [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]


def encontrar_tabela_de_navios(soup):
    """
    Procura, entre TODAS as tabelas da página (incluindo aninhadas), a que tem
    uma célula exatamente igual a "Navio" na primeira linha (cabeçalho real de
    dados) - e não apenas a palavra "navio" em algum lugar, o que evitaria cair
    em tabelas de abas tipo "Navios Atracados", "Navios Esperados" etc, que só
    são rótulos de navegação, não colunas de dados.
    """
    candidatas = []
    for tabela in soup.find_all("table"):
        linhas = tabela.find_all("tr")
        if not linhas:
            continue
        primeira_linha = celulas_da_linha(linhas[0])
        normalizadas = [normalizar(c) for c in primeira_linha]

        tem_navio_exato = any(n == "NAVIO" for n in normalizadas)
        score = sum(1 for n in normalizadas for kw in PALAVRAS_CHAVE if kw in n)

        if tem_navio_exato and score >= 3:
            headers_tabela = [c.lower() for c in primeira_linha]
            candidatas.append((tabela, headers_tabela, len(linhas), score))

    if not candidatas:
        return None, []

    candidatas.sort(key=lambda x: (x[3], x[2]), reverse=True)
    tabela, headers_tabela, _, _ = candidatas[0]
    return tabela, headers_tabela


def obter_htmls_de_todos_os_frames():
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page()
        pagina.goto(URL, wait_until="networkidle", timeout=30000)

        try:
            pagina.wait_for_selector("table", timeout=10000)
        except Exception:
            print("[aviso] nenhuma <table> apareceu em 10s de espera extra.", file=sys.stderr)

        pagina.wait_for_timeout(4000)

        print(f"[debug] a página tem {len(pagina.frames)} frame(s) no total.", file=sys.stderr)

        htmls = []
        for i, frame in enumerate(pagina.frames):
            try:
                conteudo = frame.content()
                qtd_tabelas = conteudo.lower().count("<table")
                print(f"[debug] frame {i} ({frame.url}): {qtd_tabelas} tabela(s) no HTML.", file=sys.stderr)
                htmls.append(conteudo)
            except Exception as e:
                print(f"[debug] não consegui ler o frame {i}: {e}", file=sys.stderr)

        navegador.close()
        return htmls


def buscar_movimentacoes():
    htmls = obter_htmls_de_todos_os_frames()

    melhor_tabela = None
    melhor_headers = []
    melhor_qtd_linhas = 0

    for html in htmls:
        soup_temp = BeautifulSoup(html, "lxml")
        tabela_temp, headers_temp = encontrar_tabela_de_navios(soup_temp)
        if tabela_temp is not None:
            qtd = len(tabela_temp.find_all("tr"))
            print(f"[debug] tabela candidata encontrada com {qtd} linha(s), cabeçalho: {headers_temp}", file=sys.stderr)
            if qtd > melhor_qtd_linhas:
                melhor_tabela, melhor_headers, melhor_qtd_linhas = tabela_temp, headers_temp, qtd

    if melhor_tabela is None or melhor_qtd_linhas <= 1:
        raise RuntimeError(
            "Não encontrei nenhuma tabela com dados de navios (com mais de 1 linha) "
            "em nenhum frame da página, mesmo após renderizar o JavaScript."
        )

    tabela, headers_tabela = melhor_tabela, melhor_headers

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
    for n in navios_site[:20]:
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
