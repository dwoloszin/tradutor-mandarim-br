"""Enriquecimento por diretórios B2B chineses (Made-in-China), com casamento estrito de nome.

Este módulo existe para as empresas que a feira lista **sem site nenhum** — hoje 145 das
163 chinesas. Sem site não há o que visitar, então a única saída é procurar a empresa
num diretório onde ela se cadastrou.

A regra inegociável aqui é **exatidão**. Uma busca por
"Ningbo Sino Pacific International Logistics" devolve "Yiwu Lingsheng", "Shenzhen Flying"
e outras dezenas de transportadoras parecidas. Se aceitássemos o primeiro resultado, o
intérprete ligaria para a empresa errada achando que é a expositora — o que é pior do que
não ter contato nenhum, porque parece informação boa.

Por isso: extraímos o nome de cada candidato e só aceitamos quando ele é **o mesmo nome**,
depois de normalizar maiúsculas, pontuação e sufixos societários (Co., Ltd = 有限公司).
Não há "parecido o suficiente" — ou é a empresa, ou a tarefa termina como sem_dados e o
intérprete usa os links de pesquisa prontos no site.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus, urlparse

from bs4 import BeautifulSoup

from ...core.http import Bloqueado, FalhouDeVerdade, buscar
from ...core.modelos import nome_canonico, normalizar_texto

# Buscar num diretório chinês de IP de datacenter costuma cair em desafio; e o volume de
# requisições pede o IP residencial. Marcado como residencial: roda no PC, não na nuvem.
REQUER_RESIDENCIAL = True

# searchType=supplier troca a busca de PRODUTOS pela de FORNECEDORES. Sem isso, o
# ranqueamento é por relevância de produto e a empresa exata quase nunca aparece.
BUSCA_MIC = (
    "https://www.made-in-china.com/productdirectory.do"
    "?word={termo}&subaction=hunt&style=b&mode=and&code=0&comProvince=nolimit"
    "&order=0&searchType=supplier"
)

# subdomínio de empresa: https://<slug>.en.made-in-china.com
DOMINIO_EMPRESA = re.compile(r"^https?://([\w-]+)\.en\.made-in-china\.com/?$", re.I)

# palavras que não distinguem empresa nenhuma e não devem pesar na comparação
TOKENS_VAZIOS = {
    "CO", "LTD", "LIMITED", "INC", "CORP", "GROUP", "COMPANY", "INTERNATIONAL",
    "IMPORT", "EXPORT", "IMP", "EXP", "TRADING", "TRADE", "TECHNOLOGY", "TECH",
    "INDUSTRY", "INDUSTRIAL", "MANUFACTURING", "MANUFACTURE", "FACTORY", "THE", "AND",
}


def _tokens(nome: str) -> set[str]:
    return {t for t in nome_canonico(nome).split() if t and t not in TOKENS_VAZIOS}


def mesmo_nome(procurado: str, candidato: str) -> bool:
    """É a mesma empresa? Só True quando não há dúvida.

    Aceita apenas igualdade após normalização, ou conjuntos de palavras distintivas
    idênticos (cobre "ABC Co., Ltd" vs "ABC Ltd." e ordem trocada). Qualquer palavra
    distintiva a mais ou a menos reprova — é o que separa
    "Shenzhen Flying Freight" de "Shenzhen Flying Supply Chain".
    """
    a, b = nome_canonico(procurado), nome_canonico(candidato)
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = _tokens(procurado), _tokens(candidato)
    if not ta or not tb:
        return False
    return ta == tb


def _candidatos_mic(html: str) -> list[tuple[str, str]]:
    """Extrai (nome, url_da_empresa) dos resultados da busca."""
    sopa = BeautifulSoup(html, "lxml")
    achados: dict[str, str] = {}

    for a in sopa.select("a[href]"):
        href = (a.get("href") or "").split("?")[0].rstrip("/")
        if href.startswith("//"):
            href = "https:" + href
        if not DOMINIO_EMPRESA.match(href + "/"):
            continue
        nome = normalizar_texto(a.get_text(" "))
        # âncoras de logo/nota vêm sem nome ou com "4.7/5"
        if not nome or len(nome) < 4 or re.fullmatch(r"[\d.,/\s]+", nome):
            continue
        achados.setdefault(href, nome)

    return [(nome, url) for url, nome in achados.items()]


def _extrair_perfil(html: str, url: str) -> dict:
    sopa = BeautifulSoup(html, "lxml")
    for tag in sopa(["script", "style", "noscript"]):
        tag.decompose()
    linhas = [l.strip() for l in sopa.get_text("\n", strip=True).split("\n") if l.strip()]

    def valor_de(*rotulos: str) -> str:
        for indice, linha in enumerate(linhas):
            limpa = linha.rstrip(":").strip().lower()
            if any(limpa == r.lower() for r in rotulos):
                if indice + 1 < len(linhas):
                    return normalizar_texto(linhas[indice + 1])[:120]
        return ""

    produtos = valor_de("Main Products")
    return {
        "tipo_negocio": valor_de("Business Type"),
        "ano_fundacao": valor_de("Year of Establishment")[:10],
        "funcionarios": valor_de("Number of Employees", "Employees", "Staff"),
        "provincia": valor_de("Province", "Location", "Management System Certification"),
        "produtos": [p.strip() for p in produtos.split(",") if p.strip()][:10],
        "perfil_mic": url,
    }


def enriquecer_por_nome(nome_empresa: str) -> dict:
    """Procura a empresa no Made-in-China e devolve os dados só se o nome bater.

    Retorna {} quando não há correspondência exata — de propósito.
    """
    if not nome_empresa or len(nome_empresa) < 5:
        raise FalhouDeVerdade("", "nome curto demais para buscar com segurança")

    url_busca = BUSCA_MIC.format(termo=quote_plus(nome_empresa))
    html = buscar(url_busca, ttl_horas=24 * 14, tentativas=1, timeout=25)

    candidatos = _candidatos_mic(html)
    if not candidatos:
        return {"encontrado": False, "motivo": "busca sem resultados de empresa",
                "candidatos_vistos": 0}

    for nome_candidato, url in candidatos:
        if not mesmo_nome(nome_empresa, nome_candidato):
            continue
        try:
            perfil_html = buscar(f"{url}/company-profile.html", ttl_horas=24 * 30,
                                 tentativas=1, timeout=25)
        except (Bloqueado, FalhouDeVerdade):
            perfil_html = ""

        dados = _extrair_perfil(perfil_html, url) if perfil_html else {"perfil_mic": url}
        dados.update({
            "encontrado": True,
            "nome_confirmado": nome_candidato,
            "website": url,   # o subdomínio é o site da empresa dentro do diretório
            "fonte": "made_in_china",
        })
        return dados

    return {
        "encontrado": False,
        "motivo": "nenhum candidato com o mesmo nome (só empresas parecidas)",
        "candidatos_vistos": len(candidatos),
        "exemplos_descartados": [n for n, _ in candidatos[:3]],
    }
