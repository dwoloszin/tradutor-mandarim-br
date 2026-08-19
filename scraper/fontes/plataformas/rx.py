"""Adaptador RX Global (Reed Exhibitions) — FEICON, FEBRAVA, FENATRAN, AUTOMEC e outras.

Os sites da RX são AEM com um componente React "exhibitor-directory" que busca num
índice Algolia. A página traz, em texto puro, a configuração do componente: appId e
a chave de busca do Algolia (uma search-only key, pública por natureza — é a mesma
que qualquer visitante usa) e os identificadores do evento.

Então o adaptador é: ler a config da página -> consultar o índice Algolia -> paginar.
Nada de engenharia reversa frágil: se a RX trocar as chaves, lemos as novas na página.
"""
from __future__ import annotations

import json
import re

import requests

from ...core.http import Bloqueado, FalhouDeVerdade, buscar
from ...core.modelos import normalizar_texto

PADRAO_CONFIG = re.compile(r'var reactSettings = JSON\.parse\("(.*?)"\);', re.S)

POR_PAGINA = 1000  # limite do Algolia por consulta


def _decodificar_config(html: str) -> dict:
    achado = PADRAO_CONFIG.search(html)
    if not achado:
        raise FalhouDeVerdade("(página RX)", "não achei reactSettings na página")
    texto = achado.group(1).replace("\\x22", '"').replace("\\/", "/")
    texto = texto.encode().decode("unicode_escape", errors="ignore")
    try:
        return json.loads(texto).get("props", {})
    except json.JSONDecodeError as exc:
        raise FalhouDeVerdade("(página RX)", f"reactSettings ilegível: {exc}") from exc


def ler_config(url_expositores: str) -> dict:
    """Extrai da página tudo que precisamos para consultar o índice."""
    html = buscar(url_expositores, ttl_horas=24)
    props = _decodificar_config(html)

    algolia = props.get("algoliaConfig") or {}
    contexto = props.get("context") or {}
    navegacao = props.get("navigation") or {}

    app_id = algolia.get("appId")
    chave = algolia.get("apiKey")
    id_evento = contexto.get("eventId")
    id_edicao = contexto.get("eventEditionId")

    if not all([app_id, chave, id_evento, id_edicao]):
        raise FalhouDeVerdade(url_expositores, "config do Algolia incompleta na página")

    return {
        "app_id": app_id,
        "chave": chave,
        "event_id": id_evento,
        "edicao_id": id_edicao,
        "nome_edicao": contexto.get("eventEditionName") or "",
        "locale": (contexto.get("primaryLocale") or "pt-BR").lower(),
        "url_ficha": navegacao.get("exhibitorPublicDetailsUrlFormat") or "",
        "url_expositores": url_expositores,
    }


def _consultar(config: dict, pagina: int) -> dict:
    url = f"https://{config['app_id'].lower()}-dsn.algolia.net/1/indexes/evt-{config['event_id'].removeprefix('evt-')}-index/query"
    filtros = (
        f"recordType:exhibitor AND locale:{config['locale']} "
        f"AND eventEditionId:{config['edicao_id']}"
    )
    params = (
        f"query=&page={pagina}&hitsPerPage={POR_PAGINA}"
        f"&filters={requests.utils.quote(filtros)}&typoTolerance=false"
    )
    cabecalhos = {
        "X-Algolia-API-Key": config["chave"],
        "X-Algolia-Application-Id": config["app_id"],
        "Content-Type": "application/json",
    }
    try:
        resposta = requests.post(url, headers=cabecalhos, json={"params": params}, timeout=30)
    except requests.RequestException as exc:
        raise FalhouDeVerdade(url, f"erro de rede: {exc}") from exc

    if resposta.status_code in (401, 403, 429):
        raise Bloqueado(url, f"HTTP {resposta.status_code}")
    if resposta.status_code >= 400:
        raise FalhouDeVerdade(url, f"HTTP {resposta.status_code}: {resposta.text[:150]}")
    return resposta.json()


def _normalizar(hit: dict, config: dict) -> dict:
    nome = normalizar_texto(hit.get("exhibitorName") or hit.get("companyName"))
    ficha = ""
    if config.get("url_ficha") and hit.get("id"):
        ficha = config["url_ficha"].replace("{0}", str(hit["id"]))

    produtos = []
    for p in hit.get("products") or []:
        if isinstance(p, str):
            produtos.append(p)
        elif isinstance(p, dict) and p.get("name"):
            produtos.append(p["name"])

    return {
        "nome": nome,
        "website": "",  # a RX não publica o site no índice; vem do enriquecimento
        "emails": [hit["email"]] if hit.get("email") else [],
        "pais": normalizar_texto(hit.get("countryName")),
        "cidade": "",
        "endereco": "",
        "stand": normalizar_texto(hit.get("standReference")),
        "categorias": [c for c in (hit.get("exhibitorFilters") or []) if isinstance(c, str)],
        "produtos": produtos[:30],
        "descricao": "",
        "ficha_feira": ficha,
        "fonte_plataforma": "rx",
        "fonte_url": config.get("url_expositores", ""),
        "id_plataforma": hit.get("id") or hit.get("objectID") or "",
    }


def coletar(url_expositores: str) -> dict:
    """Baixa todos os expositores de uma feira RX."""
    config = ler_config(url_expositores)

    expositores: list[dict] = []
    pagina = 0
    total = 0
    while True:
        dados = _consultar(config, pagina)
        total = dados.get("nbHits", total)
        for hit in dados.get("hits") or []:
            expositores.append(_normalizar(hit, config))
        if pagina + 1 >= (dados.get("nbPages") or 1):
            break
        pagina += 1

    return {
        "evento": {
            "titulo": config["nome_edicao"],
            "url_expositores": url_expositores,
            "plataforma": "rx",
        },
        "expositores": expositores,
        "total_informado": total,
    }
