"""
Scraper de status de navios - ZP21 Práticos (Itajaí/Navegantes) - v2
=====================================================================

Agora o script lê TRÊS tabelas da página, não só uma, porque um navio
pode estar em situações diferentes:

- "Navios Previstos"  -> ainda a caminho, com previsão de chegada (ETA)
- "Navios Fundeados"   -> já chegou na região e está ancorado, esperando vaga
- "Navios Atracados"   -> já está atracado no cais

Para cada navio da lista de vocês, o script busca nas três tabelas e retorna
o status mais concreto que encontrar (Atracado > Fundeado > Previsto).

Como usar:
    python scraper_zp21.py navios.txt

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

TABELAS_ALVO = {
    "navios previstos": "Previsto",
    "navios fundeados": "Fundeado",
    "navios atracados": "Atracado",
}

PRIORIDADE_STATUS = {"Atracado": 3, "Fundeado": 2, "Previsto": 1}


def normalizar(texto: str) -> str:
    texto = texto.strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    texto = re.sub(r"\s+", " ", texto)
    return texto


def parse_data_hora(texto: str):
    texto = (texto or "").strip()
    for formato in ("%d/%m/%Y - %H:%M", "%d/%m/%Y-%H:%M"):
        try:
            return datetime.strptime(texto, formato)
        except ValueError:
            continue
    return None


def extrair_tabela(soup, titulo_procurado):
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "strong"]):
        if titulo_procurado in tag.get_text(strip=True).lower():
            return tag.find_next("table")
    return None


def tabela_para_dicts(tabela):
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


def buscar_status_navios():
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

            eta_texto = (
                registro.get("previsão de chegada")
                or registro.get("previsao de chegada")
                or registro.get("data - hora")
                or registro.get("data-hora")
                or ""
            )
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

    with open("resultado_zp21.json", "w", encoding="utf-8") as f:
        json.dump(resultado, f, indent=2, ensure_ascii=False)

    print(f"{len(encontrados)} navio(s) encontrado(s) e atualizado(s).")
    for item in encontrados:
        print(f"  - {item['navio_planilha']}: status = {item['status']}")
    if nao_encontrados:
        print(f"{len(nao_encontrados)} navio(s) da lista NÃO encontrados em nenhuma das 3 tabelas:")
        for n in nao_encontrados:
            print(f"  - {n}")
    print("Resultado completo salvo em resultado_zp21.json")


if __name__ == "__main__":
    main()
