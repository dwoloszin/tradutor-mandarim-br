"""Monitor das notícias do Consulado-Geral da China em São Paulo.

O consulado publica, de vez em quando, vagas de emprego — e são vagas que interessam
diretamente a quem fala mandarim: assistente de seção consular, tradutor, intérprete.
São raras (uma ou duas por ano) e ficam perdidas no meio de notícias diplomáticas, então
quem não acompanha o site todo dia perde o prazo.

Por isso o monitor faz duas coisas diferentes:
  - guarda todas as notícias, para termos histórico e detectar o que é novo;
  - marca as que falam de vaga/contratação, que são as que o site destaca.

A checagem é barata (uma página) e roda todo dia junto com o resto, inclusive na nuvem.

Também vale como sinal de mercado: quando o consulado anuncia missão comercial ou
delegação chinesa vindo ao Brasil, costuma haver demanda de intérprete logo depois.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ...core.http import Bloqueado, FalhouDeVerdade, buscar
from ...core.modelos import normalizar_texto, slug
from ...core.store import agora_iso

CANAL = "https://saopaulo.china-consulate.gov.cn/pl/xwdt/"

# links de notícia: /202311/t20231116_11181056.htm
PADRAO_NOTICIA = re.compile(r"/(\d{6})/t(\d{8})_(\d+)\.htm")
PADRAO_DATA = re.compile(r"(\d{4}-\d{2}-\d{2})")

# O que faz a notícia valer um alerta no site.
TERMOS_VAGA = [
    "vaga", "vagas", "emprego", "contratação", "contratacao", "contrata",
    "recrutamento", "processo seletivo", "seleção de pessoal", "selecao de pessoal",
    "oportunidade de trabalho", "currículo", "curriculo", "candidato",
    "assistente", "tradutor", "intérprete", "interprete",
    # o consulado às vezes publica o aviso em chinês
    "招聘", "招募", "岗位", "职位",
]
# Termos que sozinhos não bastam: "assistente" e "tradutor" aparecem em notícia
# diplomática comum ("o tradutor acompanhou a comitiva"). Exigem companhia.
TERMOS_FRACOS = {"assistente", "tradutor", "intérprete", "interprete", "candidato"}
TERMOS_FORTES = [t for t in TERMOS_VAGA if t not in TERMOS_FRACOS]

# Notícia de missão/delegação: não é vaga, mas antecede demanda de intérprete.
TERMOS_MISSAO = [
    "missão comercial", "missao comercial", "delegação", "delegacao",
    "feira", "expo", "câmara de comércio", "camara de comercio",
    "rodada de negócios", "rodada de negocios", "investimento",
    "empresários", "empresarios", "comitiva",
]


def _classificar(titulo: str, texto: str = "") -> tuple[str, list[str]]:
    """Devolve (tipo, termos_encontrados). Tipo: vaga, missao ou noticia."""
    alvo = f"{titulo} {texto}".lower()

    fortes = [t for t in TERMOS_FORTES if t in alvo]
    fracos = [t for t in TERMOS_FRACOS if t in alvo]

    # "vaga"/"recrutamento" sozinho já basta; "tradutor" só conta acompanhado
    if fortes:
        return "vaga", fortes + fracos
    if len(fracos) >= 2:
        return "vaga", fracos

    missao = [t for t in TERMOS_MISSAO if t in alvo]
    if missao:
        return "missao", missao
    return "noticia", []


def _data_do_link(href: str, contexto: str) -> str:
    achado = PADRAO_DATA.search(contexto)
    if achado:
        return achado.group(1)
    achado = PADRAO_NOTICIA.search(href)
    if achado:
        bruto = achado.group(2)  # 20231116
        try:
            return datetime.strptime(bruto, "%Y%m%d").date().isoformat()
        except ValueError:
            return ""
    return ""


def _ler_corpo(url: str) -> str:
    """Texto da notícia, para confirmar a classificação feita pelo título."""
    try:
        html = buscar(url, ttl_horas=24 * 30)  # notícia publicada não muda
    except (Bloqueado, FalhouDeVerdade):
        return ""
    sopa = BeautifulSoup(html, "lxml")
    for tag in sopa(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    return normalizar_texto(sopa.get_text(" "))[:4000]


def coletar(hoje: date | None = None, ler_corpo_de_todas: bool = False) -> list[dict]:
    """Lê o canal de notícias e devolve os itens já classificados."""
    hoje = hoje or date.today()
    html = buscar(CANAL, ttl_horas=6)
    sopa = BeautifulSoup(html, "lxml")

    itens: list[dict] = []
    vistos: set[str] = set()

    for a in sopa.select("a[href]"):
        href = a.get("href") or ""
        if not PADRAO_NOTICIA.search(href):
            continue
        url = urljoin(CANAL, href)
        if url in vistos:
            continue
        vistos.add(url)

        titulo = normalizar_texto(a.get_text(" "))
        if not titulo or len(titulo) < 8:
            continue

        pai = a.find_parent(["li", "div", "td", "tr"])
        contexto = normalizar_texto(pai.get_text(" ")) if pai else ""
        data_publicacao = _data_do_link(href, contexto)

        tipo, termos = _classificar(titulo)

        # O título nem sempre entrega: "Aviso Importante" pode ser vaga. Abrimos o
        # corpo quando há qualquer suspeita, e não em toda notícia — seriam 30
        # requisições por dia num site de consulado, o que é abusivo e desnecessário.
        corpo = ""
        if ler_corpo_de_todas or tipo != "noticia" or "aviso" in titulo.lower():
            corpo = _ler_corpo(url)
            if corpo:
                tipo, termos = _classificar(titulo, corpo)

        itens.append({
            "id": f"cons:{slug(titulo, 50)}:{data_publicacao or 'sem-data'}",
            "fonte": "Consulado-Geral da China em São Paulo",
            "canal": CANAL,
            "titulo": titulo,
            "url": url,
            "data_publicacao": data_publicacao,
            "tipo": tipo,
            "termos_encontrados": termos[:6],
            "resumo": corpo[:400],
            "visto_em": agora_iso(),
        })

    return itens
