"""Adaptador Noomis — plataforma de eventos da FEBRABAN (Febraban Tech e afins).

Vale como lembrete de método: o raspador genérico já "funcionava" nesta feira, mas o
que ele produzia era pior do que parecia. Ele lia 647 itens de uma lista que tem 324
expositores — quase o dobro, em duplicatas e fragmentos —, e entregava o nome grudado
no estande ("4MATT-A3"), sem separação confiável.

A API por trás da mesma página entrega 324 registros limpos, com nome, pavilhão e
estande em campos próprios. Sempre que existir a API, ela ganha do raspador: não é só
questão de velocidade, é de o dado estar certo.

Dois endpoints, ambos públicos e permitidos pelo robots.txt do host:
  appConfig?friendly_url=<slug>          descobre o id do evento
  getPageExpositor?event_id=<id>         expositores: nome, pavilhão, estande
  getPagePatrocinadores?event_id=<id>    patrocinadores: nome, descrição e SITE
"""
from __future__ import annotations

import requests

from ...core.http import Bloqueado, FalhouDeVerdade
from ...core.modelos import normalizar_texto, normalizar_url

BASE = "https://pc-ap1-hmg.noomis.febraban.org.br/hmg/pages-events/pc"

CABECALHOS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


def _pegar(caminho: str):
    try:
        resposta = requests.get(f"{BASE}/{caminho}", headers=CABECALHOS, timeout=30)
    except requests.RequestException as exc:
        raise FalhouDeVerdade(caminho, f"erro de rede: {exc}") from exc

    if resposta.status_code in (401, 403, 429, 503):
        raise Bloqueado(caminho, f"HTTP {resposta.status_code}")
    if resposta.status_code >= 400:
        raise FalhouDeVerdade(caminho, f"HTTP {resposta.status_code}")
    try:
        return resposta.json()
    except ValueError as exc:
        raise FalhouDeVerdade(caminho, "resposta não é JSON") from exc


def _base_expositor(nome: str, contexto: dict) -> dict:
    return {
        "nome": nome,
        "website": "",
        "emails": [],
        "pais": "",
        "cidade": "",
        "endereco": "",
        "stand": "",
        "categorias": [],
        "descricao": "",
        "ficha_feira": "",
        "fonte_plataforma": "noomis",
        "fonte_url": contexto.get("url", ""),
        "id_plataforma": "",
    }


def coletar(slug_evento: str) -> dict:
    """Baixa expositores e patrocinadores de um evento na plataforma Noomis."""
    config = _pegar(f"appConfig?friendly_url={slug_evento}")
    id_evento = config.get("id")
    if not id_evento:
        raise FalhouDeVerdade(slug_evento, "appConfig sem id do evento")

    contexto = {"url": f"https://febrabantech.com/expositores"}
    por_nome: dict[str, dict] = {}

    for bruto in _pegar(f"getPageExpositor?event_id={id_evento}") or []:
        nome = normalizar_texto(bruto.get("exhibitor_name"))
        if not nome:
            continue
        # pavilhão e estande vêm separados aqui; no HTML vinham grudados no nome
        pavilhao = normalizar_texto(bruto.get("hall"))
        estande = normalizar_texto(bruto.get("stand"))
        registro = _base_expositor(nome, contexto)
        registro["stand"] = " ".join(p for p in (pavilhao, estande) if p)
        registro["id_plataforma"] = bruto.get("id") or ""
        por_nome[nome.lower()] = registro

    # Patrocinadores são expositores também, e trazem o site — que é justamente o que
    # falta nos demais. Quando o mesmo nome aparece nas duas listas, completamos o
    # registro em vez de criar um segundo.
    for bruto in _pegar(f"getPagePatrocinadores?event_id={id_evento}") or []:
        nome = normalizar_texto(bruto.get("heading"))
        if not nome:
            continue
        registro = por_nome.get(nome.lower()) or _base_expositor(nome, contexto)
        site = normalizar_url(normalizar_texto(bruto.get("link_redirect")))
        if site:
            registro["website"] = site
        descricao = normalizar_texto(bruto.get("description"))
        if descricao:
            registro["descricao"] = descricao[:600]
        registro["id_plataforma"] = registro["id_plataforma"] or (bruto.get("id") or "")
        por_nome[nome.lower()] = registro

    expositores = list(por_nome.values())
    return {
        "evento": {
            "plataforma": "noomis",
            "titulo": normalizar_texto(config.get("heading")),
            "id_evento": id_evento,
        },
        "expositores": expositores,
        "total_informado": len(expositores),
    }
