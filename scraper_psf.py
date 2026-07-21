"""
Scraper de status de navios - Praticagem São Francisco (São Francisco do Sul / Itapoá)
========================================================================================

Estrutura real dessa página (descoberta depurando passo a passo): existe UMA
ÚNICA tabela de dados, mas com várias seções dentro dela (Movimentações,
Navios Atracados, Navios Fundeados (Internos), Navios Fundeados (Barra),
Navios Esperados). Cada seção aparece como:

    [linha com só 1 célula: título da seção, ex: "Navios Atracados"]
    [linha de cabeçalho: Navio | Nrº IMO | Tipo de Navio | ... | Situação]
    [linhas de dados de navios daquela seção]
    (repete para a próxima seção)

Ou seja, o cabeçalho "reaparece" no meio da tabela toda vez que uma nova seção
começa. Um mesmo navio pode aparecer em mais de uma seção ao mesmo tempo.

Este script:
1. Usa Playwright para renderizar a página (o conteúdo carrega via JavaScript)
2. Lê a tabela linha por linha, atualizando o "cabeçalho atual" toda vez que
   encontra uma linha de cabeçalho, e tratando as linhas seguintes como dados
   daquele cabeçalho até o próximo cabeçalho aparecer
3. Junta tudo numa lista única por navio, preferindo a versão com mais campos
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
    aninhada mais profunda). Funciona mesmo com <tbody> inserido pelo navegador.
    """
    return [tr for tr in tabela.find_all("tr") if tr.find_parent("table") is tabela]


def celulas_da_linha(tr):
    """Pega o texto de cada célula FILHA DIRETA dessa linha."""
    return [c.get_text(strip=True) for c in tr.find_all(["th", "td"], recursive=False)]


def eh_linha_de_cabecalho(celulas_normalizadas):
    tem_navio_exato = any(c == "NAVIO" for c in celulas_normalizadas)
    score = sum(1 for c in celulas_normalizadas for kw in PALAVRAS_CHAVE if kw in c)
    return tem_navio_exato and score >= 3


def montar_registro(headers_atuais, celulas):
    registro_bruto = dict(zip(headers_atuais, celulas))

    nome = (registro_bruto.get("navio") or "").strip()
    if not nome:
        return None

    data_chegada_texto = registro_bruto.get("data de chegada") or ""
    data_chegada_dt = parse_data_hora(data_chegada_texto)

    data_manobra_texto = registro_bruto.get("data de manobra") or ""
    data_manobra_dt = parse_data_hora(data_manobra_texto)

    return {
        "navio": nome,
        "imo": registro_bruto.get("nrº imo") or registro_bruto.get("nº imo") or registro_bruto.get("imo"),
        "agencia": registro_bruto.get("agência") or registro_bruto.get("agencia"),
        "data_chegada_texto": data_chegada_texto or None,
        "data_chegada_iso": data_chegada_dt.isoformat() if data_chegada_dt else None,
        "manobra": registro_bruto.get("manobra") or None,
        "data_manobra": data_manobra_texto or None,
        "data_manobra_iso": data_manobra_dt.isoformat() if data_manobra_dt else None,
        "berco": registro_bruto.get("berço") or registro_bruto.get("berco"),
        "situacao": registro_bruto.get("situação") or registro_bruto.get("situacao") or None,
    }


def extrair_registros_de_tabela_com_secoes(tabela):
    """
    Percorre a tabela linha a linha. Toda vez que encontra uma linha de
    cabeçalho (Navio, Nrº IMO, ...), passa a usar esse cabeçalho para
    interpretar as linhas seguintes, até o próximo cabeçalho aparecer.

    Linhas com poucas células (ex: "Navios Atracados", "Navios Esperados")
    são títulos de seção - guardamos esse texto para marcar cada navio
    seguinte com a seção de onde ele veio (isso é o que permite ao painel
    saber se um navio já está de fato ATRACADO, por exemplo).
    """
    registros = []
    headers_atuais = None
    secao_atual = None

    for tr in linhas_diretas_da_tabela(tabela):
        celulas = celulas_da_linha(tr)
        if not celulas:
            continue

        normalizadas = [normalizar(c) for c in celulas]

        if eh_linha_de_cabecalho(normalizadas):
            headers_atuais = [c.lower() for c in celulas]
            continue

        if len(celulas) <= 2:
            # provável título de seção (ex: "Navios Atracados")
            secao_atual = celulas[0].strip()
            continue

        if headers_atuais is None or len(celulas) != len(headers_atuais):
            continue  # linha decorativa - ignora

        registro = montar_registro(headers_atuais, celulas)
        if registro:
            registro["secao"] = secao_atual
            registros.append(registro)

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
                htmls.append(frame.content())
            except Exception as e:
                print(f"[debug] não consegui ler o frame {i}: {e}", file=sys.stderr)

        navegador.close()
        return htmls


def buscar_movimentacoes():
    htmls = obter_htmls_de_todos_os_frames()

    navios_por_chave = {}

    for html in htmls:
        soup = BeautifulSoup(html, "lxml")
        todas_tabelas = soup.find_all("table")
        print(f"[debug] {len(todas_tabelas)} <table> no total neste frame.", file=sys.stderr)

        for i, tabela in enumerate(todas_tabelas):
            registros = extrair_registros_de_tabela_com_secoes(tabela)
            print(f"[debug] tabela {i}: {len(registros)} navio(s) extraído(s).", file=sys.stderr)

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

    resultado = {
        "gerado_em": datetime.now().isoformat(),
        "navios": navios_site,
    }

    with open("resultado_psf.json", "w", encoding="utf-8") as f:
        json.dump(resultado, f, indent=2, ensure_ascii=False)

    print(f"{len(navios_site)} navio(s) publicado(s) em resultado_psf.json")


if __name__ == "__main__":
    main()
