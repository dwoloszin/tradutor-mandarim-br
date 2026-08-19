"""Enriquecimento a partir do site da própria empresa.

É a fonte mais universal: funciona em qualquer IP (roda bem na nuvem), não depende de
plataforma de terceiro e traz justamente o que o intérprete precisa para abordar —
e-mail comercial, telefone com DDI, WeChat e WhatsApp.

Visitamos a home e, dela, as páginas de contato. Procuramos também a versão chinesa do
site (.cn ou /cn/), porque em muitos fabricantes o WeChat e o telefone da matriz só
aparecem lá — na versão em inglês fica só um formulário.

Nunca passamos de poucas páginas por empresa: o objetivo é achar o contato, não copiar
o site inteiro.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ...core.http import Bloqueado, FalhouDeVerdade, buscar
from ...core.modelos import normalizar_texto

CAMINHOS_CONTATO = [
    "/contact", "/contact-us", "/contactus", "/contact.html", "/contact-us.html",
    "/contato", "/contacto", "/about", "/about-us", "/aboutus", "/about.html",
    "/lianxi", "/lianxiwomen", "/contact_us.html", "/en/contact", "/en/contact-us",
    "/cn/contact", "/index.php/contact", "/pages/contact", "/support/contact",
]

EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,12}")
# telefone chinês: +86 seguido de 10-12 dígitos, com separadores variados
TELEFONE_CHINA = re.compile(r"(?:\+|00)\s?86[\s\-.()]*\d[\d\s\-.()]{7,16}\d")
TELEFONE_BR = re.compile(r"(?:\+?55)?[\s(]*\d{2}[\s)]*[\s-]?9?\d{4}[\s-]?\d{4}")
WHATSAPP_LINK = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(\+?\d[\d\s%+-]{7,20})")

# WeChat aparece como "WeChat: abc", "微信：abc", "WeChat ID abc"
WECHAT = re.compile(
    r"(?:wechat|we-chat|weixin|微信(?:号|ID)?)\s*[:：#]?\s*([A-Za-z0-9_-]{5,25})",
    re.IGNORECASE,
)

# e-mails que nunca são contato comercial da empresa
EMAIL_LIXO = re.compile(
    r"(example|test|your|email@|name@|sentry|wixpress|godaddy|"
    r"\.png|\.jpg|\.jpeg|\.gif|\.webp|\.svg|@2x|domain\.com|yourdomain|"
    r"sample|noreply|no-reply|donotreply)",
    re.IGNORECASE,
)

MAX_PAGINAS = 4


def _limpar_telefone(bruto: str) -> str:
    numero = re.sub(r"[^\d+]", "", bruto)
    if numero.startswith("00"):
        numero = "+" + numero[2:]
    return numero


def _emails_validos(texto: str, dominio: str = "") -> list[str]:
    achados = []
    for bruto in EMAIL.findall(texto):
        email = bruto.strip().lower().rstrip(".")
        if EMAIL_LIXO.search(email):
            continue
        if len(email) > 60:
            continue
        if email not in achados:
            achados.append(email)
    # e-mail no domínio da própria empresa vem primeiro: é o contato mais confiável
    if dominio:
        achados.sort(key=lambda e: 0 if e.endswith("@" + dominio) or dominio in e else 1)
    return achados[:8]


def _links_contato(sopa: BeautifulSoup, base: str) -> list[str]:
    """Links da home que parecem levar a uma página de contato."""
    achados = []
    padrao = re.compile(r"contact|contato|contacto|lianxi|联系|about|sobre|nos", re.I)
    for a in sopa.select("a[href]"):
        href = a.get("href", "")
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        if padrao.search(href) or padrao.search(a.get_text() or ""):
            url = urljoin(base, href)
            if url not in achados:
                achados.append(url)
    return achados[:3]


def _link_site_chines(sopa: BeautifulSoup, base: str) -> str:
    """Acha o link para a versão chinesa do site."""
    for a in sopa.select("a[href]"):
        texto = (a.get_text() or "").strip()
        href = a.get("href", "")
        if not href:
            continue
        if texto in ("中文", "简体中文", "中文版", "CN", "cn") or re.search(r"/(cn|zh|zh-cn|chinese)(/|$)", href, re.I):
            return urljoin(base, href)
    return ""


def _extrair(html: str, url: str, dominio: str) -> dict:
    sopa = BeautifulSoup(html, "lxml")
    for tag in sopa(["script", "style", "noscript"]):
        tag.decompose()
    texto = sopa.get_text(" ", strip=True)

    # mailto e tel são as fontes mais confiáveis: são declarações explícitas
    emails = []
    telefones = []
    for a in sopa.select("a[href^='mailto:']"):
        email = a.get("href", "")[7:].split("?")[0].strip().lower()
        if email and not EMAIL_LIXO.search(email) and email not in emails:
            emails.append(email)
    for a in sopa.select("a[href^='tel:']"):
        numero = _limpar_telefone(a.get("href", "")[4:])
        if len(numero) >= 8 and numero not in telefones:
            telefones.append(numero)

    emails.extend(e for e in _emails_validos(texto, dominio) if e not in emails)

    for bruto in TELEFONE_CHINA.findall(texto)[:5]:
        numero = _limpar_telefone(bruto)
        if numero not in telefones:
            telefones.append(numero)

    whatsapps = []
    for a in sopa.select("a[href]"):
        achado = WHATSAPP_LINK.search(a.get("href", ""))
        if achado:
            numero = _limpar_telefone(achado.group(1))
            if numero and numero not in whatsapps:
                whatsapps.append(numero)

    wechat = ""
    achado = WECHAT.search(texto)
    if achado:
        candidato = achado.group(1)
        # evita capturar palavra solta tipo "WeChat official"
        if not candidato.lower() in ("official", "account", "channel", "number", "contact"):
            wechat = candidato

    return {
        "emails": emails[:8],
        "telefones": telefones[:6],
        "whatsapps": whatsapps[:3],
        "wechat": wechat,
        "site_chines": _link_site_chines(sopa, url),
        "links_contato": _links_contato(sopa, url),
        "titulo": normalizar_texto(sopa.title.get_text() if sopa.title else "")[:150],
    }


def enriquecer(website: str) -> dict:
    """Visita o site da empresa e devolve os contatos encontrados.

    Levanta Bloqueado se o site barrar por IP (a tarefa vira "adiado_local"),
    e FalhouDeVerdade se o site não existe mais.
    """
    if not website:
        raise FalhouDeVerdade("", "empresa sem site")
    if "://" not in website:
        website = "https://" + website

    dominio = (urlparse(website).netloc or "").lower().removeprefix("www.")

    resultado = {
        "emails": [], "telefones": [], "whatsapps": [], "wechat": "",
        "website_cn": "", "titulo_site": "", "paginas_lidas": [],
    }

    html = buscar(website, ttl_horas=24 * 7)  # site de empresa muda devagar
    dados = _extrair(html, website, dominio)
    resultado["paginas_lidas"].append(website)
    _acumular(resultado, dados)

    # páginas de contato: as da home, mais os caminhos convencionais
    candidatas = list(dados["links_contato"])
    candidatas.extend(urljoin(website, c) for c in CAMINHOS_CONTATO[:6])

    lidas = 1
    vistas = {website}
    for url in candidatas:
        if lidas >= MAX_PAGINAS:
            break
        if url in vistas:
            continue
        vistas.add(url)
        try:
            html_contato = buscar(url, ttl_horas=24 * 7, tentativas=1)
        except (Bloqueado, FalhouDeVerdade):
            continue
        lidas += 1
        resultado["paginas_lidas"].append(url)
        _acumular(resultado, _extrair(html_contato, url, dominio))

    if dados.get("site_chines"):
        resultado["website_cn"] = dados["site_chines"]
    resultado["titulo_site"] = dados.get("titulo", "")
    return resultado


def _acumular(alvo: dict, novo: dict) -> None:
    for campo in ("emails", "telefones", "whatsapps"):
        for valor in novo.get(campo, []):
            if valor not in alvo[campo]:
                alvo[campo].append(valor)
    if not alvo["wechat"] and novo.get("wechat"):
        alvo["wechat"] = novo["wechat"]
    if not alvo["website_cn"] and novo.get("site_chines"):
        alvo["website_cn"] = novo["site_chines"]
