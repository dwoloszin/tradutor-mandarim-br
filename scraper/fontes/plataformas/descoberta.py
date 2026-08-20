"""Descoberta da lista de expositores de uma feira, em camadas.

A navegação do site nem sempre linka o diretório de expositores no menu (às vezes o
link visível é "seja expositor", que é venda de estande, não a lista que queremos).
Então tentamos, em ordem:

  1. URL fixada à mão em config/feiras_prioritarias.json  (sempre vence)
  2. caminhos convencionais (/expositores, /lista-de-expositores, /exhibitors...)
  3. o link achado no menu do site
  4. varredura leve de páginas internas atrás do widget de alguma plataforma conhecida

Em cada candidata, verificamos se há uma plataforma reconhecida embutida — hoje o
Swapcard, usado pela Informa Markets e pela Agrishow.
"""
from __future__ import annotations

from urllib.parse import urljoin

from ...core.http import Bloqueado, FalhouDeVerdade, buscar
from .detectar import achar_pagina_expositores, detectar_plataforma
from .swapcard import encontrar_widget

CAMINHOS_CANDIDATOS = [
    "/expositores", "/expositores/", "/lista-de-expositores", "/lista-de-expositores/",
    "/lista-expositores", "/a-feira/lista-expositores/", "/expositores/lista",
    "/exhibitors", "/exhibitor-list", "/exhibitors/", "/participantes",
    # A RX usa inicial maiuscula e o servidor e case-sensitive: a variante minuscula
    # devolve 404 e a feira inteira se perde por causa de uma letra.
    "/pt-br/Expositores.html", "/pt-br/expositores.html",
    "/pt-br/Lista-de-Expositores.html", "/pt-br/lista-de-expositores.html",
    "/pt-br/visitar/lista-de-expositores.html", "/catalogo-de-expositores",
    "/quem-expoe", "/marcas", "/empresas-participantes",
]


# Testar caminho que não existe é o grosso do custo desta busca: são muitos candidatos
# e a maioria dá 404. Timeout curto e uma tentativa só — se o servidor está lento para
# um /expositores inexistente, insistir não ajuda.
TIMEOUT_SONDAGEM = 8


def _tentar(url: str) -> tuple[str, str] | None:
    """Baixa a URL e devolve (url, html) se ela existir de verdade."""
    try:
        return url, buscar(url, ttl_horas=24, tentativas=1, timeout=TIMEOUT_SONDAGEM)
    except (Bloqueado, FalhouDeVerdade):
        return None


def descobrir(site: str, url_fixada: str | None = None) -> dict:
    """Devolve {'pagina', 'plataforma', 'url_dados'} para a lista de expositores."""
    resultado = {"pagina": None, "plataforma": None, "url_dados": None, "erro": None}

    candidatas: list[str] = []
    if url_fixada:
        candidatas.append(url_fixada)

    # O link do menu vem antes dos palpites: quando existe, costuma ser o certo,
    # e assim evitamos sondar uma dúzia de caminhos que não existem.
    try:
        do_menu = achar_pagina_expositores(site)
        if do_menu:
            candidatas.append(do_menu)
    except Exception:
        pass

    candidatas.extend(urljoin(site, caminho) for caminho in CAMINHOS_CANDIDATOS)

    vistas = set()
    for candidata in candidatas:
        if candidata in vistas:
            continue
        vistas.add(candidata)

        obtida = _tentar(candidata)
        if obtida is None:
            continue
        url, html = obtida

        widget = encontrar_widget(html, url)
        if widget:
            resultado.update({"pagina": url, "plataforma": "swapcard", "url_dados": widget})
            return resultado

        plataforma = detectar_plataforma(html)

        # Guardar a página mesmo sem reconhecer plataforma. Antes só registrávamos
        # quando havia assinatura conhecida, e uma página de expositores perfeitamente
        # válida — mas feita em algo que não conhecemos — era jogada fora como se não
        # existisse. Era o caso do Expopostos: a descoberta achava a URL certa e o
        # roteador recebia "nenhuma página encontrada".
        if resultado["pagina"] is None:
            resultado.update({"pagina": url, "plataforma": plataforma})
        elif plataforma and not resultado["plataforma"]:
            resultado["plataforma"] = plataforma

    if resultado["pagina"] is None:
        resultado["erro"] = "nenhuma página de expositores encontrada"
    return resultado
