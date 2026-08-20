"""Listas coladas de planilha: uma tabela por letra do alfabeto, um nome por célula.

Feira média não costuma ter sistema de expositor. O que ela tem é uma planilha, e
alguém cola essa planilha na página — o Google Sheets deixa a marca no HTML, um
`data-sheets-root` na tabela. É o caso da FESQUA: 25 tabelas, uma por letra, 314
empresas, e nada além do nome.

Por que isso precisa de adaptador próprio, se os nomes já estão no HTML: o raspador
genérico e o navegador enxergavam 24 das 314. Uma tabela sem classe, sem link e sem
estrutura de cartão não se parece com lista de expositores para quem procura padrão
visual — mas `data-sheets-root` é um sinal exato, sem chute.

Limite honesto: aqui só existe nome. Nem site, nem contato, nem país. Para as chinesas
o que resta é a busca por nome, e é por isso que este adaptador as marca para esse
caminho em vez de fingir que a coleta veio completa.
"""
from __future__ import annotations

from ...core.http import FalhouDeVerdade, buscar
from ...core.modelos import normalizar_texto

MIN_ITENS = 15

# Cabeçalho de planilha e sobra de formatação que não são empresa.
LIXO = {
    "expositor", "expositores", "empresa", "empresas", "nome", "razao social",
    "marca", "marcas", "estande", "stand", "a", "b", "c", "d", "e", "f", "g",
}


def _celulas(sopa) -> list[str]:
    nomes: list[str] = []
    for tabela in sopa.select("table[data-sheets-root], table[data-sheets-baot]"):
        for celula in tabela.select("td, th"):
            texto = normalizar_texto(celula.get_text(" ", strip=True))
            # Nome de empresa não passa de umas poucas dezenas de caracteres; texto
            # longo numa célula é descrição de feira que veio junto na colagem.
            if not texto or len(texto) < 2 or len(texto) > 90:
                continue
            if texto.lower() in LIXO:
                continue
            nomes.append(texto)
    return nomes


def coletar(url: str, html: str | None = None) -> dict:
    if html is None:
        html = buscar(url, ttl_horas=12, timeout=30)

    from bs4 import BeautifulSoup
    sopa = BeautifulSoup(html, "lxml")

    vistos: set[str] = set()
    expositores: list[dict] = []
    for nome in _celulas(sopa):
        chave = nome.lower()
        if chave in vistos:
            continue
        vistos.add(chave)
        expositores.append({
            "nome": nome,
            "website": "", "emails": [], "pais": "", "cidade": "", "endereco": "",
            "stand": "", "categorias": [], "descricao": "", "ficha_feira": "",
            "fonte_plataforma": "planilha",
            "fonte_url": url,
            "id_plataforma": "",
        })

    if len(expositores) < MIN_ITENS:
        raise FalhouDeVerdade(url, f"só {len(expositores)} células de planilha")

    return {
        "evento": {"plataforma": "planilha", "url_lista": url},
        "expositores": expositores,
        "total_informado": len(expositores),
    }
