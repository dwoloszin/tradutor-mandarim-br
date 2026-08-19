"""Identificação de empresas chinesas por pontuação, não por sim/não.

A versão anterior marcava a empresa se achasse "china" em qualquer campo. Isso tinha
os dois erros ao mesmo tempo: pegava "CHINA GLASS" (que era o nome de um pavilhão) e
perdia "Zhejiang Chint Electric", que não tem a palavra "china" em lugar nenhum.

Aqui cada sinal soma pontos e fica registrado. O intérprete vê por que a empresa foi
marcada, e a lista tem três faixas:

  score >= 6   confirmada   — sinal forte e direto (país=China, .cn, +86, nome em chinês)
  score 3..5   provavel     — indícios convergentes (pinyin de província + Co.,Ltd)
  score 1..2   suspeita     — aparece só na aba de revisão, não na lista principal

Taiwan e Hong Kong entram marcados à parte: o mandarim é língua de negócio nos dois
casos (em HK junto com o cantonês), então interessam ao intérprete, mas não são RPC.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from ..core.modelos import tem_chines

CONFIRMADA = "confirmada"
PROVAVEL = "provavel"
SUSPEITA = "suspeita"
NAO = "nao"

# --- províncias e cidades industriais (pinyin) -------------------------------
# Presença no nome ou endereço da empresa é indício forte de origem chinesa.
PROVINCIAS = {
    "guangdong", "zhejiang", "jiangsu", "shandong", "fujian", "hebei", "henan",
    "hubei", "hunan", "anhui", "sichuan", "shaanxi", "shanxi", "liaoning",
    "jilin", "heilongjiang", "jiangxi", "yunnan", "guizhou", "gansu", "qinghai",
    "hainan", "guangxi", "ningxia", "xinjiang", "tibet", "chongqing",
}
CIDADES = {
    "shanghai", "xangai", "beijing", "pequim", "guangzhou", "canton", "shenzhen",
    "dongguan", "foshan", "zhongshan", "zhuhai", "shantou", "huizhou", "jiangmen",
    "ningbo", "yiwu", "wenzhou", "hangzhou", "jiaxing", "shaoxing", "taizhou",
    "suzhou", "wuxi", "changzhou", "nanjing", "nantong", "yangzhou", "xuzhou",
    "qingdao", "yantai", "weifang", "jinan", "zibo", "linyi", "weihai",
    "xiamen", "quanzhou", "fuzhou", "putian", "zhangzhou",
    "chengdu", "wuhan", "changsha", "zhengzhou", "hefei", "nanchang", "xian",
    "tianjin", "dalian", "shenyang", "harbin", "kunming", "guiyang", "lanzhou",
    "shijiazhuang", "baoding", "tangshan", "handan", "luoyang", "yueqing",
    "cixi", "ruian", "haining", "anping", "zhaoqing", "chaozhou", "jieyang",
}
# cidades industriais que dão nome a fabricantes conhecidos
CIDADES_FORTES = {"yiwu", "yueqing", "cixi", "shenzhen", "dongguan", "foshan", "ningbo"}

TERMOS_CHINA = {
    "china", "chinese", "chinesa", "chines", "chinês", "prc", "p.r.china",
    "mainland china", "made in china", "r.p. china", "república popular da china",
}
TERMOS_PAVILHAO = {
    "china pavilion", "pavilhao da china", "pavilhão da china", "ccpit",
    "china chamber", "camara de comercio chinesa", "câmara de comércio chinesa",
    "china council", "cccme", "china national",
}
TERMOS_TAIWAN = {"taiwan", "taipei", "formosa", "r.o.c", "chinese taipei"}
TERMOS_HONGKONG = {"hong kong", "hongkong", "h.k.", "kowloon", "sar hong kong"}

# Provedores de e-mail dominantes na China — sinal muito bom, quase nunca falso.
EMAIL_CHINES = re.compile(
    r"@(163|126|qq|sina|sohu|aliyun|foxmail|yeah|21cn|139|188|vip\.163|tom)\."
    r"(com|net|cn)\b|@[\w.-]+\.cn\b",
    re.IGNORECASE,
)

TELEFONE_CHINA = re.compile(r"(\+\s?86|0086|00\s?86)[\s\-.(]*\d{2,4}")
TLD_CHINES = re.compile(r"\.(cn|com\.cn|net\.cn|org\.cn|gov\.cn)(/|$|\?)", re.IGNORECASE)

# "Co., Ltd" é a tradução padrão de 有限公司 — sozinho é fraco (existe no mundo todo),
# mas combinado com pinyin de cidade vira indício forte.
SUFIXO_CO_LTD = re.compile(r"\bCO\.?,?\s*LTD\.?\b|\bCO\.?\s*,?\s*LIMITED\b", re.IGNORECASE)

PAISES_CHINA = {
    "china", "cn", "chn", "p.r.china", "pr china", "people's republic of china",
    "república popular da china", "republica popular da china", "chine", "中国",
}


def _campos(empresa: dict) -> dict[str, str]:
    return {
        "nome": str(empresa.get("nome") or ""),
        "pais": str(empresa.get("pais") or "").strip().lower(),
        "endereco": str(empresa.get("endereco") or ""),
        "descricao": str(empresa.get("descricao") or ""),
        "website": str(empresa.get("website") or ""),
        "email": " ".join(empresa.get("emails") or []) + " " + str(empresa.get("email") or ""),
        "telefone": " ".join(empresa.get("telefones") or []) + " " + str(empresa.get("telefone") or ""),
        "stand": str(empresa.get("stand") or ""),
        "pavilhao": str(empresa.get("pavilhao") or ""),
    }


def _palavras(texto: str) -> set[str]:
    return set(re.findall(r"[a-zà-ÿ]+", texto.lower()))


def avaliar(empresa: dict) -> dict:
    """Retorna {score, classificacao, motivos, origem} para uma empresa."""
    c = _campos(empresa)
    texto_livre = " ".join([c["nome"], c["endereco"], c["descricao"], c["pavilhao"], c["stand"]])
    minusculo = texto_livre.lower()
    palavras = _palavras(texto_livre)

    score = 0
    motivos: list[str] = []
    origem = "china"

    def somar(pontos: int, motivo: str) -> None:
        nonlocal score
        score += pontos
        motivos.append(motivo)

    # --- sinais fortes e diretos ---
    if c["pais"] in PAISES_CHINA:
        somar(6, "país do expositor informado como China")

    if tem_chines(c["nome"]):
        somar(6, "nome da empresa em caracteres chineses")
    elif tem_chines(texto_livre):
        somar(3, "caracteres chineses nos dados do expositor")

    if TLD_CHINES.search(c["website"]):
        somar(5, "site em domínio chinês (.cn)")

    if TELEFONE_CHINA.search(c["telefone"] + " " + texto_livre):
        somar(5, "telefone com DDI +86 (China)")

    if EMAIL_CHINES.search(c["email"]):
        somar(4, "e-mail em provedor/domínio chinês (163, QQ, .cn...)")

    # --- sinais geográficos ---
    provincias_achadas = PROVINCIAS & palavras
    if provincias_achadas:
        somar(4, f"província chinesa citada: {', '.join(sorted(provincias_achadas))}")

    cidades_achadas = CIDADES & palavras
    if cidades_achadas:
        pontos = 4 if (cidades_achadas & CIDADES_FORTES) else 3
        somar(pontos, f"cidade chinesa citada: {', '.join(sorted(cidades_achadas))}")

    # --- pavilhão / delegação oficial ---
    for termo in TERMOS_PAVILHAO:
        if termo in minusculo:
            somar(4, f"delegação/pavilhão chinês: \"{termo}\"")
            break

    # --- menção genérica a China (fraca: pode ser o nome de um evento) ---
    if not provincias_achadas and not cidades_achadas:
        for termo in TERMOS_CHINA:
            if termo in minusculo:
                somar(2, f"menção a China no texto: \"{termo}\"")
                break

    # --- Co., Ltd só conta junto com outro indício asiático ---
    if SUFIXO_CO_LTD.search(c["nome"]) and score > 0:
        somar(1, "razão social no formato \"Co., Ltd\" (有限公司)")

    # --- Taiwan / Hong Kong: interessam, mas são outra origem ---
    if any(t in minusculo for t in TERMOS_TAIWAN):
        origem = "taiwan"
        somar(4, "empresa de Taiwan (mandarim é a língua de negócios)")
    elif any(t in minusculo for t in TERMOS_HONGKONG):
        origem = "hong_kong"
        somar(4, "empresa de Hong Kong (mandarim usado em negócios)")

    if score >= 6:
        classificacao = CONFIRMADA
    elif score >= 3:
        classificacao = PROVAVEL
    elif score >= 1:
        classificacao = SUSPEITA
    else:
        classificacao = NAO

    return {
        "score": score,
        "classificacao": classificacao,
        "motivos": motivos,
        "origem": origem if score > 0 else "",
    }


def e_relevante(empresa: dict, minimo: str = PROVAVEL) -> bool:
    """Deve entrar na lista que o intérprete usa?"""
    ordem = {NAO: 0, SUSPEITA: 1, PROVAVEL: 2, CONFIRMADA: 3}
    return ordem[avaliar(empresa)["classificacao"]] >= ordem[minimo]


def links_pesquisa(nome: str, website: str = "") -> dict:
    """Links prontos para o intérprete conferir a empresa e achar o que faltar.

    Continuam úteis mesmo com o enriquecimento automático: são o plano B quando o
    robô não achou e-mail, e a forma de o intérprete validar antes de abordar.
    """
    q = quote_plus(nome)
    links = {
        "google": f"https://www.google.com/search?q={q}",
        "baidu": f"https://www.baidu.com/s?wd={q}",
        "alibaba": f"https://www.alibaba.com/trade/search?SearchText={q}",
        "made_in_china": f"https://www.made-in-china.com/productdirectory.do?word={q}",
        "1688": f"https://s.1688.com/selloffer/offer_search.htm?keywords={q}",
        "qcc": f"https://www.qcc.com/web/search?key={q}",
        "linkedin": f"https://www.linkedin.com/search/results/companies/?keywords={q}",
    }
    if website:
        dominio = re.sub(r"^https?://(www\.)?", "", website).split("/")[0]
        links["contato_no_site"] = (
            f"https://www.google.com/search?q=site:{dominio}+"
            f"(contact+OR+contato+OR+%E8%81%94%E7%B3%BB%E6%88%91%E4%BB%AC)"
        )
    return links
