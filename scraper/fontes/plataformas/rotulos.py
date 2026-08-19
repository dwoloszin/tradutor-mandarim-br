"""Parser de listas em formato "Empresa: X / Estande: Y / Visite o site".

Padrão comum em site de feira brasileiro feito com Elementor: a lista não é uma tabela
nem uma coleção de links, é um monte de caixas de texto com rótulos. Nenhuma das outras
estratégias pega isso — o raspador por link não acha prefixo comum, o por cartão pega a
caixa errada, e a API não existe.

A FEIPLAR é o caso: 15 itens vinham da home enquanto a lista real, com centenas de
expositores (vários chineses), ficava intacta nessa estrutura.

O ganho aqui é que os rótulos identificam o campo com certeza. Onde o raspador genérico
adivinha o que é nome e o que é estande, aqui o próprio site diz.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ...core.http import FalhouDeVerdade, buscar
from ...core.modelos import normalizar_texto, normalizar_url

ROTULO_EMPRESA = re.compile(r"^\s*(empresa|expositor|exhibitor|company)\s*:?\s*$", re.I)
ROTULO_ESTANDE = re.compile(r"^\s*(estande|stand|booth|rua|pavilh[ãa]o)\s*:?\s*$", re.I)

# "Empresa: ACME" no mesmo bloco de texto
ROTULO_INLINE = re.compile(
    r"(?:empresa|expositor|exhibitor|company)\s*:\s*(.{2,90}?)\s*"
    r"(?:estande|stand|booth)\s*:\s*([\w./\- ]{1,20})",
    re.IGNORECASE | re.DOTALL,
)

MIN_ITENS = 5

LIXO = re.compile(r"^(visite o site|site|saiba mais|ver mais|acesse)$", re.I)


def _texto_util(elemento) -> list[str]:
    """Textos visíveis do bloco, na ordem, sem vazios."""
    return [
        normalizar_texto(t)
        for t in elemento.stripped_strings
        if normalizar_texto(t)
    ]


def _extrair_do_bloco(bloco, base_url: str) -> dict | None:
    partes = _texto_util(bloco)
    if not partes:
        return None

    nome = estande = ""
    for indice, parte in enumerate(partes):
        if ROTULO_EMPRESA.match(parte) and indice + 1 < len(partes):
            candidato = partes[indice + 1]
            if not ROTULO_ESTANDE.match(candidato) and not LIXO.match(candidato):
                nome = candidato
        elif ROTULO_ESTANDE.match(parte) and indice + 1 < len(partes):
            candidato = partes[indice + 1]
            if not LIXO.match(candidato) and len(candidato) <= 24:
                estande = candidato

    if not nome:
        return None

    link = ""
    for a in bloco.select("a[href]"):
        href = a.get("href") or ""
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        # "Visite o site" costuma apontar para o site da própria empresa
        link = href
        break

    return {
        "nome": nome,
        "website": normalizar_url(link) if link and base_url not in link else "",
        "emails": [],
        "pais": "",
        "cidade": "",
        "endereco": "",
        "stand": estande,
        "categorias": [],
        "descricao": "",
        "ficha_feira": link if link and base_url in link else "",
        "fonte_plataforma": "rotulos",
        "fonte_url": base_url,
        "id_plataforma": "",
    }


def coletar(url: str, html: str | None = None) -> dict:
    """Lê uma lista rotulada de expositores."""
    if html is None:
        html = buscar(url, ttl_horas=12, timeout=30)

    sopa = BeautifulSoup(html, "lxml")
    for tag in sopa(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    # blocos que contêm o rótulo "Empresa:" — subimos até o ancestral que também
    # tem o estande, que é onde o registro inteiro vive
    blocos = []
    for texto in sopa.find_all(string=ROTULO_EMPRESA):
        atual = texto.parent
        for _ in range(4):
            if atual is None:
                break
            conteudo = atual.get_text(" ", strip=True)
            if re.search(r"estande|stand|booth", conteudo, re.IGNORECASE):
                blocos.append(atual)
                break
            atual = atual.parent

    expositores, vistos = [], set()
    for bloco in blocos:
        registro = _extrair_do_bloco(bloco, url)
        if not registro:
            continue
        chave = registro["nome"].lower()
        if chave in vistos:
            continue
        vistos.add(chave)
        expositores.append(registro)

    # variante: tudo num parágrafo só, "Empresa: X Estande: Y"
    if len(expositores) < MIN_ITENS:
        texto_inteiro = sopa.get_text("\n", strip=True)
        for achado in ROTULO_INLINE.finditer(texto_inteiro):
            nome = normalizar_texto(achado.group(1))
            if not nome or nome.lower() in vistos:
                continue
            vistos.add(nome.lower())
            expositores.append({
                "nome": nome, "website": "", "emails": [], "pais": "", "cidade": "",
                "endereco": "", "stand": normalizar_texto(achado.group(2)),
                "categorias": [], "descricao": "", "ficha_feira": "",
                "fonte_plataforma": "rotulos", "fonte_url": url, "id_plataforma": "",
            })

    if len(expositores) < MIN_ITENS:
        raise FalhouDeVerdade(url, f"lista rotulada com só {len(expositores)} itens")

    return {
        "evento": {"plataforma": "rotulos", "url_lista": url},
        "expositores": expositores,
        "total_informado": len(expositores),
    }
