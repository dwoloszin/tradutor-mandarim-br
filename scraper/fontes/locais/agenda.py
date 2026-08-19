"""Agenda dos centros de exposição — a origem de tudo.

Cada local publica seu calendário de um jeito:
  - São Paulo Expo e Distrito Anhembi: WordPress + Modern Events Calendar (MEC). A lista
    só carrega inteira depois de clicar "Carregar mais" várias vezes, então usamos
    navegador headless.
  - Expo Center Norte: Next.js, mas com o HTML já renderizado no servidor — requests basta.
  - Riocentro (RJ): agenda própria, HTML servido pronto.
  - Feiras fixas (Agrishow): não têm agenda de local, são cadastradas direto.

Cada evento sai daqui já no formato do store, com datas resolvidas para ISO — é isso que
permite esconder feira encerrada e priorizar o que está perto de acontecer.
"""
from __future__ import annotations

from datetime import date

from bs4 import BeautifulSoup

from ...core.datas import encerrado, interpretar_periodo
from ...core.http import Bloqueado, FalhouDeVerdade, buscar, dominio_de
from ...core.modelos import chave_evento, normalizar_texto, novo_evento

SP_EXPO = "https://www.saopauloexpo.com.br/pt/agenda-de-eventos/"
ANHEMBI = "https://distritoanhembi.com.br/agenda/"
CENTER_NORTE = "https://www.expocenternorte.com.br/pt/eventos"
RIOCENTRO = "https://www.riocentro.com.br/agenda"

# Domínios que aparecem na página do evento mas não são o site oficial da feira.
IGNORAR_DOMINIO = {
    "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
    "tiktok.com", "linkedin.com", "whatsapp.com", "api.whatsapp.com",
    "gl-events.com", "gleventsbrasil.com.br", "gleventsbrasilcarreiras.gupy.io",
    "cookiedatabase.org", "gmpg.org", "spturis.com", "visitesaopaulo.com",
    "centernorte.com.br", "cidadecenternorte.com.br", "centernorteinc.com.br",
    "institutocenternorte.com.br", "larcenter.com.br", "expocenternorte.com.br",
    "distritoanhembi.com.br", "saopauloexpo.com.br", "riocentro.com.br",
    "google.com", "googletagmanager.com", "intranetmall.com",
    "machadocorretor.com.br", "habildesign.com", "gov.br",
}
ENCURTADORES = {"tinyurl.com", "bit.ly", "t.co", "goo.gl", "is.gd", "ow.ly", "abr.ai"}


def _montar(nome, local, cidade, uf, fonte, *, data_texto="", pavilhao="",
            descricao="", site="", categorias=None, pagina_local="", hoje=None) -> dict:
    hoje = hoje or date.today()
    inicio, fim = interpretar_periodo(data_texto, hoje)
    return novo_evento(
        id=chave_evento(nome, inicio[:4] if inicio else None),
        nome=normalizar_texto(nome),
        site=site or "",
        pagina_local=pagina_local,
        data_inicio=inicio,
        data_fim=fim,
        data_texto=normalizar_texto(data_texto),
        local_nome=local,
        cidade=cidade,
        uf=uf,
        pavilhao=normalizar_texto(pavilhao),
        categorias=categorias or [],
        descricao=normalizar_texto(descricao)[:600],
        encerrado=encerrado(fim, hoje),
        fontes=[fonte],
    )


def _expandir_curto(url: str) -> str:
    import requests
    if dominio_de(url) not in ENCURTADORES:
        return url
    try:
        from ...core.http import CABECALHOS_PADRAO
        return requests.get(url, headers=CABECALHOS_PADRAO, timeout=15,
                            allow_redirects=True).url
    except requests.RequestException:
        return url


def _site_oficial(pagina_url: str) -> str:
    """Segue a página interna do evento e tenta achar o site oficial da feira."""
    try:
        sopa = BeautifulSoup(buscar(pagina_url, ttl_horas=72), "lxml")
    except (Bloqueado, FalhouDeVerdade):
        return ""
    dominio_pagina = dominio_de(pagina_url)
    for a in sopa.select("a[href^='http']"):
        href = a.get("href", "")
        dominio = dominio_de(href)
        if not dominio or dominio == dominio_pagina:
            continue
        if any(b in dominio for b in IGNORAR_DOMINIO):
            continue
        return _expandir_curto(href)
    return ""


# ---------------------------------------------------------------- MEC (SP Expo, Anhembi)

def _parse_mec(html: str, local: str, cidade: str, uf: str, fonte: str,
               hoje: date | None = None) -> list[dict]:
    sopa = BeautifulSoup(html, "lxml")
    eventos = []
    hoje_iso = (hoje or date.today()).isoformat()
    container = sopa.select_one("#mec_skin_mec1") or sopa
    for artigo in container.select("article"):
        titulo = artigo.select_one("h3.mec-event-title a")
        if not titulo:
            continue
        nome = normalizar_texto(titulo.get_text())
        if not nome:
            continue

        link = (titulo.get("href") or "").strip()
        descricao_el = artigo.select_one(".mec-event-description")
        data_el = artigo.select_one(".mec-start-date-label")
        local_el = artigo.select_one(".mec-venue-details span")
        categorias = [normalizar_texto(a.get_text()) for a in artigo.select(".mec-categories a")]
        expirado_no_site = bool(artigo.select_one(".mec-expired-normal-label"))

        evento = _montar(
            nome, local, cidade, uf, fonte,
            data_texto=data_el.get_text() if data_el else "",
            pavilhao=local_el.get_text() if local_el else "",
            descricao=descricao_el.get_text() if descricao_el else "",
            categorias=categorias,
            pagina_local=link if dominio_de(link) == dominio_de(fonte) else "",
            site=link if dominio_de(link) != dominio_de(fonte) else "",
            hoje=hoje,
        )
        # O site marcando "expirado" é mais confiável que nossa leitura da data: a
        # agenda escreve "27 - 30 jan" sem ano, e nós chutamos o próximo janeiro.
        # Se o site diz que já passou, o ano certo é o anterior — corrigimos a data,
        # senão a feira reapareceria como futura na próxima rodada.
        if expirado_no_site:
            evento["encerrado"] = True
            evento["expirado_na_fonte"] = True
            for campo in ("data_inicio", "data_fim"):
                valor = evento.get(campo)
                if valor and valor > hoje_iso:
                    evento[campo] = f"{int(valor[:4]) - 1}{valor[4:]}"
        eventos.append(evento)
    return eventos


def coletar_sao_paulo_expo(hoje: date | None = None) -> list[dict]:
    from ...core.browser_legado import renderizar_com_carregar_mais
    html = renderizar_com_carregar_mais(SP_EXPO)
    return _parse_mec(html, "São Paulo Expo", "São Paulo", "SP", SP_EXPO, hoje)


def coletar_anhembi(hoje: date | None = None) -> list[dict]:
    from ...core.browser_legado import renderizar_com_carregar_mais
    html = renderizar_com_carregar_mais(ANHEMBI)
    eventos = _parse_mec(html, "Distrito Anhembi", "São Paulo", "SP", ANHEMBI, hoje)
    for evento in eventos:
        if not evento["site"] and evento["pagina_local"]:
            evento["site"] = _site_oficial(evento["pagina_local"])
    return eventos


# ---------------------------------------------------------------- Expo Center Norte

def coletar_expo_center_norte(hoje: date | None = None) -> list[dict]:
    try:
        sopa = BeautifulSoup(buscar(CENTER_NORTE, ttl_horas=12), "lxml")
    except (Bloqueado, FalhouDeVerdade):
        return []

    eventos = []
    mes_atual = ""
    for el in sopa.select("h2, li"):
        if el.name == "h2":
            mes_atual = normalizar_texto(el.get_text())
            continue
        link = el.select_one("a[href^='/eventos/']")
        if not link:
            continue
        nome_el = link.select_one("h3")
        if not nome_el:
            continue

        textos = [normalizar_texto(p.get_text()) for p in link.select("p")]
        descricao = textos[0] if textos else ""
        data_texto = textos[1] if len(textos) > 1 else ""
        pavilhao = textos[2] if len(textos) > 2 else ""
        # o mês do cabeçalho ajuda quando a data do card não traz o mês
        if data_texto and not any(c.isalpha() for c in data_texto):
            data_texto = f"{data_texto} {mes_atual}"

        pagina = "https://www.expocenternorte.com.br" + link.get("href", "")
        eventos.append(_montar(
            nome_el.get_text(), "Expo Center Norte", "São Paulo", "SP", CENTER_NORTE,
            data_texto=data_texto, pavilhao=pavilhao, descricao=descricao,
            pagina_local=pagina, site=_site_oficial(pagina), hoje=hoje,
        ))
    return eventos


# ---------------------------------------------------------------- Riocentro (RJ)

def coletar_riocentro(hoje: date | None = None) -> list[dict]:
    try:
        sopa = BeautifulSoup(buscar(RIOCENTRO, ttl_horas=12), "lxml")
    except (Bloqueado, FalhouDeVerdade):
        return []

    eventos = []
    vistos = set()
    for cartao in sopa.select("article, .evento, .card, li"):
        titulo_el = cartao.select_one("h2, h3, h4")
        if not titulo_el:
            continue
        nome = normalizar_texto(titulo_el.get_text())
        if not nome or len(nome) < 3 or nome in vistos:
            continue

        texto = normalizar_texto(cartao.get_text(" "))
        inicio, _ = interpretar_periodo(texto, hoje)
        if not inicio:
            continue  # sem data não é um card de evento
        vistos.add(nome)

        link = cartao.select_one("a[href]")
        href = link.get("href", "") if link else ""
        pagina = href if href.startswith("http") else f"https://www.riocentro.com.br{href}"

        eventos.append(_montar(
            nome, "Riocentro", "Rio de Janeiro", "RJ", RIOCENTRO,
            data_texto=texto, pagina_local=pagina, hoje=hoje,
        ))
    return eventos


# ---------------------------------------------------------------- feiras fixas

def coletar_feiras_fixas(hoje: date | None = None) -> list[dict]:
    """Feiras que não pertencem à agenda de um centro monitorado."""
    fixas = [
        {
            "nome": "AGRISHOW",
            "site": "https://www.agrishow.com.br/",
            "local": "Parque Zilo Bezerra de Sales",
            "cidade": "Ribeirão Preto", "uf": "SP",
            # a edição de 2026 já passou; a próxima é a de 2027. Quando o adaptador
            # Swapcard rodar, ele sobrescreve com a data oficial da plataforma.
            "data_texto": "26 a 30 de abril de 2027",
            "descricao": "Maior feira de tecnologia agrícola da América Latina.",
        },
    ]
    return [
        _montar(f["nome"], f["local"], f["cidade"], f["uf"], f["site"],
                data_texto=f["data_texto"], descricao=f["descricao"],
                site=f["site"], hoje=hoje)
        for f in fixas
    ]


LOCAIS = {
    "sao_paulo_expo": coletar_sao_paulo_expo,
    "anhembi": coletar_anhembi,
    "expo_center_norte": coletar_expo_center_norte,
    "riocentro": coletar_riocentro,
    "feiras_fixas": coletar_feiras_fixas,
}


def coletar_todos(hoje: date | None = None) -> tuple[list[dict], dict[str, str]]:
    """Roda todas as agendas. Um local quebrado nunca derruba os outros."""
    eventos: list[dict] = []
    situacao: dict[str, str] = {}
    for nome, funcao in LOCAIS.items():
        try:
            achados = funcao(hoje)
            eventos.extend(achados)
            situacao[nome] = f"ok: {len(achados)} eventos"
        except Exception as exc:  # noqa: BLE001 - resiliência é o ponto
            situacao[nome] = f"falhou: {type(exc).__name__}: {exc}"
    return eventos, situacao
