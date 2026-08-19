"""Vagas de intérprete/tradutor de mandarim em portais de emprego brasileiros.

Só portais cujo robots.txt permite. O LinkedIn fica de fora de propósito: responde
`Disallow: /` para robôs genéricos e tem histórico de ação judicial contra scraping.
Para ele o site oferece link de busca pronto — quem clica é o intérprete, não o robô.

O que sai daqui alimenta a mesma aba do alerta do consulado: são as duas formas de
trabalho que aparecem sem prospecção nenhuma, e as duas têm prazo curto.

Nota sobre a natureza dessas vagas: quase todas são CLT ou PJ de longo prazo, um
perfil diferente do freelance de feira que é o foco do projeto. Entram como
complemento — e porque uma empresa que contrata intérprete fixo é, ela própria,
um cliente possível para trabalho pontual.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from ...core.http import Bloqueado, FalhouDeVerdade, buscar
from ...core.modelos import normalizar_texto, slug
from ...core.store import agora_iso

TERMOS = ["tradutor mandarim", "intérprete mandarim", "tradutor chinês", "mandarim"]

# Portais verificados um a um contra o robots.txt (ver scraper/core/robots.py,
# que também bloqueia em tempo de execução se algo mudar do lado deles).
PORTAIS = [
    {
        "nome": "InfoJobs",
        "url": "https://www.infojobs.com.br/empregos.aspx?palabra={termo}",
        "item": "div.js-vacancyLoad, div.vacancy-card",
        "titulo": "h2, .h3, a.text-decoration-none",
        "empresa": ".text-muted, .vacancy-company",
    },
    {
        "nome": "Vagas.com",
        "url": "https://www.vagas.com.br/vagas-de-{slug}",
        "item": "li.vaga, article.vaga",
        "titulo": "h2.cargo a, h2 a",
        "empresa": ".emprVaga, .empresa",
    },
    {
        "nome": "Empregos.com.br",
        "url": "https://www.empregos.com.br/vagas/{slug}",
        "item": "article, li.vaga, div.job",
        "titulo": "h2 a, h3 a, a.job-title",
        "empresa": ".company, .empresa",
    },
]

# Buscas que o intérprete abre com um clique. Nenhuma é raspada.
LINKS_PRONTOS = {
    "LinkedIn": "https://www.linkedin.com/jobs/search/?keywords={termo}&location=Brasil",
    "Indeed": "https://br.indeed.com/jobs?q={termo}&l=Brasil",
    "Glassdoor": "https://www.glassdoor.com.br/Vaga/index.htm?keyword={termo}",
    "Gupy": "https://portal.gupy.io/job-search/term={termo}",
}

# Uma vaga só interessa se for de mandarim: "tradutor" sozinho traz inglês e espanhol.
RELEVANTE = re.compile(r"mandarim|chin[êe]s|chinesa|中文|汉语|普通话", re.IGNORECASE)


def _extrair(html: str, portal: dict, base: str) -> list[dict]:
    sopa = BeautifulSoup(html, "lxml")
    achados = []

    for cartao in sopa.select(portal["item"])[:40]:
        titulo_el = cartao.select_one(portal["titulo"])
        if not titulo_el:
            continue
        titulo = normalizar_texto(titulo_el.get_text(" "))
        if not titulo or len(titulo) < 5:
            continue

        texto = normalizar_texto(cartao.get_text(" "))
        if not RELEVANTE.search(texto):
            continue  # é vaga de tradutor de outro idioma

        empresa_el = cartao.select_one(portal["empresa"]) if portal.get("empresa") else None
        link_el = titulo_el if titulo_el.name == "a" else cartao.select_one("a[href]")
        href = link_el.get("href") if link_el else ""

        achados.append({
            "titulo": titulo[:160],
            "empresa": normalizar_texto(empresa_el.get_text(" "))[:80] if empresa_el else "",
            "url": urljoin(base, href) if href else base,
            "resumo": texto[:280],
        })
    return achados


def coletar() -> list[dict]:
    """Varre os portais permitidos e devolve as vagas de mandarim encontradas."""
    itens: list[dict] = []
    vistos: set[str] = set()

    for portal in PORTAIS:
        for termo in TERMOS:
            url = portal["url"].format(termo=quote_plus(termo), slug=slug(termo))
            try:
                html = buscar(url, ttl_horas=12, tentativas=1, timeout=20)
            except (Bloqueado, FalhouDeVerdade):
                continue  # portal fora do ar ou bloqueando: não é motivo para parar

            for vaga in _extrair(html, portal, url):
                chave = f"{vaga['titulo'].lower()}|{vaga['empresa'].lower()}"
                if chave in vistos:
                    continue
                vistos.add(chave)
                itens.append({
                    "id": f"vaga:{slug(portal['nome'])}:{slug(vaga['titulo'], 40)}",
                    "fonte": portal["nome"],
                    "canal": url,
                    "titulo": vaga["titulo"],
                    "url": vaga["url"],
                    "data_publicacao": "",
                    "tipo": "vaga_portal",
                    "termos_encontrados": [termo],
                    "resumo": (f"{vaga['empresa']} — " if vaga["empresa"] else "") + vaga["resumo"],
                    "visto_em": agora_iso(),
                })
    return itens


def links_de_busca() -> dict[str, str]:
    """Buscas prontas para os portais que não podem ser raspados."""
    termo = quote_plus("intérprete mandarim")
    return {nome: url.format(termo=termo) for nome, url in LINKS_PRONTOS.items()}
