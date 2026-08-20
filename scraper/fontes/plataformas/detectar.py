"""Descobre, para um site de feira, onde está a lista de expositores e que plataforma a serve.

Ferramenta de diagnóstico, usada por `python -m scraper.cli investigar`. Toda vez que
uma feira nova entra na agenda, é isto que diz se ela cai num adaptador existente ou
se precisa de um novo.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from ...core.http import Bloqueado, FalhouDeVerdade, buscar, buscar_sopa, dominio_de

# Assinaturas de plataformas de diretório de expositores. A chave é o que aparece no
# HTML/JS da página; o valor é o adaptador que sabe conversar com ela.
ASSINATURAS = {
    "api.reedexpo.com": "rx",
    "reedexpo": "rx",
    "rxweb": "rx",
    "mapyourshow.com": "mapyourshow",
    "eventscloud.com": "cvent",
    "cvent.com": "cvent",
    "swapcard.com": "swapcard",
    "grip.events": "grip",
    "expocad.com": "expocad",
    "expofp.com": "expofp",
    "n-expo": "nexpo",
    "informamarkets": "informa",
    "ungerboeck": "ungerboeck",
    "eventtia": "eventtia",
    "sympla.com.br": "sympla",
    "algolia": "algolia",
    "elasticsearch": "elastic",
    "/wp-json/": "wordpress",
    "__NEXT_DATA__": "nextjs",
    "wixstatic.com": "wix",
    "webflow": "webflow",
    "framer.com": "framer",
    "data-framer-": "framer",
}

PADRAO_LINK_EXPOSITOR = re.compile(
    r"expositor|exhibitor|exhibition-list|lista-de-empresas|catalogo|catalog|"
    r"participating-companies|marcas",
    re.IGNORECASE,
)
PADRAO_LINK_NEGATIVO = re.compile(
    r"seja-expositor|quero-expor|area-do-expositor|por-que-|torne-se-|"
    r"become-an-exhibitor|why-exhibit|book-a-stand|reserve|manual-do-expositor|"
    r"exhibitor-manual|credenciamento|vantagens|"
    # Páginas de VENDA de estande. Elas falam de "expositor" o tempo todo e passam
    # pelo filtro positivo, mas não listam empresa nenhuma: raspar a "traga sua marca"
    # da FEBRAVA produziu 248 itens que não eram expositores.
    r"traga-sua-marca|traga-sua-empresa|expor-na-|quero-expo|torne-se|seja-um-expositor|"
    r"ja-sou-expositor|portal-do-expositor|exhibitor-hub|produtos-digitais|"
    r"midia-kit|midiakit|patrocin",
    re.IGNORECASE,
)
PADRAO_LINK_FORTE = re.compile(
    r"lista-de-expositores|exhibitor-list|exhibitor-directory|expositores$|"
    r"exhibitors$|diretorio|buscador|search|catalogo-de-expositores",
    re.IGNORECASE,
)

# Endpoints que costumam devolver a lista pronta em JSON.
PADRAO_API = re.compile(
    r"https?://[^\s\"'<>]{6,180}?(api|graphql|search|exhibitor|expositor|"
    r"wp-json)[^\s\"'<>]{0,120}",
    re.IGNORECASE,
)


def detectar_plataforma(html: str) -> str | None:
    minusculo = html.lower()
    for assinatura, plataforma in ASSINATURAS.items():
        if assinatura.lower() in minusculo:
            return plataforma
    return None


# "confirmados" e o sinal mais forte de que o link e a lista de verdade, e nao a
# pagina de venda de estande.
PADRAO_CONFIRMADOS = re.compile(
    r"confirmad|lista-de-expositor|lista-expositor|catalogo|diretorio|"
    r"quem-expoe|exhibitor-list",
    re.IGNORECASE,
)


def _normalizar_agulha(href: str, texto: str) -> str:
    """Junta URL e texto num formato unico para os padroes casarem.

    Sem isto, "Torne-se um expositor" (com espacos) escapava do filtro que procurava
    "torne-se-" (com hifen), e a pagina de VENDA de estande era escolhida como se
    fosse a lista. Foi exatamente o que aconteceu na FEIPLAR: 15 itens raspados da
    home, quando a lista real tinha centenas.
    """
    return re.sub(r"[^a-z0-9]+", "-", f"{href} {texto}".lower())


def achar_pagina_expositores(site: str, html: str | None = None) -> str | None:
    """Acha o link do diretorio de expositores, escolhendo por pontuacao.

    Nao serve pegar o primeiro que casa: o mesmo site costuma ter "/expositores/"
    (torne-se um) e "/expositores-confirmados-2026/" (a lista), e uma busca ingenua
    acha os dois igualmente bons.
    """
    try:
        sopa = buscar_sopa(site) if html is None else None
    except (Bloqueado, FalhouDeVerdade):
        return None
    if sopa is None:
        from bs4 import BeautifulSoup
        sopa = BeautifulSoup(html, "lxml")

    candidatos: list[tuple[int, int, str]] = []
    for a in sopa.select("a[href]"):
        href = a.get("href", "")
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        agulha = _normalizar_agulha(href, a.get_text(" ", strip=True))

        if PADRAO_LINK_NEGATIVO.search(agulha):
            continue
        if not PADRAO_LINK_EXPOSITOR.search(agulha):
            continue

        pontos = 2 if PADRAO_LINK_FORTE.search(agulha) else 1
        if PADRAO_CONFIRMADOS.search(agulha):
            pontos = 5

        # O ano no link muda a cada edição ("expositores-confirmados-2026" vira 2027).
        # Nunca fixamos a URL no config por isso: o ano entra como desempate, não como
        # regra. Na virada, quando as duas páginas coexistem, a mais nova ganha; e se
        # a feira parar de publicar a lista, a descoberta cai nos outros candidatos.
        ano = re.search(r"(20\d{2})", agulha)
        ano_valor = int(ano.group(1)) if ano else 0
        if ano_valor:
            pontos += 1
        candidatos.append((pontos, ano_valor, urljoin(site, href)))

    if not candidatos:
        return None
    candidatos.sort(key=lambda c: (-c[0], -c[1]))
    return candidatos[0][2]


def apis_candidatas(html: str, base: str) -> list[str]:
    """URLs no HTML/JS que cheiram a endpoint de dados de expositores."""
    achados = []
    for m in PADRAO_API.finditer(html):
        url = m.group(0).rstrip("\\\"',);")
        if any(x in url for x in ("googleapis", "gstatic", "facebook", "google-analytics",
                                  "googletagmanager", "hotjar", "cookiebot", "jquery",
                                  "bootstrap", "fontawesome", "recaptcha")):
            continue
        if url not in achados:
            achados.append(url)
    return achados[:15]


def investigar(site: str) -> dict:
    """Diagnóstico completo de uma feira: plataforma, página de expositores, APIs."""
    relatorio = {
        "site": site,
        "dominio": dominio_de(site),
        "acessivel": False,
        "bloqueado": False,
        "plataforma": None,
        "pagina_expositores": None,
        "plataforma_pagina": None,
        "apis": [],
        "expositores_no_html": 0,
        "erro": None,
    }

    try:
        html = buscar(site, ttl_horas=24)
        relatorio["acessivel"] = True
    except Bloqueado as exc:
        relatorio["bloqueado"] = True
        relatorio["erro"] = exc.motivo
        return relatorio
    except FalhouDeVerdade as exc:
        relatorio["erro"] = exc.motivo
        return relatorio

    relatorio["plataforma"] = detectar_plataforma(html)
    pagina = achar_pagina_expositores(site, html)
    relatorio["pagina_expositores"] = pagina

    if pagina:
        try:
            html_lista = buscar(pagina, ttl_horas=24)
            relatorio["plataforma_pagina"] = detectar_plataforma(html_lista)
            relatorio["apis"] = apis_candidatas(html_lista, pagina)
            # quantos links parecem ficha de empresa já no HTML servido
            relatorio["expositores_no_html"] = len(
                re.findall(r'href="[^"]*(?:expositor|exhibitor)[^"]*/[^"/]{3,}"', html_lista, re.I)
            )
        except Bloqueado as exc:
            relatorio["bloqueado"] = True
            relatorio["erro"] = f"lista bloqueada: {exc.motivo}"
        except FalhouDeVerdade as exc:
            relatorio["erro"] = f"lista inacessível: {exc.motivo}"

    return relatorio
