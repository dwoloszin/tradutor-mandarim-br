"""Adaptador The smarter E — Intersolar South America, Power2Drive, EES.

A Intersolar é a feira com maior densidade de fabricantes chineses do calendário
brasileiro: o setor fotovoltaico é dominado por Jinko, Trina, LONGi, Canadian, Growatt,
Deye, Sungrow e a cadeia de inversores e baterias. Ela roda junto com Power2Drive e EES
sob o guarda-chuva "The smarter E South America", no Expo Center Norte.

A lista de expositoras é carregada por JavaScript, mas o endpoint é simples: um POST
para /search/execute que devolve os cartões já em HTML. Os dois identificadores que o
payload exige (`menuPageId` e o tipo da página) estão no HTML da própria página, num
input escondido e no link de download dos favoritos — então lemos de lá em vez de
fixar valores que mudam a cada edição da feira.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...core.http import CABECALHOS_PADRAO, Bloqueado, FalhouDeVerdade, buscar
from ...core.modelos import normalizar_texto, normalizar_url

# <input type="hidden" id="menuPageId" ... value="606ebff0b3162c54d723c912" />
PADRAO_MENU_PAGE = re.compile(
    r'id="menuPageId"[^>]*value="([a-f0-9]{24})"', re.IGNORECASE
)
# /search/downloadFavourites/<tipoDaPagina>/<menuPageId>
PADRAO_TIPO = re.compile(
    r"/search/downloadFavourites/([a-f0-9]{24})/([a-f0-9]{24})", re.IGNORECASE
)

# usa navegador (csrfToken gerado por JS): so roda no PC, nunca na nuvem
REQUER_RESIDENCIAL = True

POR_PAGINA_ESTIMADO = 24
PAGINAS_MAX = 60


def _sessao(referer: str) -> requests.Session:
    """Sessão já "aquecida": o endpoint de busca recusa quem não visitou a página antes.

    O servidor emite cookies de sessão no primeiro GET e o POST sem eles volta 403 —
    o que pareceria bloqueio por IP, quando na verdade é só falta de cookie.
    """
    s = requests.Session()
    s.headers.update({
        **CABECALHOS_PADRAO,
        "Accept": "text/html, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": referer,
        "Origin": f"https://{urlparse(referer).netloc}",
    })
    try:
        s.get(referer, timeout=30)
    except requests.RequestException:
        pass  # sem cookie o POST provavelmente falha, mas deixamos ele reportar o motivo
    return s


def ler_config(url_lista: str) -> dict:
    """Extrai da página os identificadores que o endpoint de busca exige."""
    html = buscar(url_lista, ttl_horas=24)

    menu_page = PADRAO_MENU_PAGE.search(html)
    tipo = PADRAO_TIPO.search(html)
    if not menu_page and not tipo:
        raise FalhouDeVerdade(url_lista, "página não parece ser da plataforma The smarter E")

    menu_page_id = menu_page.group(1) if menu_page else tipo.group(2)
    tipo_pagina = tipo.group(1) if tipo else ""

    base = f"{urlparse(url_lista).scheme}://{urlparse(url_lista).netloc}"
    return {
        "menu_page_id": menu_page_id,
        "tipo_pagina": tipo_pagina,
        "endpoint": f"{base}/search/execute",
        "base": base,
        "url_lista": url_lista,
    }


# Códigos de estande da Intersolar: "G3.31", "W4.64", "B4.60, B6.70, B6.80".
# Sempre no começo do cabeçalho do cartão, antes do nome da empresa.
PADRAO_STAND_INICIAL = re.compile(
    r"^((?:[A-Z]{1,2}\d{1,2}\.\d{1,4})(?:\s*,\s*[A-Z]{1,2}\d{1,2}\.\d{1,4})*)\s+(.+)$"
)


def _separar_stand(cabecalho: str) -> tuple[str, str]:
    """Devolve (estande, nome) a partir do cabeçalho do cartão."""
    achado = PADRAO_STAND_INICIAL.match(cabecalho.strip())
    if not achado:
        return "", cabecalho.strip()
    return normalizar_texto(achado.group(1)), normalizar_texto(achado.group(2))


def _parse_cartoes(fragmento: str, base: str, url_lista: str) -> list[dict]:
    """Os resultados voltam como cartões HTML, um por expositora."""
    sopa = BeautifulSoup(fragmento, "lxml")
    empresas = []

    for cartao in sopa.select("a.teaser"):
        href = cartao.get("href") or ""
        # o nome fica no cabeçalho do cartão; o resto é chamada de marketing
        titulo = cartao.select_one(".teaser-header, h2, h3, .teaser-title, strong")
        nome = normalizar_texto(titulo.get_text(" ")) if titulo else ""
        if not nome:
            nome = normalizar_texto(cartao.get_text(" "))[:120]
        if not nome:
            continue

        texto = normalizar_texto(cartao.get_text(" "))

        # O cabeçalho do cartão vem como "B1.100 Contemporary Amperex Technology":
        # o código do estande grudado no nome. Sem separar, a empresa fica cadastrada
        # com o estande no nome e nunca casa com a mesma empresa em outra feira.
        stand, nome = _separar_stand(nome)

        empresas.append({
            "nome": nome,
            "website": "",
            "emails": [],
            "pais": "",
            "cidade": "",
            "endereco": "",
            "stand": stand,
            "categorias": [],
            "descricao": texto[:400],
            "ficha_feira": urljoin(base, href.split("?")[0]) if href else "",
            "fonte_plataforma": "smarter_e",
            "fonte_url": url_lista,
            "id_plataforma": cartao.get("data-content-id") or "",
        })
    return empresas


def coletar(url_lista: str, limite_paginas: int = PAGINAS_MAX) -> dict:
    """Baixa todas as expositoras da lista.

    Precisa de navegador: o endpoint exige um cabeçalho `x-csrf-token` cujo valor é
    gerado pelo JavaScript da página (`window.csrfToken`) e não aparece no HTML servido.
    Em vez de reimplementar a geração — que quebraria no próximo deploy deles —, abrimos
    a página e fazemos as chamadas de dentro dela, com o token que ela mesma produziu.
    """
    from ...core.navegador import contexto

    config = ler_config(url_lista)
    empresas: list[dict] = []
    vistos: set[str] = set()

    with contexto() as ctx:
        pagina_web = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina_web.goto(url_lista, wait_until="domcontentloaded", timeout=90000)
        pagina_web.wait_for_timeout(5000)

        token = pagina_web.evaluate("() => window.csrfToken || ''")
        if not token:
            raise FalhouDeVerdade(url_lista, "não achei o csrfToken na página")

        for pagina in range(1, limite_paginas + 1):
            corpo = {
                "page": pagina,
                "menuPageId": config["menu_page_id"],
                "term": "",
                "sortBy": "ALPHA",
                "displayType": "condensed",
            }
            if config["tipo_pagina"]:
                corpo["menuPageTypes"] = [config["tipo_pagina"]]

            resultado = pagina_web.evaluate(
                """async ([url, corpo, token]) => {
                    const r = await fetch(url, {
                      method: 'POST',
                      headers: {
                        'Content-Type': 'application/json; charset=UTF-8',
                        'X-Requested-With': 'XMLHttpRequest',
                        'X-CSRF-Token': token,
                      },
                      body: JSON.stringify(corpo),
                    });
                    return [r.status, await r.text()];
                }""",
                [config["endpoint"], corpo, token],
            )
            status, fragmento = resultado[0], resultado[1]

            if status in (401, 403, 429, 503):
                raise Bloqueado(config["endpoint"], f"HTTP {status}")
            if status >= 400:
                raise FalhouDeVerdade(config["endpoint"], f"HTTP {status}")

            novos = _parse_cartoes(fragmento, config["base"], url_lista)
            if not novos:
                break

            antes = len(vistos)
            for empresa in novos:
                chave = empresa["ficha_feira"] or empresa["nome"]
                if chave in vistos:
                    continue
                vistos.add(chave)
                empresas.append(empresa)
            if len(vistos) == antes:
                break  # a página repetiu o conteúdo anterior: fim da lista

    return {
        "evento": {"plataforma": "smarter_e", "url_lista": url_lista},
        "expositores": empresas,
        "total_informado": len(empresas),
    }
