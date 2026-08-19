"""Adaptador genérico para listas que só existem depois do JavaScript rodar.

É a rede de segurança do projeto. Quando a feira não usa nenhuma plataforma conhecida
(Swapcard, RX, TradeChina, The smarter E) e o HTML servido vem vazio, abrimos a página
num navegador, deixamos o JS montar a lista, rolamos até o fim e lemos o que apareceu.

Cobre casos como febrabantech.com/expositores e rio.websummit.com/partners, além das
feiras em Webflow/React que hoje ficam marcadas como "plataforma nova".

Precisão menor que a dos adaptadores dedicados, e isso é assumido: aqui não há campo
de país nem de estande, só nome e link. Serve para saber *quem* expõe; o país e o
contato vêm depois, da detecção e do enriquecimento. Por isso ele é sempre o último
recurso, nunca a primeira tentativa.
"""
from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urljoin, urlparse

from ...core.http import FalhouDeVerdade
from ...core.modelos import normalizar_texto

# Precisa de navegador com JS: só roda no PC, nunca na nuvem.
REQUER_RESIDENCIAL = True

MIN_ITENS = 12          # abaixo disso é menu ou destaque, não diretório
MAX_ROLAGENS = 12
TEXTOS_CARREGAR_MAIS = [
    "carregar mais", "ver mais", "mostrar mais", "load more", "show more",
    "ver todos", "próxima", "next",
]

# Palavras que denunciam item de navegação, não empresa.
RUIDO = re.compile(
    r"^(home|in[íi]cio|sobre|contato|not[íi]cias|blog|login|entrar|cadastr|"
    r"pol[íi]tica|privacidade|cookies|termos|menu|buscar|search|filtrar|"
    r"todos|todas|ver mais|carregar|compartilhar|voltar|pr[óo]xim|anterior|"
    r"facebook|instagram|linkedin|youtube|twitter|whatsapp)",
    re.IGNORECASE,
)


# Algumas listas juntam nome e estande num campo só: "4MATT-A3",
# "ACT DIGITAL-A104 + A105", "2RPNET-Lounge Fintech".
PADRAO_NOME_ESTANDE = re.compile(
    r"^(.{3,}?)\s*[-–]\s*((?:[A-Z]{0,2}\d[\w +.]{0,18})|"
    r"(?:(?:Lounge|Sala|Estande|Stand|Rua|Hall|Espa[çc]o).{0,24}))$",
    re.IGNORECASE,
)


def _separar_nome_estande(nome: str) -> tuple[str, str]:
    achado = PADRAO_NOME_ESTANDE.match(nome)
    if not achado:
        return nome, ""
    return normalizar_texto(achado.group(1)), normalizar_texto(achado.group(2))


def _limpar_nome(texto: str) -> str:
    nome = normalizar_texto(texto)
    # cartões costumam repetir o nome duas vezes (logo alt + título)
    metade = len(nome) // 2
    if metade > 2 and nome[:metade].strip() == nome[metade:].strip():
        nome = nome[:metade].strip()
    return nome


def _plausivel(nome: str) -> bool:
    if not nome or not (2 < len(nome) <= 90):
        return False
    if RUIDO.match(nome):
        return False
    if not re.search(r"[A-Za-zÀ-ÿ一-鿿]", nome):
        return False
    return True


def _extrair_por_link(pagina, base: str) -> list[dict]:
    """Estratégia 1: os itens são links que compartilham o mesmo prefixo de caminho."""
    # Links dentro de nav/header/footer sao menu do site, nao expositores. Sem excluir,
    # a pagina da Web Summit devolvia "Programacao", "Palestrantes", "Imprensa" como
    # se fossem empresas — lixo que entraria no banco parecendo dado bom.
    dados = pagina.evaluate("""() =>
        [...document.querySelectorAll('a[href]')]
          .filter(a => !a.closest('nav, header, footer, [role=navigation], [class*=menu], [class*=nav], [class*=footer], [class*=header]'))
          .map(a => ({
            href: a.getAttribute('href') || '',
            texto: (a.innerText || a.textContent || '').trim(),
            alt: (a.querySelector('img') || {}).alt || '',
        }))
    """)

    prefixos = Counter()
    for item in dados:
        caminho = urlparse(item["href"]).path.rstrip("/")
        pai = caminho.rsplit("/", 1)[0]
        if pai and pai not in ("/", ""):
            prefixos[pai] += 1
    if not prefixos:
        return []

    prefixo, quantos = prefixos.most_common(1)[0]
    if quantos < MIN_ITENS:
        return []

    empresas, vistos = [], set()
    for item in dados:
        caminho = urlparse(item["href"]).path.rstrip("/")
        if caminho.rsplit("/", 1)[0] != prefixo:
            continue
        nome = _limpar_nome(item["texto"] or item["alt"])
        if not _plausivel(nome) or nome.lower() in vistos:
            continue
        vistos.add(nome.lower())
        empresas.append((nome, urljoin(base, item["href"])))
    return [{"nome": n, "link": u} for n, u in empresas]


def _extrair_por_cartao(pagina) -> list[dict]:
    """Estratégia 2: itens repetidos com a mesma classe (grid de logos/cards)."""
    dados = pagina.evaluate("""() => {
        const contagem = {};
        for (const el of document.querySelectorAll('div,li,article,section')) {
            const c = (el.className || '').toString().trim();
            if (!c || c.length > 120) continue;
            contagem[c] = (contagem[c] || 0) + 1;
        }
        const melhor = Object.entries(contagem)
            .filter(([, n]) => n >= 12)
            .sort((a, b) => b[1] - a[1])[0];
        if (!melhor) return [];
        return [...document.querySelectorAll('.' + CSS.escape(melhor[0]).replace(/\\\\ /g, '.'))]
            .map(el => ({
                texto: (el.innerText || '').trim().split('\\n')[0],
                alt: (el.querySelector('img') || {}).alt || '',
                href: (el.querySelector('a') || {}).href || '',
            }));
    }""")

    empresas, vistos = [], set()
    for item in dados or []:
        nome = _limpar_nome(item.get("texto") or item.get("alt") or "")
        if not _plausivel(nome) or nome.lower() in vistos:
            continue
        vistos.add(nome.lower())
        empresas.append({"nome": nome, "link": item.get("href") or ""})
    return empresas


def coletar(url_lista: str) -> dict:
    """Renderiza a página e extrai a lista de empresas."""
    from ...core.navegador import contexto

    with contexto() as ctx:
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto(url_lista, wait_until="domcontentloaded", timeout=90000)
        pagina.wait_for_timeout(5000)

        # rola até o fim: muitas listas carregam em lotes conforme se desce
        altura_anterior = 0
        for _ in range(MAX_ROLAGENS):
            pagina.mouse.wheel(0, 6000)
            pagina.wait_for_timeout(1200)
            altura = pagina.evaluate("() => document.body.scrollHeight")
            if altura == altura_anterior:
                break
            altura_anterior = altura

        # e clica em "carregar mais" enquanto existir
        for _ in range(MAX_ROLAGENS):
            clicou = False
            for texto in TEXTOS_CARREGAR_MAIS:
                try:
                    botao = pagina.get_by_text(texto, exact=False).first
                    if botao and botao.is_visible(timeout=800):
                        botao.click(timeout=2500)
                        pagina.wait_for_timeout(1800)
                        clicou = True
                        break
                except Exception:
                    continue
            if not clicou:
                break

        achados = _extrair_por_link(pagina, url_lista)
        if len(achados) < MIN_ITENS:
            achados = _extrair_por_cartao(pagina)

    if len(achados) < MIN_ITENS:
        raise FalhouDeVerdade(
            url_lista, f"pagina renderizada mas so achei {len(achados)} itens"
        )

    # só separa se a lista inteira usa o formato — senão um traço no nome viraria estande
    com_estande = sum(1 for i in achados if _separar_nome_estande(i["nome"])[1])
    separar = com_estande >= len(achados) * 0.6

    expositores = []
    for item in achados:
        nome, stand = _separar_nome_estande(item["nome"]) if separar else (item["nome"], "")
        expositores.append({
            "nome": nome,
            "website": "",
            "emails": [],
            "pais": "",
            "cidade": "",
            "endereco": "",
            "stand": stand,
            "categorias": [],
            "descricao": "",
            "ficha_feira": item.get("link", ""),
            "fonte_plataforma": "renderizado",
            "fonte_url": url_lista,
            "id_plataforma": "",
        })

    return {
        "evento": {"plataforma": "renderizado", "url_lista": url_lista},
        "expositores": expositores,
        "total_informado": len(expositores),
    }
