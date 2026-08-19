"""Enriquecimento pela ficha pública de fornecedor do TradeChina (plataforma da Meorient).

É a melhor fonte de *porte* que achamos, e vem em português: a ficha traz "Total de
Empregados", "Ano Estabelecido", "Capital Registrado", faturamento anual, área de
fábrica e os principais produtos. Saber que a empresa tem 500 funcionários e fatura
US$ 50 milhões muda a abordagem do intérprete — é outro tipo de cliente.

Duas restrições que respeitamos:

  1. robots.txt do tradechina.com proíbe URLs com query string (Disallow: /*?*). Só
     lemos caminhos limpos, como /supplier/<nome>_<id>.html, que são permitidos.
  2. O site fica atrás do CAPTCHA do Tencent EdgeOne, que exige navegador de verdade.
     Por isso estas tarefas só rodam no seu PC (fonte marcada como residencial) e
     nunca no GitHub Actions.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ...core.modelos import normalizar_texto

REQUER_RESIDENCIAL = True  # CAPTCHA + sessão de navegador: só no PC

PADRAO_FICHA = re.compile(r"https?://[\w.-]*tradechina\.com/supplier/[\w%-]+_\d+\.html", re.I)

# Rótulos da ficha, em português (o site já entrega traduzido) e em inglês.
CAMPOS = {
    "funcionarios": ("Total de Empregados", "Funcionários", "Total Employees", "Employees"),
    "ano_fundacao": ("Ano Estabelecido", "Year Established", "Established"),
    "capital_registrado": ("Capital Registrado", "Registered Capital"),
    "receita_anual": ("Imposto Total Anual", "Valor de saída anual", "Annual Output Value",
                      "Annual Revenue", "Total Annual Revenue"),
    "area_fabrica": ("Tamanho da fábrica", "Área de Escritório", "Factory Size"),
    "localizacao": ("Localização de negócios", "Business Location"),
    "porto": ("Porto mais próximo", "Nearest Port"),
    "tipo_negocio": ("Tipo de Negócios", "Business Type"),
}

# "5-10 人" -> "5-10"; o 人 (pessoas) polui o filtro por porte
LIMPAR_PESSOAS = re.compile(r"\s*(人|pessoas|people|persons?|funcionários)\s*$", re.I)


def _valor_apos(texto: str, rotulos: tuple[str, ...]) -> str:
    """Na ficha, o valor vem na linha seguinte ao rótulo."""
    linhas = [linha.strip() for linha in texto.split("\n") if linha.strip()]
    for indice, linha in enumerate(linhas):
        for rotulo in rotulos:
            if linha.lower() == rotulo.lower() or linha.lower().startswith(rotulo.lower() + ":"):
                if ":" in linha and len(linha) > len(rotulo) + 1:
                    return linha.split(":", 1)[1].strip()
                if indice + 1 < len(linhas):
                    candidato = linhas[indice + 1]
                    # se o próximo também for rótulo, o campo está vazio
                    if any(candidato.lower() == r.lower()
                           for grupo in CAMPOS.values() for r in grupo):
                        return ""
                    return candidato
    return ""


def extrair_ficha(html: str) -> dict:
    """Lê os dados cadastrais da ficha já renderizada."""
    sopa = BeautifulSoup(html, "lxml")
    for tag in sopa(["script", "style", "noscript"]):
        tag.decompose()
    texto = re.sub(r"\n{2,}", "\n", sopa.get_text("\n", strip=True))

    dados: dict[str, str] = {}
    for campo, rotulos in CAMPOS.items():
        valor = normalizar_texto(_valor_apos(texto, rotulos))
        if valor and len(valor) < 120:
            dados[campo] = valor

    if dados.get("funcionarios"):
        dados["funcionarios"] = LIMPAR_PESSOAS.sub("", dados["funcionarios"]).strip()

    # cabeçalho: "EMPRESA - China - Foshan" e a linha "China - Foshan"
    titulo = sopa.title.get_text() if sopa.title else ""
    achado = re.search(r"China\s*[-–]\s*([A-Za-zÀ-ÿ\s]{3,30})", texto)
    if achado:
        dados.setdefault("cidade", normalizar_texto(achado.group(1)))
    if "Fabricante" in texto:
        dados.setdefault("tipo_negocio", "Fabricante")
    elif "Trading Company" in texto or "Empresa Comercial" in texto:
        dados.setdefault("tipo_negocio", "Trading Company")

    principais = _valor_apos(texto, ("Principais Produtos", "Main Products"))
    if principais:
        dados["produtos"] = [p.strip() for p in principais.split(",") if p.strip()][:12]

    dados["nome_na_ficha"] = normalizar_texto(titulo.split(" - ")[0])[:120]
    return dados


def enriquecer(url_ficha: str) -> dict:
    """Abre a ficha com a sessão do navegador e devolve os dados cadastrais.

    Levanta PrecisaIntervencao se cair no CAPTCHA — a tarefa volta para a fila
    pedindo que você rode `python -m scraper.cli login`.
    """
    from ...core.navegador import ler_pagina

    if not PADRAO_FICHA.match(url_ficha):
        from ...core.http import FalhouDeVerdade
        raise FalhouDeVerdade(url_ficha, "não é uma ficha de fornecedor do TradeChina")

    html = ler_pagina(url_ficha, esperar_ms=7000, rolar=3)
    dados = extrair_ficha(html)
    dados["fonte"] = "tradechina"
    dados["url_ficha"] = url_ficha
    return dados
