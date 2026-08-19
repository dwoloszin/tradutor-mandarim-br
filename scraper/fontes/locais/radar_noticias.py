"""Radar de feiras novas a partir da imprensa do setor (portaleventos.com.br).

Este portal não é um catálogo de eventos e não tem lista de expositores — é um site de
notícias. Mas é ele que anuncia feira nova antes de ela existir em qualquer agenda de
centro de exposições, e cobre o Brasil inteiro, não só São Paulo. Nas manchetes de hoje,
por exemplo, aparecem "Feicon Rio" e "Febrava Rio" no Riocentro, que não estão em
nenhuma das agendas que monitoramos.

Por isso ele entra como **radar**, não como fonte de dados: extraímos pistas
(nome + data + local) para `data/radar_feiras.jsonl`. Você revisa e promove a que
interessar para config/feiras_prioritarias.json. Nada daqui vira evento automaticamente:
texto de notícia é ambíguo demais para virar dado sem revisão humana.
"""
from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup

from ...core.datas import interpretar_periodo
from ...core.http import Bloqueado, FalhouDeVerdade, buscar
from ...core.modelos import chave_feira, normalizar_texto
from ...core.store import Tabela, agora_iso

PAGINAS = [
    "https://www.portaleventos.com.br/Canal/FEIRAS",
    "https://www.portaleventos.com.br/Canal/AGENDA",
    "https://www.portaleventos.com.br/Canal/EVENTOS-MEGA",
]

# Locais de exposição relevantes fora de São Paulo — é o que queremos descobrir aqui.
LOCAIS_CONHECIDOS = [
    ("Riocentro", "Rio de Janeiro", "RJ"),
    ("Expo Mag", "Rio de Janeiro", "RJ"),
    ("Sulamérica", "Rio de Janeiro", "RJ"),
    ("Expominas", "Belo Horizonte", "MG"),
    ("Expominas BH", "Belo Horizonte", "MG"),
    ("Fiergs", "Porto Alegre", "RS"),
    ("Centro de Eventos do Ceará", "Fortaleza", "CE"),
    ("Centro de Convenções de Salvador", "Salvador", "BA"),
    ("Expoville", "Joinville", "SC"),
    ("Centro de Eventos de Florianópolis", "Florianópolis", "SC"),
    ("Expo Unimed", "Curitiba", "PR"),
    ("Positivo", "Curitiba", "PR"),
    ("Centro de Convenções de Pernambuco", "Recife", "PE"),
    ("Ulysses Guimarães", "Brasília", "DF"),
    ("São Paulo Expo", "São Paulo", "SP"),
    ("Expo Center Norte", "São Paulo", "SP"),
    ("Distrito Anhembi", "São Paulo", "SP"),
    ("Transamerica Expo", "São Paulo", "SP"),
]

# "será realizado de 6 a 8 de outubro", "acontece de 12 a 15 de novembro"
TRECHO_DATA = re.compile(
    r"(?:de|entre|dias?)\s+(\d{1,2}\s*(?:a|até|e|-|–)\s*\d{1,2}\s+de\s+\w+"
    r"(?:\s+de\s+\d{4})?)",
    re.IGNORECASE,
)

# nomes de feira costumam vir em maiúscula inicial, 1 a 4 palavras, antes do verbo
NOME_FEIRA = re.compile(
    r"\b([A-Z][\wÀ-ÿ&.-]{2,}(?:\s+[A-Z][\wÀ-ÿ&.-]{1,}){0,3})\s+"
    r"(?:anuncia|abre|estreia|reunirá|reúne|acontece|será|chega|lança|recebe|apresenta)",
)


# O texto da chamada vem grudado com o nome do canal e a data de publicação
# ("EVENTOS MEGA 14/09/2023 Fulano..."). Isso não é nome de feira.
RUIDO_INICIO = re.compile(
    r"^(EVENTOS MEGA|EVENTOS|FEIRAS|AGENDA|INTERNACIONAL|PORTAL EVENTOS TV|"
    r"HOTELARIA|TURISMO|GERAL|ESPAÇOS|Destinos|Entidades|Colunistas)\s*"
    r"(\d{2}/\d{2}/\d{4})?\s*",
    re.IGNORECASE,
)


def _limpar_nome(nome: str) -> str:
    anterior = None
    while anterior != nome:
        anterior = nome
        nome = RUIDO_INICIO.sub("", nome).strip()
    # sobrou só uma data ou um pedaço curto? não serve
    if re.fullmatch(r"[\d/\s.-]*", nome):
        return ""
    return nome


def _localizar(texto: str) -> tuple[str, str, str]:
    for nome, cidade, uf in LOCAIS_CONHECIDOS:
        if nome.lower() in texto.lower():
            return nome, cidade, uf
    return "", "", ""


def _pistas_da_pagina(url: str, hoje: date) -> list[dict]:
    try:
        html = buscar(url, ttl_horas=12, tentativas=1, timeout=20)
    except (Bloqueado, FalhouDeVerdade):
        return []

    sopa = BeautifulSoup(html, "lxml")
    for tag in sopa(["script", "style"]):
        tag.decompose()

    pistas = []
    # cada notícia é um link /news/... com o texto da chamada ao redor
    for link in sopa.select("a[href*='/news/']"):
        bloco = normalizar_texto(link.get_text(" "))
        if len(bloco) < 30:
            continue

        local, cidade, uf = _localizar(bloco)
        achado_data = TRECHO_DATA.search(bloco)
        if not (local or achado_data):
            continue  # sem data nem local, não é pista de evento

        inicio, fim = interpretar_periodo(achado_data.group(1), hoje) if achado_data else ("", "")

        achado_nome = NOME_FEIRA.search(bloco)
        nome = normalizar_texto(achado_nome.group(1)) if achado_nome else ""
        if not nome:
            # sem verbo reconhecido, usa o começo da manchete
            nome = normalizar_texto(bloco.split(".")[0])[:60]
        nome = _limpar_nome(nome)
        if len(nome) < 3:
            continue

        pistas.append({
            "id": f"radar:{chave_feira(nome)}",
            "nome": nome,
            "manchete": bloco[:300],
            "data_inicio": inicio,
            "data_fim": fim,
            "local_citado": local,
            "cidade": cidade,
            "uf": uf,
            "url_noticia": "https://www.portaleventos.com.br" + (link.get("href") or ""),
            "fonte": url,
            "revisado": False,
            "visto_em": agora_iso(),
        })
    return pistas


def coletar(hoje: date | None = None) -> dict:
    """Varre o portal e grava as pistas em data/radar_feiras.jsonl."""
    hoje = hoje or date.today()
    tabela = Tabela("radar_feiras").carregar()

    novas = 0
    total = 0
    for pagina in PAGINAS:
        for pista in _pistas_da_pagina(pagina, hoje):
            total += 1
            if tabela.obter(pista["id"]) is None:
                novas += 1
            tabela.upsert(pista)

    tabela.salvar()
    return {
        "pistas_encontradas": total,
        "pistas_novas": novas,
        "total_no_radar": len(tabela),
        "arquivo": "data/radar_feiras.jsonl",
    }
