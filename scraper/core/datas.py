"""Interpretação das datas das feiras.

As agendas dos centros de exposição escrevem data de um jeito cada: "27 - 30 jan",
"09 a 13 de março de 2026", "16 - 18 set. 2026", "26 a 30 de abril". Muitas omitem o
ano, o que é justamente o dado de que precisamos para saber se a feira já passou.

Sem data resolvida não dá para cumprir o pedido de esconder feira encerrada, nem para
priorizar quem expõe daqui a três semanas — que é quando o intérprete precisa
prospectar. Então normalizamos tudo para ISO (AAAA-MM-DD).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

MESES = {
    "jan": 1, "janeiro": 1, "fev": 2, "fevereiro": 2, "mar": 3, "março": 3, "marco": 3,
    "abr": 4, "abril": 4, "mai": 5, "maio": 5, "jun": 6, "junho": 6,
    "jul": 7, "julho": 7, "ago": 8, "agosto": 8, "set": 9, "setembro": 9, "sete": 9,
    "out": 10, "outubro": 10, "nov": 11, "novembro": 11, "dez": 12, "dezembro": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

_MES_RE = "|".join(sorted(MESES, key=len, reverse=True))

# "27 - 30 jan 2026" / "09 a 13 de março de 2026" / "16 - 18 set. 2026"
PERIODO = re.compile(
    rf"(?P<d1>\d{{1,2}})\s*(?:[-–—]|a|até|to)\s*(?P<d2>\d{{1,2}})\s*"
    rf"(?:de\s+)?(?P<mes>{_MES_RE})\.?\s*(?:de\s+)?(?P<ano>\d{{4}})?",
    re.IGNORECASE,
)
# "27 jan a 02 fev 2026" (vira o mês no meio)
PERIODO_DOIS_MESES = re.compile(
    rf"(?P<d1>\d{{1,2}})\s*(?:de\s+)?(?P<mes1>{_MES_RE})\.?\s*(?:[-–—]|a|até)\s*"
    rf"(?P<d2>\d{{1,2}})\s*(?:de\s+)?(?P<mes2>{_MES_RE})\.?\s*(?:de\s+)?(?P<ano>\d{{4}})?",
    re.IGNORECASE,
)
DATA_UNICA = re.compile(
    rf"(?P<d>\d{{1,2}})\s*(?:de\s+)?(?P<mes>{_MES_RE})\.?\s*(?:de\s+)?(?P<ano>\d{{4}})?",
    re.IGNORECASE,
)
ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _ano_provavel(mes: int, dia: int, referencia: date) -> int:
    """Sem ano no texto, assume a próxima ocorrência daquele mês/dia.

    Uma agenda publicada em agosto que diz "27-30 jan" está falando de janeiro do ano
    que vem; se dissesse "10-12 set", é deste ano. A regra: se a data já passou há mais
    de 60 dias, é do ano seguinte. A folga de 60 dias evita jogar para 2027 uma feira
    que aconteceu no mês passado e ainda interessa como histórico.
    """
    try:
        candidata = date(referencia.year, mes, min(dia, 28))
    except ValueError:
        return referencia.year
    if candidata < referencia - timedelta(days=60):
        return referencia.year + 1
    return referencia.year


def interpretar_periodo(texto: str, referencia: date | None = None) -> tuple[str, str]:
    """Devolve (data_inicio_iso, data_fim_iso). Strings vazias se não der para ler."""
    if not texto:
        return "", ""
    referencia = referencia or date.today()
    texto = texto.strip()

    achado = ISO.search(texto)
    if achado:
        inicio = achado.group(0)
        todas = ISO.findall(texto)
        fim = "-".join(todas[-1]) if len(todas) > 1 else inicio
        return inicio, fim

    achado = PERIODO_DOIS_MESES.search(texto)
    if achado:
        g = achado.groupdict()
        mes1, mes2 = MESES[g["mes1"].lower()], MESES[g["mes2"].lower()]
        ano = int(g["ano"]) if g["ano"] else _ano_provavel(mes1, int(g["d1"]), referencia)
        ano_fim = ano + 1 if mes2 < mes1 else ano
        return (
            f"{ano:04d}-{mes1:02d}-{int(g['d1']):02d}",
            f"{ano_fim:04d}-{mes2:02d}-{int(g['d2']):02d}",
        )

    achado = PERIODO.search(texto)
    if achado:
        g = achado.groupdict()
        mes = MESES[g["mes"].lower()]
        dia1, dia2 = int(g["d1"]), int(g["d2"])
        ano = int(g["ano"]) if g["ano"] else _ano_provavel(mes, dia1, referencia)

        # "27 a 01 de maio": o mês escrito é o do FIM, e o início ficou no mês anterior.
        # Sem isso o evento nasce com fim antes do começo e a conta de "já encerrou"
        # dá qualquer coisa.
        if dia2 < dia1:
            mes_inicio = mes - 1 or 12
            ano_inicio = ano - 1 if mes_inicio == 12 else ano
            return (
                f"{ano_inicio:04d}-{mes_inicio:02d}-{dia1:02d}",
                f"{ano:04d}-{mes:02d}-{dia2:02d}",
            )
        return (
            f"{ano:04d}-{mes:02d}-{dia1:02d}",
            f"{ano:04d}-{mes:02d}-{dia2:02d}",
        )

    achado = DATA_UNICA.search(texto)
    if achado:
        g = achado.groupdict()
        mes = MESES[g["mes"].lower()]
        ano = int(g["ano"]) if g["ano"] else _ano_provavel(mes, int(g["d"]), referencia)
        iso = f"{ano:04d}-{mes:02d}-{int(g['d']):02d}"
        return iso, iso

    return "", ""


def encerrado(data_fim_iso: str, hoje: date | None = None) -> bool:
    """A feira já acabou? Sem data, assumimos que não (melhor mostrar do que sumir)."""
    if not data_fim_iso:
        return False
    hoje = hoje or date.today()
    try:
        fim = datetime.strptime(data_fim_iso[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return fim < hoje


def dias_ate(data_inicio_iso: str, hoje: date | None = None) -> int | None:
    """Quantos dias faltam para começar. Negativo se já começou; None se sem data."""
    if not data_inicio_iso:
        return None
    hoje = hoje or date.today()
    try:
        inicio = datetime.strptime(data_inicio_iso[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (inicio - hoje).days
