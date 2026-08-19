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

CHR_NL = chr(10)

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

    # a lista dá nome e estande; a ficha dá contato e país — e é o país que decide
    # se a empresa entra na lista do intérprete
    enriquecer_com_fichas(empresas)

    return {
        "evento": {"plataforma": "smarter_e", "url_lista": url_lista},
        "expositores": empresas,
        "total_informado": len(empresas),
    }

# ---------------------------------------------------------------- fichas

# A ficha de cada expositora traz um bloco "Informações de contato" com telefone,
# e-mail, site e endereço — inclusive o país, que é justamente o campo que decide a
# detecção de empresa chinesa. A lista sozinha não tem nada disso.
ROTULO_CONTATO = re.compile(r"informa[çc][õo]es de contato|contact information", re.I)
EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,12}")
TELEFONE = re.compile(r"(?:\+|00)\s?\d{1,3}[\s\-.()]*\d[\d\s\-.()]{6,16}\d")
SITE = re.compile(r"https?://[^\s<>\"']{4,120}")

# "201112 Shanghai, China" / "Shanghai, China"
LINHA_PAIS = re.compile(r"^(?:\d{4,8}\s+)?(.+?),\s*([A-Za-zÀ-ÿ .'-]{3,40})$")


def _texto_da_ficha(url: str) -> str:
    from bs4 import BeautifulSoup
    html = buscar(url, ttl_horas=24 * 14, tentativas=1, timeout=25)
    sopa = BeautifulSoup(html, "lxml")
    for tag in sopa(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    return sopa.get_text(CHR_NL, strip=True)


def _extrair_contato(texto: str) -> dict:
    linhas = [l.strip() for l in texto.split(CHR_NL) if l.strip()]

    inicio = next((i for i, l in enumerate(linhas) if ROTULO_CONTATO.search(l)), None)
    bloco = linhas[inicio + 1: inicio + 12] if inicio is not None else linhas[:0]
    junto = CHR_NL.join(bloco)

    emails = [e.lower() for e in EMAIL.findall(junto)]
    telefones = [re.sub(r"[^\d+]", "", t) for t in TELEFONE.findall(junto)]
    sites = [s for s in SITE.findall(junto) if "intersolar" not in s]

    # o país é a última parte da última linha com vírgula
    pais = cidade = ""
    for linha in reversed(bloco):
        achado = LINHA_PAIS.match(linha)
        if achado and "@" not in linha and not linha.startswith(("+", "http")):
            cidade, pais = achado.group(1).strip(), achado.group(2).strip()
            break

    # o endereço termina na linha do país; depois dela vem navegação da página
    fim = len(bloco)
    for indice, linha in enumerate(bloco):
        if pais and linha.rstrip().endswith(pais):
            fim = indice + 1
            break
    endereco = " ".join(
        l for l in bloco[:fim]
        if not EMAIL.search(l) and not TELEFONE.search(l) and not l.startswith("http")
    )[:200]

    return {
        "emails": emails[:3],
        "telefones": telefones[:3],
        "website": normalizar_url(sites[0]) if sites else "",
        "pais": pais,
        "cidade": cidade,
        "endereco": endereco,
    }


def enriquecer_com_fichas(expositores: list[dict], paralelas: int = 6,
                          limite: int | None = None) -> int:
    """Visita a ficha de cada expositora e completa contato, país e endereço.

    Vale a visita extra: é uma requisição por empresa, e traz e-mail direto e o país —
    dado que a lista não tem e que muda a classificação de metade delas.
    """
    from concurrent.futures import ThreadPoolExecutor

    alvos = [e for e in expositores if e.get("ficha_feira")]
    if limite:
        alvos = alvos[:limite]

    def visitar(exp):
        try:
            return exp, _extrair_contato(_texto_da_ficha(exp["ficha_feira"]))
        except Exception:  # noqa: BLE001 - ficha ruim não derruba a coleta
            return exp, None

    completados = 0
    with ThreadPoolExecutor(max_workers=max(1, paralelas)) as executor:
        for exp, dados in executor.map(visitar, alvos):
            if not dados:
                continue
            for campo in ("pais", "cidade", "endereco", "website"):
                if dados.get(campo) and not exp.get(campo):
                    exp[campo] = dados[campo]
            if dados.get("emails"):
                exp["emails"] = list(dict.fromkeys(exp.get("emails", []) + dados["emails"]))
            if dados.get("telefones"):
                exp["telefones"] = dados["telefones"]
            completados += 1
    return completados
