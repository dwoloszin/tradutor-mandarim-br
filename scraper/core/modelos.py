"""Identidade e formato dos registros.

O problema central aqui é dedupe: a mesma fábrica chinesa aparece como
"GUANGDONG AURICAN HARDWARE TECHNOLOGY CO., LTD." numa feira e
"Aurican Hardware" na outra. Se não juntarmos, o intérprete vê a mesma empresa
duas vezes e não enxerga que ela expõe em três feiras no ano — que é justamente
o sinal de que vale a pena abordá-la.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

CJK = re.compile(r"[一-鿿]")

# Sufixos societários que não distinguem empresa nenhuma.
SUFIXOS_SOCIETARIOS = re.compile(
    r"\b(CO|COMPANY|LTD|LTDA|LIMITED|INC|CORP|CORPORATION|GROUP|HOLDINGS?|"
    r"INDUSTRY|INDUSTRIAL|INTERNATIONAL|IMP|EXP|IMPORT|EXPORT|TRADING|TRADE|"
    r"TECHNOLOGY|TECHNOLOGIES|TECH|SA|S/A|EIRELI|ME|EPP)\b",
    re.IGNORECASE,
)
SUFIXOS_CHINES = ("有限公司", "股份有限公司", "集团", "公司", "厂")

# Hospedagens que não são o site da empresa — não servem como identidade.
DOMINIOS_GENERICOS = {
    "alibaba.com", "made-in-china.com", "1688.com", "aliexpress.com",
    "linkedin.com", "facebook.com", "instagram.com", "wechat.com",
    "globalsources.com", "ec21.com", "tradeindia.com", "indiamart.com",
    "sites.google.com", "wixsite.com", "weebly.com", "blogspot.com",
}


def normalizar_texto(texto: str | None) -> str:
    if not texto:
        return ""
    texto = unicodedata.normalize("NFC", str(texto))
    texto = texto.replace(" ", " ")
    return re.sub(r"\s+", " ", texto).strip()


def sem_acentos(texto: str) -> str:
    decomposto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")


def slug(texto: str, tamanho: int = 60) -> str:
    base = sem_acentos(normalizar_texto(texto)).lower()
    base = re.sub(r"[^a-z0-9一-鿿]+", "-", base).strip("-")
    return base[:tamanho] or "sem-nome"


def nome_canonico(nome: str) -> str:
    """Reduz o nome ao núcleo distintivo, para comparar empresas.

    'GUANGDONG AURICAN HARDWARE TECHNOLOGY CO., LTD.' -> 'GUANGDONG AURICAN HARDWARE'
    """
    texto = normalizar_texto(nome).upper()
    for sufixo in SUFIXOS_CHINES:
        texto = texto.replace(sufixo, " ")
    texto = re.sub(r"[.,()\[\]{}·・、/\\|]+", " ", texto)
    texto = SUFIXOS_SOCIETARIOS.sub(" ", texto)
    texto = re.sub(r"[^A-Z0-9一-鿿 ]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def dominio_raiz(url: str | None) -> str:
    """Domínio sem www e sem subdomínio de terceiro nível comum."""
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    try:
        host = (urlparse(url).netloc or "").lower().split(":")[0]
    except ValueError:
        return ""
    host = host.removeprefix("www.")
    return host


def dominio_proprio(url: str | None) -> str:
    """Só devolve o domínio se ele for da empresa (não Alibaba, não LinkedIn)."""
    host = dominio_raiz(url)
    if not host:
        return ""
    if any(host == g or host.endswith("." + g) for g in DOMINIOS_GENERICOS):
        return ""
    return host


def chave_empresa(nome: str, website: str | None = None) -> str:
    """Id estável da empresa.

    Preferimos o domínio próprio quando existe: é o identificador mais confiável
    (duas grafias do nome, um site só). Sem site, caímos no nome canônico.
    """
    host = dominio_proprio(website)
    if host:
        return f"emp:d:{host}"
    canonico = nome_canonico(nome)
    return f"emp:n:{slug(canonico)}"


# "41ª ABIMAD", "31st FEICON", "12º ENCONTRO" — o ordinal muda a cada edição e não
# pode entrar na identidade, senão a mesma feira vira um evento novo todo ano.
ORDINAL_EDICAO = re.compile(r"^\s*\d{1,3}\s*(ª|º|°|A|O|ST|ND|RD|TH)?\s+", re.IGNORECASE)


def nome_base_evento(nome: str) -> str:
    """Nome da feira sem número de edição e sem ano — a identidade que persiste."""
    texto = nome_canonico(nome)
    texto = re.sub(r"\b(19|20)\d{2}\b", " ", texto)
    texto = ORDINAL_EDICAO.sub("", texto)
    texto = re.sub(r"\b(EDICAO|EDITION|ANO)\b", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def chave_evento(nome: str, ano: int | str | None = None) -> str:
    """Id do evento. Com ano, identifica a edição; sem ano, a feira como série."""
    return f"ev:{slug(nome_base_evento(nome))}" + (f":{ano}" if ano else "")


def chave_feira(nome: str) -> str:
    """Id da feira como série (todas as edições) — usado para acumular histórico."""
    return f"fe:{slug(nome_base_evento(nome))}"


def chave_participacao(empresa_id: str, evento_id: str) -> str:
    return f"par:{empresa_id.split(':',1)[1]}@{evento_id.split(':',1)[1]}"


def tem_chines(texto: str | None) -> bool:
    return bool(CJK.search(texto or ""))


def novo_evento(**campos) -> dict:
    """Registro de evento com todos os campos previstos, para o site nunca ver undefined."""
    base = {
        "id": "", "nome": "", "nome_curto": "", "edicao": "",
        "site": "", "pagina_expositores": "", "pagina_local": "",
        "data_inicio": "", "data_fim": "", "data_texto": "",
        "local_nome": "", "cidade": "", "uf": "", "pavilhao": "",
        "setor": "", "categorias": [], "descricao": "",
        "prioridade": 5, "encerrado": False,
        "densidade_china": "", "plataforma": "",
        "total_expositores": 0, "total_chinesas": 0,
        "fontes": [],
    }
    base.update(campos)
    return base


def nova_empresa(**campos) -> dict:
    base = {
        "id": "", "nome": "", "nome_zh": "", "nome_canonico": "",
        "pais": "", "provincia": "", "cidade": "", "endereco": "",
        "website": "", "website_cn": "", "sites_alternativos": [],
        "emails": [], "telefones": [], "whatsapps": [],
        "wechat": "", "contato_nome": "", "cargo_contato": "",
        "funcionarios": "", "faixa_funcionarios": "", "ano_fundacao": "",
        "receita_anual": "", "tipo_negocio": "",
        "produtos": [], "setor": "", "descricao": "",
        "perfis": {},            # alibaba, made_in_china, linkedin...
        "score_china": 0, "motivos_deteccao": [],
        "enriquecida_em": "", "fontes": [],
    }
    base.update(campos)
    return base
