"""
Scraper de previsão de chegada de navios - ZP21 Práticos (Itajaí/Navegantes)
=============================================================================

O que este script faz:
1. Acessa a página pública de movimentação de navios do ZP21
   (https://praticoszp21.com.br/movimentacao-de-navios/)
2. Lê a tabela "Navios Previstos" (nome do navio + previsão de chegada)
3. Cruza com a lista de navios que a empresa está acompanhando
   (vinda da planilha semanal) usando comparação aproximada de nomes
   (útil pois o nome pode vir com grafia levemente diferente)
4. Gera um arquivo JSON com o resultado: navios encontrados (com ETA)
   e navios da lista que não foram encontrados na página (para revisão manual)

Como usar:
    python scraper_zp21.py caminho/para/navios_acompanhados.txt

O arquivo .txt de entrada deve ter um nome de navio por linha, exatamente
como aparece na planilha (ex: MSC AVNI, LOG-IN ENDURANCE, etc).
Se nenhum arquivo for passado, o script só imprime tudo que encontrou no site.

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

# Navegadores de verdade mandam esse cabeçalho; alguns sites bloqueiam
# requisições sem User-Agent por padrão.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def normalizar(texto: str) -> str:
    """Remove acentos, espaços extras e deixa em maiúsculas, para comparar nomes."""
    texto = texto.strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    texto = re.sub(r"\s+", " ", texto)
    return texto


def parse_data_hora(texto: str):
    """Converte 'dd/mm/aaaa - HH:MM' em datetime. Retorna None se não der para converter."""
    texto = texto.strip()
    try:
        return datetime.strptime(texto, "%d/%m/%Y - %H:%M")
    except ValueError:
        return None


def buscar_navios_previstos():
    """Busca a tabela 'Navios Previstos' na página do ZP21 e retorna uma lista de dicts."""
    resp = requests.get(URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    # Estratégia: procurar o título "Navios Previstos" (pode estar em h2/h3/strong)
    # e pegar a primeira <table> que aparece depois dele. Isso é mais resistente
    # a mudanças de layout do que "pegar a tabela de índice X".
    titulo = None
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "strong"]):
        if "navios previstos" in tag.get_text(strip=True).lower():
            titulo = tag
            break

    if titulo is None:
        raise RuntimeError(
            "Não encontrei o título 'Navios Previstos' na página. "
            "O site pode ter mudado a estrutura - me avise para eu ajustar."
        )

    tabela = titulo.find_next("table")
    if tabela is None:
        raise RuntimeError("Encontrei o título 'Navios Previstos' mas nenhuma tabela depois dele.")

    # Cabeçalhos esperados: Navio | Loa | Calado | Rota | Previsão de chegada | Rebocadores
    headers_tabela = [th.get_text(strip=True).lower() for th in tabela.find_all("th")]

    navios = []
    for linha in tabela.find_all("tr")[1:]:  # pula o cabeçalho
        colunas = [td.get_text(strip=True) for td in linha.find_all("td")]
        if not colunas or len(colunas) < 2:
            continue

        registro = dict(zip(headers_tabela, colunas))

        nome = registro.get("navio", "").strip()
        eta_texto = registro.get("previsão de chegada") or registro.get("previsao de chegada") or ""
        eta_dt = parse_data_hora(eta_texto)

        if nome:
            navios.append({
                "navio": nome,
                "previsao_chegada_texto": eta_texto,
                "previsao_chegada_iso": eta_dt.isoformat() if eta_dt else None,
                "calado": registro.get("calado"),
                "rota": registro.get("rota"),
            })

    return navios


def cruzar_com_lista(navios_site, navios_acompanhados, limiar=0.82):
    """
    Faz o "match" entre os navios do site e os navios que a empresa acompanha.
    Usa comparação aproximada (fuzzy) porque nomes podem vir com grafia
    levemente diferente entre a planilha da empresa e o site do prático.
    """
    encontrados = []
    nomes_site_normalizados = {normalizar(n["navio"]): n for n in navios_site}

    nao_encontrados = []

    for nome_buscado in navios_acompanhados:
        chave_buscada = normalizar(nome_buscado)

        # 1. tenta igualdade exata primeiro
        if chave_buscada in nomes_site_normalizados:
            match = nomes_site_normalizados[chave_buscada]
            encontrados.append({"navio_planilha": nome_buscado, **match})
            continue

        # 2. senão, tenta a melhor aproximação (ex: pequenas diferenças de grafia)
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
    navios_site = buscar_navios_previstos()

    if len(sys.argv) < 2:
        print(json.dumps(navios_site, indent=2, ensure_ascii=False))
        return

    caminho_lista = sys.argv[1]
    with open(caminho_lista, encoding="utf-8") as f:
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
    if nao_encontrados:
        print(f"{len(nao_encontrados)} navio(s) da lista NÃO encontrados no site:")
        for n in nao_encontrados:
            print(f"  - {n}")
    print("Resultado completo salvo em resultado_zp21.json")


if __name__ == "__main__":
    main()
