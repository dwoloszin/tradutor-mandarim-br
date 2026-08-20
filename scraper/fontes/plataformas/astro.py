"""Adaptador para sites feitos em Astro, que embutem os dados na própria página.

O Expopostos é o caso: a lista mostra 50 cartões e um botão "Ver mais" que revela os
outros 147. Clicando no botão, nenhuma requisição nova acontece — porque os 197
expositores já vieram no HTML, dentro do atributo `props` de um `<astro-island>`.

Isso importa por dois motivos. Primeiro, não precisamos de navegador para pegar todos:
o botão é só apresentação. Segundo — e esse é o ponto — um raspador que lesse os
cartões visíveis pararia em 50 e não haveria nada indicando que faltavam 147.

A serialização do Astro embrulha cada valor num par `[tipo, valor]`, então
desembrulhamos antes de ler.
"""
from __future__ import annotations

import html as html_lib
import json
import re

from ...core.http import FalhouDeVerdade, buscar
from ...core.modelos import normalizar_texto, normalizar_url

PADRAO_ISLAND = re.compile(r'<astro-island[^>]*\sprops="([^"]{100,})"', re.IGNORECASE)

# nomes de campo que costumam guardar a lista de empresas
CHAVES_LISTA = ("expositores", "marcas", "empresas", "exhibitors", "brands", "items")

MIN_ITENS = 10


def _desembrulhar(valor):
    """Astro serializa como [tipo, conteudo]. Devolve só o conteúdo, recursivamente."""
    if isinstance(valor, list) and len(valor) == 2 and isinstance(valor[0], int):
        return _desembrulhar(valor[1])
    if isinstance(valor, list):
        return [_desembrulhar(v) for v in valor]
    if isinstance(valor, dict):
        return {k: _desembrulhar(v) for k, v in valor.items()}
    return valor


def _listas_de_empresas(dados) -> list[list[dict]]:
    """Procura, na estrutura, listas de dicionários que pareçam empresas."""
    achadas = []

    def visitar(no):
        if isinstance(no, dict):
            for chave, valor in no.items():
                if (chave.lower() in CHAVES_LISTA and isinstance(valor, list)
                        and valor and isinstance(valor[0], dict)):
                    achadas.append(valor)
                visitar(valor)
        elif isinstance(no, list):
            if no and isinstance(no[0], dict) and any(
                k in no[0] for k in ("nome", "name", "title", "titulo")
            ):
                achadas.append(no)
            for item in no:
                visitar(item)

    visitar(dados)
    return achadas


def coletar(url: str, html: str | None = None) -> dict:
    """Lê os expositores embutidos na página Astro."""
    if html is None:
        html = buscar(url, ttl_horas=12, timeout=30)

    melhor: list[dict] = []
    for bruto in PADRAO_ISLAND.findall(html):
        try:
            dados = _desembrulhar(json.loads(html_lib.unescape(bruto)))
        except (json.JSONDecodeError, ValueError):
            continue
        for lista in _listas_de_empresas(dados):
            if len(lista) > len(melhor):
                melhor = lista

    # O island é só a primeira página. Se houver API, ela tem a lista inteira —
    # e a diferença não é pequena: 50 contra 197 no Expopostos. Um raspador que
    # lesse só o que está visível pararia em 50 sem nada indicar que faltava o resto.
    api = _descobrir_api(html, url)
    if api:
        completos = _paginar_api(*api)
        if len(completos) > len(melhor):
            melhor = completos

    if len(melhor) < MIN_ITENS:
        raise FalhouDeVerdade(url, f"astro-island com só {len(melhor)} itens")

    expositores = []
    vistos = set()
    for item in melhor:
        nome = normalizar_texto(
            item.get("nome") or item.get("name") or item.get("title") or ""
        )
        # o site repete o nome duas vezes separado por hífen em alguns registros:
        # "ACS Automação Comercial - ACS Automação Comercial"
        partes = [p.strip() for p in nome.split(" - ")]
        if len(partes) == 2 and partes[0].lower() == partes[1].lower():
            nome = partes[0]
        if not nome or nome.lower() in vistos:
            continue
        vistos.add(nome.lower())

        expositores.append({
            "nome": nome,
            "website": normalizar_url(item.get("link") or item.get("site") or ""),
            "emails": [],
            "pais": normalizar_texto(item.get("pais") or item.get("country") or ""),
            "cidade": "",
            "endereco": "",
            "stand": normalizar_texto(item.get("stand") or item.get("estande") or ""),
            "categorias": [],
            "descricao": normalizar_texto(item.get("descricao") or "")[:300],
            "ficha_feira": "",
            "fonte_plataforma": "astro",
            "fonte_url": url,
            "id_plataforma": str(item.get("id") or ""),
        })

    return {
        "evento": {"plataforma": "astro", "url_lista": url},
        "expositores": expositores,
        "total_informado": len(expositores),
    }

# ---------------------------------------------------------------- API paginada

# O island traz só a primeira página. O resto vem de uma API que o próprio bundle
# JavaScript revela — no Expopostos, "/api/marca_confirmada/?page_size=50".
# O formato de resposta (count/next/results) é o padrão do Django REST Framework,
# comum em site brasileiro, então vale como capacidade geral e não como caso isolado.
# O Astro referencia os bundles por component-url, não só por src — procurar
# apenas src fazia a descoberta da API falhar em silêncio.
PADRAO_BUNDLE = re.compile(r"(/_astro/[\w.-]+\.js)")
PADRAO_BASE_API = re.compile(r"[\"']((?:https?://)[\w.-]+/)[\"']")
PADRAO_CAMINHO_API = re.compile(r"[\"'](/api/[\w/-]+/?\?[\w=&-]*)[\"']")

PAGINAS_MAX_API = 40


def _descobrir_api(html: str, url_base: str) -> tuple[str, str] | None:
    """Procura, nos bundles da página, a base e o caminho da API de expositores."""
    from urllib.parse import urljoin

    base_api = ""
    caminhos: list[str] = []

    vistos: set[str] = set()
    fila = list(dict.fromkeys(PADRAO_BUNDLE.findall(html)))[:12]

    while fila and len(vistos) < 20:
        bundle = fila.pop(0)
        if bundle in vistos:
            continue
        vistos.add(bundle)
        try:
            js = buscar(urljoin(url_base, bundle), ttl_horas=24, tentativas=1, timeout=20)
        except Exception:  # noqa: BLE001 - bundle ausente não impede os outros
            continue

        if not base_api:
            achado = PADRAO_BASE_API.search(js)
            if achado and "admin" in achado.group(1):
                base_api = achado.group(1)

        for caminho in PADRAO_CAMINHO_API.findall(js):
            if re.search(r"marca|expositor|brand|exhibitor", caminho, re.IGNORECASE):
                caminhos.append(caminho)

        # a base da API costuma estar num módulo auxiliar importado pelo componente
        # (o "fetch.js"), então seguimos os imports relativos um nível
        if caminhos and not base_api:
            for importado in re.findall(r'from"\./([\w.-]+\.js)"', js):
                fila.append("/_astro/" + importado)

    if not caminhos:
        return None
    if not base_api:
        base_api = url_base
    return base_api.rstrip("/"), caminhos[0]


def _paginar_api(base: str, caminho: str) -> list[dict]:
    """Percorre uma API no formato do Django REST (count / next / results)."""
    from urllib.parse import urljoin

    proxima = urljoin(base + "/", caminho.lstrip("/"))
    itens: list[dict] = []

    for _ in range(PAGINAS_MAX_API):
        try:
            dados = json.loads(buscar(proxima, ttl_horas=12, tentativas=1, timeout=25))
        except (json.JSONDecodeError, FalhouDeVerdade):
            break
        if not isinstance(dados, dict) or "results" not in dados:
            break
        itens.extend(dados.get("results") or [])
        proxima = dados.get("next")
        if not proxima:
            break
    return itens
