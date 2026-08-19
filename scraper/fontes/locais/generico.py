"""Coletor genérico de agenda de centro de exposições, dirigido por config/locais.json.

Ampliar a cobertura para outras capitais não deveria exigir um parser novo por cidade.
Este módulo lê a lista de locais do config e tenta, em ordem: os seletores informados,
depois uma descoberta automática dos cartões repetidos da página.

A parte chata é a data. Vários calendários listam só o nome do evento e escondem a data
dentro da página de cada um — e evento sem data é inútil aqui: não dá para saber se já
passou, nem para priorizar quem abre primeiro. Por isso, quando o cartão não traz data,
seguimos o link (uma requisição por evento, com cache longo).

Uma feira de capital regional tem, em média, menos expositor chinês que uma de São Paulo:
a densidade vem do setor (industrial, eletrônico, construção), não do tamanho da cidade.
Mas o custo de monitorar é baixo e o detector nos dirá, com dado, onde compensa.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ...core.datas import interpretar_periodo
from ...core.http import Bloqueado, FalhouDeVerdade, buscar
from ...core.modelos import normalizar_texto
from ...core.store import RAIZ, ler_json

CONFIG = RAIZ / "config" / "locais.json"

MIN_EVENTOS = 3
MAX_FICHAS = 40   # teto de visitas por local numa rodada

# Texto que não é nome de evento.
RUIDO = re.compile(
    r"^(agenda|eventos?|home|in[íi]cio|ver mais|saiba mais|todos|pr[óo]xim|anterior|"
    r"newsletter|contato|sobre|institucional|imprensa|facebook|instagram|youtube|"
    r"menu|buscar|filtrar|categoria|arquivos?)$",
    re.IGNORECASE,
)


def _plausivel(nome: str) -> bool:
    return bool(nome) and 3 < len(nome) <= 110 and not RUIDO.match(nome.strip())


def _itens_por_seletor(sopa: BeautifulSoup, local: dict, base: str) -> list[tuple[str, str]]:
    achados = []
    seletor = local.get("seletor_item")
    if not seletor:
        return achados
    for cartao in sopa.select(seletor):
        titulo_el = (cartao.select_one(local["seletor_titulo"])
                     if local.get("seletor_titulo") else
                     cartao.select_one("h1, h2, h3, h4, a"))
        if not titulo_el:
            continue
        nome = normalizar_texto(titulo_el.get_text(" "))
        if not _plausivel(nome):
            continue
        link_el = titulo_el if titulo_el.name == "a" else cartao.select_one("a[href]")
        href = link_el.get("href") if link_el else ""
        achados.append((nome, urljoin(base, href) if href else "", normalizar_texto(cartao.get_text(" "))))
    return achados


def _itens_por_descoberta(sopa: BeautifulSoup, base: str) -> list[tuple[str, str]]:
    """Sem seletor no config: procura links que compartilham o mesmo prefixo."""
    prefixos = Counter()
    for a in sopa.select("a[href]"):
        caminho = urlparse(a.get("href", "")).path.rstrip("/")
        pai = caminho.rsplit("/", 1)[0]
        if pai and pai not in ("", "/"):
            prefixos[pai] += 1
    if not prefixos:
        return []

    prefixo, quantos = prefixos.most_common(1)[0]
    if quantos < MIN_EVENTOS:
        return []
    if not re.search(r"event|agenda|feira|expo", prefixo, re.IGNORECASE):
        return []

    achados, vistos = [], set()
    for a in sopa.select("a[href]"):
        caminho = urlparse(a.get("href", "")).path.rstrip("/")
        if caminho.rsplit("/", 1)[0] != prefixo:
            continue
        nome = normalizar_texto(a.get_text(" "))
        if not _plausivel(nome) or nome.lower() in vistos:
            continue
        vistos.add(nome.lower())
        pai = a.find_parent(["article", "li", "div"])
        contexto = normalizar_texto(pai.get_text(" ")) if pai else nome
        achados.append((nome, urljoin(base, a.get("href", "")), contexto))
    return achados


def _data_da_ficha(url: str, hoje: date) -> tuple[str, str]:
    """Abre a página do evento só para achar o período."""
    if not url:
        return "", ""
    try:
        html = buscar(url, ttl_horas=24 * 7, tentativas=1, timeout=15)
    except (Bloqueado, FalhouDeVerdade):
        return "", ""
    sopa = BeautifulSoup(html, "lxml")
    for tag in sopa(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    texto = normalizar_texto(sopa.get_text(" "))[:3000]
    return interpretar_periodo(texto, hoje)


def coletar_local(local: dict, hoje: date | None = None) -> list[dict]:
    from .agenda import _montar

    hoje = hoje or date.today()
    url = local["url"]

    try:
        html = buscar(url, ttl_horas=12, timeout=25)
    except (Bloqueado, FalhouDeVerdade):
        if not local.get("navegador"):
            return []
        html = ""

    if (not html or len(html) < 3000) and local.get("navegador"):
        from ...core.navegador import ler_pagina
        try:
            html = ler_pagina(url, esperar_ms=6000, rolar=3)
        except Exception:  # noqa: BLE001
            return []

    sopa = BeautifulSoup(html, "lxml")
    itens = _itens_por_seletor(sopa, local, url) or _itens_por_descoberta(sopa, url)
    if len(itens) < MIN_EVENTOS:
        return []

    eventos = []
    visitas = 0
    for nome, link, contexto in itens:
        inicio, fim = interpretar_periodo(contexto, hoje)
        if not inicio and local.get("data_na_ficha") and visitas < MAX_FICHAS:
            visitas += 1
            inicio, fim = _data_da_ficha(link, hoje)
        texto_data = f"{inicio} a {fim}" if inicio else ""

        eventos.append(_montar(
            nome, local["nome"], local.get("cidade", ""), local.get("uf", ""), url,
            data_texto=texto_data, pagina_local=link, hoje=hoje,
        ))
    return eventos


def coletar_todos_configurados(hoje: date | None = None) -> tuple[list[dict], dict[str, str]]:
    dados = ler_json(CONFIG, {"locais": []})
    eventos: list[dict] = []
    situacao: dict[str, str] = {}

    for local in dados.get("locais", []):
        chave = local.get("nome", "?")
        try:
            achados = coletar_local(local, hoje)
            eventos.extend(achados)
            situacao[chave] = f"ok: {len(achados)} eventos"
        except Exception as exc:  # noqa: BLE001 - um local ruim não derruba os outros
            situacao[chave] = f"falhou: {type(exc).__name__}: {exc}"
    return eventos, situacao
