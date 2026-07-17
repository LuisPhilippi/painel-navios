"""
Scraper de status de navios - Praticagem São Francisco (São Francisco do Sul / Itapoá)
========================================================================================

Estrutura real dessa página (descoberta durante os testes): existe uma tabela
"de fora" que só organiza as abas visuais, e DENTRO dela ficam várias tabelas
menores, uma para cada seção:

    Movimentações | Navios Atracados | Navios Fundeados (Internos) |
    Navios Fundeados (Barra) | Navios Esperados

Cada uma dessas tabelas tem seu próprio cabeçalho (Navio, Nrº IMO, Tipo de
Navio, Agência, ..., Situação) e suas próprias linhas de navios. Um mesmo navio
pode aparecer em mais de uma seção (ex: em "Movimentações" e em "Navios
Atracados" ao mesmo tempo).

Este script:
1. Usa Playwright para renderizar a página (o conteúdo carrega via JavaScript)
2. Lê CADA linha usando apenas os filhos DIRETOS da linha (recursive=False),
   para não "vazar" e misturar com tabelas aninhadas dentro dela
3. Encontra TODAS as tabelas de navios (uma por seção), não só uma
4. Junta tudo numa lista única por navio, preferindo a versão com mais campos
   preenchidos quando o mesmo navio aparece em mais de uma seção

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

PALAVRAS_CHAVE = ["NAVIO", "IMO", "CHEGADA", "MANOBRA", "SITUACAO", "BERCO", "CALLSIGN", "CALADO", "AGENCIA"]


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


def linhas_diretas_da_tabela(tabela):
    """
    Retorna as linhas (<tr>) que pertencem DIRETAMENTE a essa tabela - ou seja,
    cuja tabela mais próxima acima delas é exatamente essa (não uma tabela
    aninhada mais profunda). Isso funciona mesmo que o navegador insira
    automaticamente um <tbody> no meio (o que ele faz sempre), porque olha
    para a tabela ancestral mais próxima, não para o "pai direto" literal.
    """
    return [tr for tr in tabela.find_all("tr") if tr.find_parent("table") is tabela]


def celulas_da_linha(tr):
    """Pega o texto de cada célula FILHA DIRETA dessa linha (recursive=False)."""
    return [c.get_text(strip=True) for c in tr.find_all(["th", "td"], recursive=False)]


def encontrar_todas_tabelas_de_navios(soup):
    """
    Retorna uma lista de (tabela, headers) para TODAS as tabelas da página
    (incluindo aninhadas) cuja primeira linha tenha uma célula exatamente
    igual a "Navio" - ou seja, cada seção (Movimentações, Atracados, etc)
    vira um item separado nessa lista.
    """
    todas_tabelas = soup.find_all("table")
    print(f"[debug] {len(todas_tabelas)} <table> no total nesta página/frame.", file=sys.stderr)

    encontradas = []
    for i, tabela in enumerate(todas_tabelas):
        linhas = linhas_diretas_da_tabela(tabela)
        if not linhas:
            print(f"[debug] tabela {i}: 0 linha(s) direta(s) (provavelmente vazia ou só decorativa).", file=sys.stderr)
            continue

        primeira_linha = celulas_da_linha(linhas[0])
        normalizadas = [normalizar(c) for c in primeira_linha]

        tem_navio_exato = any(n == "NAVIO" for n in normalizadas)
        score = sum(1 for n in normalizadas for kw in PALAVRAS_CHAVE if kw in n)

        amostra = [c[:30] for c in primeira_linha[:8]]
        print(
            f"[debug] tabela {i}: {len(linhas)} linha(s) direta(s), "
            f"tem_navio_exato={tem_navio_exato}, score={score}, "
            f"1ª linha (amostra): {amostra}",
            file=sys.stderr,
        )

        if tem_navio_exato and score >= 3:
            headers_tabela = [c.lower() for c in primeira_linha]
            encontradas.append((tabela, headers_tabela))

    return encontradas


def extrair_registros_da_tabela(tabela, headers_tabela):
    registros = []
    for tr in linhas_diretas_da_tabela(tabela)[1:]:
        colunas = celulas_da_linha(tr)
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
            "imo": registro.get("nrº imo") or registro.get("nº imo") or registro.get("imo"),
            "agencia": registro.get("agência") or registro.get("agencia"),
            "data_chegada_texto": data_chegada_texto or None,
            "data_chegada_iso": data_chegada_dt.isoformat() if data_chegada_dt else None,
            "manobra": registro.get("manobra") or None,
            "data_manobra": registro.get("data de manobra") or None,
            "berco": registro.get("berço") or registro.get("berco"),
            "situacao": registro.get("situação") or registro.get("situacao") or None,
        })

    return registros


def campos_preenchidos(registro):
    """Conta quantos campos (fora 'navio') têm valor - usado para escolher a versão mais completa de um navio."""
    return sum(1 for k, v in registro.items() if k != "navio" and v)


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
                htmls.append(conteudo)
            except Exception as e:
                print(f"[debug] não consegui ler o frame {i}: {e}", file=sys.stderr)

        navegador.close()
        return htmls


def buscar_movimentacoes():
    htmls = obter_htmls_de_todos_os_frames()

    navios_por_chave = {}

    for html in htmls:
        soup = BeautifulSoup(html, "lxml")
        tabelas = encontrar_todas_tabelas_de_navios(soup)
        print(f"[debug] {len(tabelas)} seção(ões) de navios encontrada(s) neste frame.", file=sys.stderr)

        for tabela, headers_tabela in tabelas:
            registros = extrair_registros_da_tabela(tabela, headers_tabela)
            print(f"[debug]   seção com cabeçalho {headers_tabela[:3]}...: {len(registros)} navio(s)", file=sys.stderr)

            for registro in registros:
                chave = normalizar(registro["navio"])
                existente = navios_por_chave.get(chave)
                if existente is None or campos_preenchidos(registro) > campos_preenchidos(existente):
                    navios_por_chave[chave] = registro

    return list(navios_por_chave.values())


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

    print(f"[debug] {len(navios_site)} navio(s) únicos lidos no total.", file=sys.stderr)
    for n in navios_site[:30]:
        print(f"[debug]   - {n['navio']} | situação: {n.get('situacao')}", file=sys.stderr)

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
