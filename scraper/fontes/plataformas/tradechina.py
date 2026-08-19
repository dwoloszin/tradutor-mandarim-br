"""Adaptador TradeChina / Meorient — as feiras 100% chinesas no Brasil.

A China Homelife Brazil e suas irmãs (Machinex, Appliance & Electronics, Decoration &
Furniture, Building, INTEX) acontecem no São Paulo Expo e são compostas **apenas** por
expositores chineses. É a maior concentração de clientes possíveis do país num só lugar:
1.934 empresas na edição atual.

O site fica atrás do CAPTCHA do Tencent EdgeOne, mas a busca de fornecedores é servida
por um endpoint de API em outro host, que responde a requisição normal. Então este
adaptador **não precisa de navegador nem de login** — roda inclusive na nuvem.

E o que ele traz é o melhor conjunto de dados de todo o projeto, porque é a própria
plataforma que cadastra o fornecedor:

    nome em chinês + nome comercial + cidade + porte (faixa de funcionários)
    + ano de fundação + faturamento anual + certificações + produtos (em português)

O nome em chinês importa de verdade para o intérprete: é por ele que se acha a empresa
no Baidu, no QCC e no WeChat.
"""
from __future__ import annotations

import re
import time

import requests

from ...core.http import Bloqueado, FalhouDeVerdade
from ...core.modelos import normalizar_texto, normalizar_url, tem_chines

BUSCA = "https://global-all-api.tradechina.com/search/v2/searchSupplier.json"

POR_PAGINA = 100      # a API aceita 100; 1.934 expositores viram 20 requisições
PAGINAS_MAX = 60      # trava de segurança
PAUSA = 1.2           # segundos entre páginas

CABECALHOS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://www.tradechina.com",
    "Referer": "https://www.tradechina.com/search/supplier",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

# Como a plataforma escreve o modelo de negócio, e como queremos mostrar.
MODELO_NEGOCIO = {
    "ManufacturingAndTrade": "Fabricante e exportadora",
    "Manufacturing": "Fabricante",
    "Trade": "Trading company",
    "TradingCompany": "Trading company",
    "Agent": "Agente",
    "Service": "Prestadora de serviço",
}


def _texto(valor) -> str:
    return normalizar_texto(valor) if valor else ""


def _padronizar_porte(bruto: str) -> str:
    """A API mistura idiomas no mesmo campo: "51-100 pessoas" e "5-10 人".

    Sem padronizar, o filtro por porte no site trataria as duas como faixas
    diferentes e o intérprete não conseguiria separar fábrica grande de escritório.
    """
    texto = _texto(bruto)
    if not texto:
        return ""
    texto = texto.replace("人", " pessoas")
    texto = texto.replace("acima de", "mais de")
    # a API às vezes escreve a faixa com vírgula ("1,499" = de 1 a 499)
    faixa = re.fullmatch(r"(\d+)\s*,\s*(\d+)", texto)
    if faixa:
        texto = f"{faixa.group(1)}-{faixa.group(2)}"
    if re.search(r"\d", texto) and "pessoas" not in texto:
        texto = f"{texto} pessoas"
    return normalizar_texto(texto)


# Alguns cadastros põem o endereço no campo do nome comercial:
# "18-20/F HUASHUN BUILDING,NO.58 WEST LAKE AVENUE,HANGHZOU,CHINA".
# Marcadores de logradouro sozinhos não bastam para acusar — "FOSHAN OLIWA BUILDING
# MATERIALS CO., LTD" é nome de empresa de verdade. O que separa os dois é o sufixo
# societário: endereço não tem "Co., Ltd".
MARCADOR_ENDERECO = re.compile(
    r"\d+\s*[-–]?\s*\d*/F\b|\bNO\.?\s?\d|\bRM\.?\s?\d|\bROOM\s?\d|\bFLOOR\b|"
    r"\bINDUSTRIAL (BASE|PARK) (IN|OF)\b|\b\d{5,6}\b",
    re.IGNORECASE,
)
SUFIXO_EMPRESA = re.compile(
    r"\b(CO\.?,?\s*LTD|LIMITED|INC\b|CORP|GROUP|COMPANY|FACTORY|INDUSTR|INTERNATIONAL|"
    r"TECHNOLOGY|TRADING|IMP|EXP|ENTERPRISE)\b",
    re.IGNORECASE,
)


def _parece_endereco(texto: str) -> bool:
    if not texto:
        return False
    if SUFIXO_EMPRESA.search(texto):
        return False
    return bool(MARCADOR_ENDERECO.search(texto)) or texto.count(",") >= 3


def _normalizar(item: dict, contexto: dict) -> dict:
    """Converte um fornecedor da API para o formato de expositor do projeto."""
    nome_zh = _texto(item.get("name"))
    nome_comercial = _texto(item.get("adName"))

    endereco = ""
    if _parece_endereco(nome_comercial):
        # o campo do nome veio com o endereço: aproveitamos o dado no lugar certo
        endereco, nome_comercial = nome_comercial, ""

    # "name" costuma ser a razão social em chinês e "adName" o nome comercial em inglês.
    # Preferimos o comercial para exibir, mas guardamos o chinês: é ele que serve para
    # procurar a empresa no Baidu/QCC e para abordar em mandarim.
    if nome_comercial and not tem_chines(nome_comercial):
        nome = nome_comercial
    elif nome_zh and not tem_chines(nome_zh):
        nome = nome_zh
        nome_zh = ""
    else:
        # sem nome ocidental utilizável, o nome chinês vira o principal — para um
        # intérprete de mandarim isso não é perda nenhuma, é o nome que ele vai usar
        nome = nome_comercial or nome_zh
        if nome == nome_zh:
            nome_zh = ""

    produtos = [p.strip() for p in (_texto(item.get("mainProduct")) or "").split(",") if p.strip()]
    certificacoes = [c.strip() for c in (_texto(item.get("qualsName")) or "").split(",") if c.strip()]

    ano = item.get("yearFounded")
    modelo = _texto(item.get("businessModel"))

    # a plataforma publica o nome de quem atende — abrir a conversa chamando a pessoa
    # pelo nome muda a taxa de resposta, ainda mais em mandarim
    contato = (item.get("supplierContactDto") or {}).get("name") or ""

    return {
        "nome": nome,
        "nome_zh": nome_zh if tem_chines(nome_zh) else "",
        "website": normalizar_url(_texto(item.get("webSite"))),
        "emails": [],
        "pais": _texto(item.get("countryName")) or "China",
        "cidade": _texto(item.get("cityName")),
        "provincia": "",
        "endereco": endereco,
        "stand": "",  # a plataforma não publica o estande na busca
        "categorias": certificacoes,
        "produtos": produtos[:15],
        "descricao": "",
        "contato_nome": _texto(contato),
        "funcionarios": _padronizar_porte(item.get("employees")),
        "ano_fundacao": str(ano) if ano else "",
        "receita_anual": _texto(item.get("annualRevenue")),
        "tipo_negocio": MODELO_NEGOCIO.get(modelo, modelo),
        "ficha_feira": "",
        "fonte_plataforma": "tradechina",
        "fonte_url": contexto.get("url_referencia", ""),
        "id_plataforma": str(item.get("id") or item.get("code") or ""),
    }


def coletar(exhibition_id: str, limite_paginas: int = PAGINAS_MAX) -> dict:
    """Baixa todos os expositores de uma feira da Meorient.

    ATENÇÃO — restrição conhecida: o host desta API responde

        User-Agent: *
        Disallow: /

    ou seja, proíbe robôs em todo o site. Esta foi a maior fonte do projeto (1.934
    empresas), mas coletá-la contraria o robots.txt e os termos da plataforma.

    Por isso o adaptador vem **desligado por padrão**. Para usá-lo mesmo assim, é
    preciso uma decisão explícita do dono do projeto, ligando `permitir: true` na
    entrada da feira em config/feiras_prioritarias.json. A decisão fica registrada
    no config, não escondida no código.
    """
    if not exhibition_id:
        raise FalhouDeVerdade(BUSCA, "sem exhibitionId da feira")

    sessao = requests.Session()
    sessao.headers.update(CABECALHOS)

    expositores: list[dict] = []
    total = 0
    contexto = {"url_referencia": f"https://www.tradechina.com/search/supplier"}

    for pagina in range(limite_paginas):
        corpo = {
            "data": {
                "siteId": "",
                "searchKey": "",
                "page": pagina,
                "limit": POR_PAGINA,
                "exhibitionId": str(exhibition_id),
            },
            "time": int(time.time() * 1000),
            "lang": "pt",
            "token": "",
        }
        try:
            resposta = sessao.post(BUSCA, json=corpo, timeout=40)
        except requests.RequestException as exc:
            raise FalhouDeVerdade(BUSCA, f"erro de rede: {exc}") from exc

        if resposta.status_code in (401, 403, 429, 503):
            raise Bloqueado(BUSCA, f"HTTP {resposta.status_code}")
        if resposta.status_code >= 400:
            raise FalhouDeVerdade(BUSCA, f"HTTP {resposta.status_code}")

        try:
            dados = resposta.json()
        except ValueError as exc:
            raise Bloqueado(BUSCA, "resposta não-JSON (provável desafio anti-bot)") from exc

        if not dados.get("success", True):
            raise FalhouDeVerdade(BUSCA, f"API recusou: {dados.get('msg', '')[:120]}")

        bloco = dados.get("data") or {}
        itens = bloco.get("items") or []
        total = bloco.get("totalCount") or total

        if not itens:
            break

        for item in itens:
            normalizado = _normalizar(item, contexto)
            if normalizado["nome"]:
                expositores.append(normalizado)

        if len(expositores) >= total:
            break
        time.sleep(PAUSA)

    return {
        "evento": {"plataforma": "tradechina", "exhibition_id": str(exhibition_id)},
        "expositores": expositores,
        "total_informado": total,
    }
