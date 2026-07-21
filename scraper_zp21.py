"""
Scraper de status de navios - ZP21 Práticos (Itajaí/Navegantes) - v3
=====================================================================

Lê QUATRO tabelas da página, porque um navio pode estar em situações
diferentes, e o que mais importa pra quem acompanha o despacho aduaneiro
é a previsão de ATRACAÇÃO, não só a chegada na região:

- "Manobras Previstas" -> tem a previsão REAL de atracação (a mais importante
  para navios que já chegaram e estão fundeados esperando vaga)
- "Navios Previstos"    -> ainda a caminho, com previsão de chegada (ETA)
- "Navios Fundeados"    -> já chegou na região e está ancorado, esperando vaga
- "Navios Atracados"    -> já está atracado no cais

Para cada navio, o script busca nas quatro tabelas e prioriza a informação
mais útil: Manobra Prevista (data real de atracação) > Atracado > Fundeado >
Previsto - ou seja, se um navio está "Fundeado" MAS já tem uma manobra de
atracação prevista, o painel mostra essa data de atracação, não só o fato de
estar fundeado sem previsão nenhuma.

Como usar:
    python scraper_zp21.py

Dependências:
    pip install requests beautifulsoup4 lxml
"""

import sys
import json
import re
import unicodedata
import difflib
from datetime import datetime

import requests
from bs4 import BeautifulSoup

URL = "https://praticoszp21.com.br/movimentacao-de-navios/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# título exato na página -> nome do status que vamos usar no resultado
TABELAS_ALVO = {
    "manobras previstas": "ManobraPrevista",
    "navios previstos": "Previsto",
    "navios fundeados": "Fundeado",
    "navios atracados": "Atracado",
}

# ordem de prioridade quando um navio aparece em mais de uma tabela -
# Manobra Prevista (previsão real de atracação) vence tudo, porque é a
# informação mais concreta e mais útil pra quem acompanha o despacho.
PRIORIDADE_STATUS = {"ManobraPrevista": 4, "Atracado": 3, "Fundeado": 2, "Previsto": 1}

DATA_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
HORA_RE = re.compile(r"(\d{2}:\d{2})")


def normalizar(texto: str) -> str:
    texto = texto.strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    texto = re.sub(r"\s+", " ", texto)
    return texto


def parse_data_hora(texto: str):
    """Extrai data e hora de dentro do texto, em qualquer formatação (mais tolerante que um formato fixo)."""
    if not texto:
        return None
    data_match = DATA_RE.search(texto)
    if not data_match:
        return None
    hora_match = HORA_RE.search(texto)
    hora_str = hora_match.group(1) if hora_match else "00:00"
    try:
        return datetime.strptime(f"{data_match.group(1)} {hora_str}", "%d/%m/%Y %H:%M")
    except ValueError:
        return None


def extrair_tabela(soup, titulo_procurado):
    """Acha o título (h1-h4/strong) que contém titulo_procurado e devolve a tabela seguinte."""
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "strong"]):
        if titulo_procurado in tag.get_text(strip=True).lower():
            tabela = tag.find_next("table")
            return tabela
    return None


def tabela_para_dicts(tabela):
    """Converte uma <table> do BeautifulSoup em uma lista de dicts (chave = cabeçalho em minúsculo)."""
    if tabela is None:
        return []
    headers_tabela = [th.get_text(strip=True).lower() for th in tabela.find_all("th")]
    linhas = []
    for tr in tabela.find_all("tr")[1:]:
        colunas = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not colunas:
            continue
        linhas.append(dict(zip(headers_tabela, colunas)))
    return linhas


def encontrar_data_no_registro(registro):
    """
    Tenta achar a data/hora relevante do registro. Primeiro tenta nomes de
    coluna conhecidos; se não achar, varre TODOS os valores da linha
    procurando qualquer coisa no formato de data - assim não depende de
    adivinhar o nome exato da coluna (que pode mudar de tabela pra tabela).
    """
    candidatos_conhecidos = [
        "previsão de chegada", "previsao de chegada",
        "data - hora", "data-hora", "data",
        "data prevista", "data/hora prevista", "previsão", "previsao",
        "data da manobra", "horário previsto", "horario previsto",
    ]
    for chave in candidatos_conhecidos:
        if registro.get(chave):
            return registro[chave]

    # fallback: procura em qualquer célula da linha algo parecido com uma data
    for valor in registro.values():
        if valor and DATA_RE.search(valor):
            return valor

    return ""


def buscar_status_navios():
    """
    Busca as quatro tabelas e retorna uma lista unificada, um registro por
    navio, já com o status mais útil (se o navio aparecer em mais de uma
    tabela, prioriza Manobra Prevista > Atracado > Fundeado > Previsto).
    """
    resp = requests.get(URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    navios_por_chave = {}

    for titulo_procurado, status in TABELAS_ALVO.items():
        tabela = extrair_tabela(soup, titulo_procurado)
        if tabela is None:
            print(f"[aviso] não encontrei a tabela '{titulo_procurado}' na página.", file=sys.stderr)
            continue

        for registro in tabela_para_dicts(tabela):
            nome = (registro.get("navio") or "").strip()
            if not nome:
                continue

            eta_texto = encontrar_data_no_registro(registro)
            eta_dt = parse_data_hora(eta_texto)

            chave = normalizar(nome)
            candidato = {
                "navio": nome,
                "status": status,
                "data_hora_texto": eta_texto or None,
                "data_hora_iso": eta_dt.isoformat() if eta_dt else None,
                "calado": registro.get("calado"),
                "rota": registro.get("rota"),
                "berco": registro.get("berço") or registro.get("berco"),
                "posicao": registro.get("posicao") or registro.get("posição"),
            }

            existente = navios_por_chave.get(chave)
            if existente is None or PRIORIDADE_STATUS[status] > PRIORIDADE_STATUS[existente["status"]]:
                navios_por_chave[chave] = candidato

    return list(navios_por_chave.values())


def cruzar_com_lista(navios_site, navios_acompanhados, limiar=0.82):
    encontrados = []
    nomes_site_normalizados = {normalizar(n["navio"]): n for n in navios_site}
    nao_encontrados = []

    for nome_buscado in navios_acompanhados:
        chave_buscada = normalizar(nome_buscado)

        if chave_buscada in nomes_site_normalizados:
            match = nomes_site_normalizados[chave_buscada]
            encontrados.append({"navio_planilha": nome_buscado, **match})
            continue

        candidatos = difflib.get_close_matches(
            chave_buscada, nomes_site_normalizados.keys(), n=1, cutoff=limiar
        )
        if candidatos:
            match = nomes_site_normalizados[candidatos[0]]
            encontrados.append({"navio_planilha": nome_buscado, **match})
        else:
            nao_encontrados.append(nome_buscado)

    return encontrados, nao_encontrados


def main():
    navios_site = buscar_status_navios()

    resultado = {
        "gerado_em": datetime.now().isoformat(),
        "navios": navios_site,
    }

    with open("resultado_zp21.json", "w", encoding="utf-8") as f:
        json.dump(resultado, f, indent=2, ensure_ascii=False)

    print(f"{len(navios_site)} navio(s) publicado(s) em resultado_zp21.json")


if __name__ == "__main__":
    main()
