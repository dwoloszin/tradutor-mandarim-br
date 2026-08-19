"""Adaptador Swapcard — o de maior alcance do projeto.

A Informa Markets (Intermodal, Plástico Brasil, Hospitalar, Expomafe, Fenatran, M&T Expo,
Futurecom, FESPA...) e a Agrishow servem seus diretórios de expositores por um app
Swapcard white-label. Um adaptador só resolve todas essas feiras.

Como funciona: a página do diretório é um widget Next.js que fala com um endpoint
GraphQL do próprio domínio da feira (/api/graphql), sem autenticação, com a query
`Core_eventExhibitorListView`. Em vez de depender do hash de persisted query (que muda
a cada deploy do front deles), mandamos a query inteira — o servidor aceita, e assim o
adaptador não quebra quando eles atualizam o app.

O que vem de graça daqui, e que é exatamente o que faltava para achar as chinesas:
nome, país, cidade, site oficial, estande, categorias e redes sociais.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

import requests

from ...core.http import (
    CABECALHOS_PADRAO,
    Bloqueado,
    FalhouDeVerdade,
    buscar,
    dominio_de,
)
from ...core.modelos import normalizar_texto, normalizar_url

# Hosts já confirmados como Swapcard white-label. A detecção também é automática:
# basta a página conter um iframe/link para /widget/event/.../exhibitors/.
HOSTS_CONHECIDOS = {
    "app.informamarkets.com.br",
    "app.agrishow.com.br",
    "app.swapcard.com",
}

# O mesmo app aparece em duas formas: embutido (/widget/event/...) e como aplicativo
# completo (/event/...). Agrishow e ForMóbile usam a segunda; Intermodal, a primeira.
PADRAO_WIDGET = re.compile(
    r"https?://([\w.-]+)/(?:widget/)?event/([\w%.-]+)/exhibitors/([\w%=+-]+)",
    re.IGNORECASE,
)

CONSULTA_EXPOSITORES = """
query ListaExpositores($viewId: ID!, $eventId: ID!, $endCursor: String) {
  view: Core_eventExhibitorListView(viewId: $viewId) {
    id
    exhibitors(cursor: { first: 50, after: $endCursor }) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        id: _id
        name
        type
        websiteUrl
        email
        htmlDescription
        logoUrl
        categories
        address { street city state country zipCode place type }
        socialNetworks { type profile }
        withEvent(eventId: $eventId) { booth }
      }
    }
  }
}
"""

PAGINAS_MAX = 60  # 50 por página; trava de segurança contra loop infinito


def _sessao(referer: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        **CABECALHOS_PADRAO,
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": f"https://{dominio_de(referer)}",
        "Referer": referer,
    })
    return s


def encontrar_widget(html: str, base: str) -> str | None:
    """Acha a URL do widget Swapcard embutida na página de expositores da feira."""
    achado = PADRAO_WIDGET.search(html)
    if achado:
        return achado.group(0)
    # às vezes o iframe usa caminho relativo no domínio do app
    for m in re.finditer(r'<iframe[^>]+src="([^"]+)"', html, re.IGNORECASE):
        url = urljoin(base, m.group(1))
        if "/widget/event/" in url and "exhibitors" in url:
            return url
    return None


def dados_do_widget(url_widget: str) -> dict:
    """Extrai viewId (da URL) e eventId (do __NEXT_DATA__ da página)."""
    achado = PADRAO_WIDGET.search(url_widget)
    if not achado:
        raise FalhouDeVerdade(url_widget, "URL não é um widget Swapcard de expositores")
    host, slug_evento, view_id = achado.groups()

    html = buscar(url_widget, ttl_horas=24)
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S
    )
    if not m:
        raise FalhouDeVerdade(url_widget, "página do widget sem __NEXT_DATA__")

    dados = json.loads(m.group(1))
    props = dados.get("props", {})
    evento = props.get("event") or {}
    event_id = evento.get("id")
    if not event_id:
        raise FalhouDeVerdade(url_widget, "não achei o eventId no __NEXT_DATA__")

    return {
        "host": host,
        "slug_evento": slug_evento,
        "view_id": requests.utils.unquote(view_id),
        "event_id": event_id,
        "titulo": evento.get("title") or "",
        "inicio": evento.get("beginsAt") or "",
        "fim": evento.get("endsAt") or "",
        "email_organizador": evento.get("organizerSupportEmail") or "",
        "url_widget": url_widget,
    }


def _normalizar(node: dict, contexto: dict) -> dict:
    endereco = node.get("address") or {}
    redes = {
        (r.get("type") or "").lower(): r.get("profile") or ""
        for r in (node.get("socialNetworks") or [])
    }
    descricao_html = node.get("htmlDescription") or ""
    descricao = normalizar_texto(re.sub(r"<[^>]+>", " ", descricao_html))

    partes_endereco = [endereco.get(c) or "" for c in ("street", "city", "state", "zipCode")]
    return {
        "nome": normalizar_texto(node.get("name")),
        "website": normalizar_url(node.get("websiteUrl")),
        "emails": [node["email"]] if node.get("email") else [],
        "pais": normalizar_texto(endereco.get("country")),
        "cidade": normalizar_texto(endereco.get("city")),
        "provincia": normalizar_texto(endereco.get("state")),
        "endereco": normalizar_texto(", ".join(p for p in partes_endereco if p)),
        "stand": normalizar_texto((node.get("withEvent") or {}).get("booth")),
        "categorias": [c for c in (node.get("categories") or []) if c],
        "descricao": descricao[:1200],
        "logo": node.get("logoUrl") or "",
        "tipo_expositor": normalizar_texto(node.get("type")),
        "perfis": {k: v for k, v in redes.items() if v},
        "fonte_plataforma": "swapcard",
        "fonte_url": contexto.get("url_widget", ""),
        "id_plataforma": node.get("id") or "",
    }


def coletar(url_widget: str, limite_paginas: int = PAGINAS_MAX) -> dict:
    """Baixa todos os expositores de um evento Swapcard.

    Retorna {"evento": {...}, "expositores": [...], "total_informado": N}.
    """
    contexto = dados_do_widget(url_widget)
    endpoint = f"https://{contexto['host']}/api/graphql"
    sessao = _sessao(url_widget)

    expositores: list[dict] = []
    cursor = None
    total_informado = 0

    for _ in range(limite_paginas):
        variaveis = {
            "viewId": contexto["view_id"],
            "eventId": contexto["event_id"],
            "endCursor": cursor,
        }
        try:
            resposta = sessao.post(
                endpoint,
                json={"query": CONSULTA_EXPOSITORES, "variables": variaveis},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise FalhouDeVerdade(endpoint, f"erro de rede: {exc}") from exc

        if resposta.status_code in (401, 403, 429, 503):
            raise Bloqueado(endpoint, f"HTTP {resposta.status_code}")
        if resposta.status_code >= 400:
            raise FalhouDeVerdade(endpoint, f"HTTP {resposta.status_code}")

        try:
            dados = resposta.json()
        except ValueError as exc:
            # resposta HTML no lugar de JSON costuma ser desafio anti-bot
            if "<!DOCTYPE html>" in resposta.text[:200]:
                raise Bloqueado(endpoint, "desafio anti-bot no lugar do JSON") from exc
            raise FalhouDeVerdade(endpoint, "resposta não é JSON") from exc

        if dados.get("errors"):
            mensagens = "; ".join(e.get("message", "")[:120] for e in dados["errors"][:2])
            raise FalhouDeVerdade(endpoint, f"GraphQL: {mensagens}")

        conexao = (((dados.get("data") or {}).get("view") or {}).get("exhibitors")) or {}
        total_informado = conexao.get("totalCount") or total_informado
        for node in conexao.get("nodes") or []:
            expositores.append(_normalizar(node, contexto))

        info = conexao.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        cursor = info.get("endCursor")
        if not cursor:
            break

    return {
        "evento": contexto,
        "expositores": expositores,
        "total_informado": total_informado,
    }


def coletar_de_site(site_feira: str) -> dict | None:
    """Ponta a ponta: do site da feira até a lista de expositores, se ela for Swapcard."""
    from .detectar import achar_pagina_expositores

    pagina = achar_pagina_expositores(site_feira)
    if not pagina:
        return None
    try:
        html = buscar(pagina, ttl_horas=24)
    except (Bloqueado, FalhouDeVerdade):
        return None

    widget = encontrar_widget(html, pagina)
    if not widget:
        return None
    return coletar(widget)
