"""Adaptador WordPress REST — muitas feiras médias usam WordPress e nem sabem que
expõem uma API pronta.

Boa parte das feiras que sobraram sem adaptador são sites WordPress. Elas renderizam a
lista de expositores com algum plugin de galeria, o que faz o raspador genérico penar
(ou inventar itens). Mas o WordPress publica `/wp-json/wp/v2/` por padrão, e quando o
site cadastra os expositores como um tipo de conteúdo próprio — `expositores`,
`expositor`, `marcas` — eles saem dali limpos e paginados.

Foi o caso da Fenasan: a página rendia zero pelo raspador e a API entrega 439 registros,
com o primeiro sendo "Hunan Drillmaster Trenchless Technology Co.,Ltd.".

Limitação assumida: o WordPress devolve nome e link, e só. País, estande e site saem
vazios, a não ser que o site use ACF com campos preenchidos — o que é raro. Ainda assim
é melhor que raspar: o nome vem correto e a contagem é exata.
"""
from __future__ import annotations

import json
import re

import requests

from ...core.http import Bloqueado, FalhouDeVerdade, buscar
from ...core.modelos import normalizar_texto, normalizar_url

# Tipos de conteúdo que costumam guardar expositores.
PADRAO_TIPO = re.compile(r"expositor|exhibit|marca|empresa|participante", re.IGNORECASE)

# Tipos internos do WordPress: nunca são expositores.
TIPOS_INTERNOS = {
    "post", "page", "attachment", "nav_menu_item", "wp_block", "wp_template",
    "wp_template_part", "wp_global_styles", "wp_navigation", "wp_font_family",
    "wp_font_face", "elementor_library", "elementskit_content", "elementskit_template",
}

POR_PAGINA = 100
PAGINAS_MAX = 30

TAG_HTML = re.compile(r"<[^>]+>")


def descobrir_tipo(base: str) -> str | None:
    """Procura, entre os tipos de conteúdo do site, o que guarda os expositores."""
    try:
        tipos = json.loads(buscar(f"{base}/wp-json/wp/v2/types", ttl_horas=24, tentativas=1))
    except (Bloqueado, FalhouDeVerdade, json.JSONDecodeError):
        return None
    if not isinstance(tipos, dict):
        return None

    candidatos = [
        nome for nome in tipos
        if nome not in TIPOS_INTERNOS and PADRAO_TIPO.search(nome)
    ]
    if not candidatos:
        return None
    # o mais específico primeiro: "expositores" ganha de "marcas"
    candidatos.sort(key=lambda n: (0 if "expositor" in n.lower() else 1, len(n)))
    return candidatos[0]


def _rota(base: str, tipo: str, tipos_info: dict | None = None) -> str:
    """O caminho REST nem sempre é o nome do tipo (rest_base pode diferir)."""
    if tipos_info and isinstance(tipos_info.get(tipo), dict):
        rest = tipos_info[tipo].get("rest_base")
        if rest:
            return rest
    return tipo


def coletar(site: str, tipo: str | None = None) -> dict:
    """Baixa todos os itens do tipo de conteúdo de expositores."""
    base = site.rstrip("/")
    if base.endswith("/wp-json"):
        base = base[: -len("/wp-json")]

    tipos_info = None
    try:
        tipos_info = json.loads(buscar(f"{base}/wp-json/wp/v2/types", ttl_horas=24,
                                       tentativas=1))
    except (Bloqueado, FalhouDeVerdade, json.JSONDecodeError):
        tipos_info = None

    tipo = tipo or descobrir_tipo(base)
    if not tipo:
        raise FalhouDeVerdade(base, "nenhum tipo de conteúdo de expositor no wp-json")

    rota = _rota(base, tipo, tipos_info)
    expositores: list[dict] = []

    for pagina in range(1, PAGINAS_MAX + 1):
        url = f"{base}/wp-json/wp/v2/{rota}?per_page={POR_PAGINA}&page={pagina}"
        try:
            resposta = requests.get(
                url, timeout=30,
                headers={"User-Agent": "Mozilla/5.0 (compatible; mandarimJob/1.0)"},
            )
        except requests.RequestException as exc:
            raise FalhouDeVerdade(url, f"erro de rede: {exc}") from exc

        if resposta.status_code in (401, 403, 429, 503):
            raise Bloqueado(url, f"HTTP {resposta.status_code}")
        if resposta.status_code == 400:
            break  # passou da última página; o WP responde 400
        if resposta.status_code >= 400:
            raise FalhouDeVerdade(url, f"HTTP {resposta.status_code}")

        try:
            itens = resposta.json()
        except ValueError as exc:
            raise FalhouDeVerdade(url, "resposta não é JSON") from exc
        if not isinstance(itens, list) or not itens:
            break

        for item in itens:
            nome = normalizar_texto(
                TAG_HTML.sub("", (item.get("title") or {}).get("rendered", ""))
            )
            if not nome:
                continue
            campos = item.get("acf") if isinstance(item.get("acf"), dict) else {}
            expositores.append({
                "nome": nome,
                "website": normalizar_url(campos.get("site") or campos.get("website") or ""),
                "emails": [e for e in [campos.get("email")] if e],
                "pais": normalizar_texto(campos.get("pais") or campos.get("country") or ""),
                "cidade": normalizar_texto(campos.get("cidade") or ""),
                "endereco": "",
                "stand": normalizar_texto(campos.get("stand") or campos.get("estande") or ""),
                "categorias": [],
                "descricao": normalizar_texto(
                    TAG_HTML.sub(" ", (item.get("excerpt") or {}).get("rendered", ""))
                )[:400],
                "ficha_feira": item.get("link") or "",
                "fonte_plataforma": "wordpress",
                "fonte_url": f"{base}/wp-json/wp/v2/{rota}",
                "id_plataforma": str(item.get("id") or ""),
            })

        if len(itens) < POR_PAGINA:
            break

    if not expositores:
        raise FalhouDeVerdade(base, f"tipo '{tipo}' existe mas veio vazio")

    return {
        "evento": {"plataforma": "wordpress", "tipo_conteudo": tipo},
        "expositores": expositores,
        "total_informado": len(expositores),
    }

# ------------------------------------------------- WordPress headless (custom/v1/node)

# Alguns sites usam WordPress só como CMS e servem o conteúdo por um endpoint próprio,
# com os endpoints padrão (/wp/v2/...) bloqueados. O Beauty Fair é assim: a lista de
# expositores vive em custom/v1/node, paginada, com 391 empresas em 27 páginas.
#
# A URL do CMS aparece no HTML da página pública, então não precisamos adivinhá-la.
PADRAO_NODE = re.compile(
    r"(https?://[\w.-]+/[\w-]*/?wp-json)/custom/v1/node", re.IGNORECASE
)

PAGINAS_MAX_NODE = 60


def _achar_lista_no_bloco(dados) -> tuple[list, dict]:
    """Devolve (itens, paginacao) do primeiro bloco que parece listagem."""
    blocos = ((dados.get("fields") or {}).get("blocks")) or []
    for bloco in blocos:
        itens = bloco.get("list")
        if isinstance(itens, list) and itens:
            return itens, bloco.get("pagination") or {}
    return [], {}


def coletar_node(url_pagina: str, html: str | None = None) -> dict:
    """Coleta de um WordPress headless que expõe custom/v1/node."""
    from urllib.parse import urlparse

    if html is None:
        html = buscar(url_pagina, ttl_horas=12, timeout=30)

    achado = PADRAO_NODE.search(html)
    if not achado:
        raise FalhouDeVerdade(url_pagina, "página não expõe custom/v1/node")
    base_json = achado.group(1)

    caminho = urlparse(url_pagina).path.strip("/").split("/")[-1] or "expositores"

    expositores: list[dict] = []
    vistos: set[str] = set()
    total_paginas = 1

    for pagina in range(1, PAGINAS_MAX_NODE + 1):
        alvo = (f"{base_json}/custom/v1/node?path={caminho}&lang=pt"
                f"&category=&paged={pagina}&search=")
        try:
            dados = json.loads(buscar(alvo, ttl_horas=12, tentativas=1, timeout=25))
        except (json.JSONDecodeError, FalhouDeVerdade):
            break

        itens, paginacao = _achar_lista_no_bloco(dados)
        total_paginas = paginacao.get("total_pages", total_paginas)
        if not itens:
            break

        for item in itens:
            nome = normalizar_texto(item.get("title") or "")
            if not nome or nome.lower() in vistos:
                continue
            vistos.add(nome.lower())
            link = item.get("url") or {}
            categorias = [
                normalizar_texto(c.get("label"))
                for c in (item.get("categories") or []) if c.get("label")
            ]
            expositores.append({
                "nome": nome,
                "website": normalizar_url(link.get("url") if isinstance(link, dict) else ""),
                "emails": [], "pais": "", "cidade": "", "endereco": "", "stand": "",
                "categorias": categorias[:8], "descricao": "",
                "ficha_feira": item.get("link") or "",
                "fonte_plataforma": "wordpress_node",
                "fonte_url": url_pagina,
                "id_plataforma": str(item.get("id") or ""),
            })

        if pagina >= total_paginas:
            break

    if not expositores:
        raise FalhouDeVerdade(url_pagina, "custom/v1/node não devolveu expositores")

    return {
        "evento": {"plataforma": "wordpress_node", "url_lista": url_pagina},
        "expositores": expositores,
        "total_informado": len(expositores),
    }
