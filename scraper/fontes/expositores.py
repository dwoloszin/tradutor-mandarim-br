"""Roteador de adaptadores de expositores.

Dado um evento, descobre qual plataforma serve o diretório e chama o adaptador certo.
A ordem importa: primeiro o que foi fixado à mão (você sabe mais que a heurística),
depois as plataformas que entregam dados estruturados (Swapcard, RX), e só então o
raspador genérico de HTML, que é o menos confiável.

Cada retorno traz o motivo do resultado, porque "0 expositores" pode significar coisas
muito diferentes: feira sem lista publicada ainda, plataforma nova, ou bloqueio de IP —
e só a última deve virar tarefa para a rodada local.
"""
from __future__ import annotations

from ..core.http import Bloqueado, FalhouDeVerdade, buscar
from ..core.modelos import normalizar_texto
from .plataformas import (
    astro, noomis, renderizado, rotulos, rx, smarter_e, swapcard, tradechina,
    planilha,
    wordpress,
)
from .plataformas.descoberta import descobrir
from .plataformas.detectar import detectar_plataforma

# resultados possíveis
OK = "ok"
SEM_LISTA = "sem_lista"
PLATAFORMA_NOVA = "plataforma_nova"
BLOQUEADO = "bloqueado"
ERRO = "erro"


def _resultado(status: str, **extra) -> dict:
    base = {
        "status": status,
        "plataforma": "",
        "pagina": "",
        "url_dados": "",
        "expositores": [],
        "total_informado": 0,
        "detalhe": "",
        # o Swapcard devolve as datas oficiais do evento; são melhores que a nossa
        # leitura do texto da agenda, e às vezes a única fonte de data que temos
        "datas_evento": {},
    }
    base.update(extra)
    return base


def _datas_do_swapcard(dados: dict) -> dict:
    evento = dados.get("evento") or {}
    inicio, fim = (evento.get("inicio") or "")[:10], (evento.get("fim") or "")[:10]
    if not inicio:
        return {}
    return {"data_inicio": inicio, "data_fim": fim or inicio,
            "titulo_oficial": evento.get("titulo", "")}


def _parse_generico(url: str) -> list[dict]:
    """Último recurso: procura uma listagem repetida de empresas no HTML.

    Só aceita se encontrar bastante item — uma lista de expositores de verdade tem
    dezenas. Poucos itens quase sempre são menu ou destaques, não o diretório.
    """
    from collections import Counter
    from urllib.parse import urljoin, urlparse

    try:
        from bs4 import BeautifulSoup
        sopa = BeautifulSoup(buscar(url, ttl_horas=24), "lxml")
    except (Bloqueado, FalhouDeVerdade):
        return []

    prefixos = Counter()
    for a in sopa.select("a[href]"):
        caminho = urlparse(a.get("href", "")).path.rstrip("/")
        pai = caminho.rsplit("/", 1)[0]
        if pai and pai != "/":
            prefixos[pai] += 1
    if not prefixos:
        return []

    prefixo, quantos = prefixos.most_common(1)[0]
    if quantos < 15:
        return []
    if not any(p in prefixo.lower() for p in ("expositor", "exhibitor", "empresa", "marca")):
        return []

    empresas, vistos = [], set()
    for a in sopa.select("a[href]"):
        caminho = urlparse(a.get("href", "")).path.rstrip("/")
        if caminho.rsplit("/", 1)[0] != prefixo:
            continue
        nome = normalizar_texto(a.get_text())
        # o HTML costuma repetir o nome duas vezes dentro do card
        metade = len(nome) // 2
        if metade and nome[:metade].strip() == nome[metade:].strip():
            nome = nome[:metade].strip()
        if not nome or len(nome) < 2 or nome in vistos:
            continue
        vistos.add(nome)
        empresas.append({
            "nome": nome,
            "website": "",
            "emails": [],
            "pais": "",
            "cidade": "",
            "endereco": "",
            "stand": "",
            "categorias": [],
            "descricao": "",
            "ficha_feira": urljoin(url, a.get("href", "")),
            "fonte_plataforma": "generico",
            "fonte_url": url,
            "id_plataforma": "",
        })
    return empresas


def _parse_cartoes_estatico(html: str, url: str) -> list[dict]:
    """Cartões repetidos no HTML já servido — sem precisar de navegador.

    Sites em Framer, Wix e afins montam a lista como blocos com a mesma classe gerada
    ("framer-1luz2do"). O nome da classe muda de site para site, então procuramos a
    classe MAIS repetida e tratamos cada ocorrência como um expositor.

    Isto rodava só no adaptador com navegador. Mas essas páginas vêm inteiras no HTML:
    gastar um Chrome nelas era desperdício, e na nuvem simplesmente não rodava.
    """
    from collections import Counter

    from bs4 import BeautifulSoup

    sopa = BeautifulSoup(html, "lxml")
    for tag in sopa(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    contagem = Counter()
    for elemento in sopa.select("div, li, article"):
        classe = " ".join(elemento.get("class") or [])
        if classe and len(classe) < 120:
            contagem[classe] += 1
    if not contagem:
        return []

    classe, repeticoes = contagem.most_common(1)[0]
    if repeticoes < 15:
        return []

    seletor = "." + ".".join(c for c in classe.split() if c)
    empresas, vistos = [], set()
    for bloco in sopa.select(seletor):
        textos = [normalizar_texto(x) for x in bloco.stripped_strings]
        textos = [x for x in textos if x]
        if not textos:
            continue
        nome = textos[0]
        # descarta chamada de marketing, texto de preenchimento e pergunta de seção
        if len(nome) > 70 or nome.endswith("?") or "lorem ipsum" in nome.lower():
            continue
        if len(nome) < 3 or nome.lower() in vistos:
            continue
        vistos.add(nome.lower())

        link = bloco.select_one("a[href]")
        empresas.append({
            "nome": nome,
            "website": "", "emails": [], "pais": "", "cidade": "", "endereco": "",
            "stand": "", "categorias": [], "descricao": "",
            "ficha_feira": link.get("href") if link else "",
            "fonte_plataforma": "cartoes", "fonte_url": url, "id_plataforma": "",
        })
    return empresas


def coletar(evento: dict, config_feira: dict | None = None) -> dict:
    """Coleta os expositores de um evento. Nunca levanta exceção: devolve status."""
    config_feira = config_feira or {}
    site = config_feira.get("site") or evento.get("site") or evento.get("pagina_local")
    if not site:
        return _resultado(SEM_LISTA, detalhe="evento sem site oficial conhecido")

    # Só a URL fixada à mão no config é autoridade. A que o evento carrega foi
    # DESCOBERTA por nós numa rodada anterior — tratá-la como entrada faz um palpite
    # ruim se perpetuar: a FEIPLAR ficou com a home gravada como "página de
    # expositores" e, a cada rodada, o roteador confiava nela e raspava 15 itens,
    # enquanto a lista real com 156 nunca era procurada de novo.
    url_fixada = config_feira.get("pagina_expositores")

    # 0) feiras da Meorient (China Homelife e irmas): a plataforma tem API propria,
    #    identificada pelo exhibition_id no config. Sao 100% expositores chineses.
    if config_feira.get("plataforma") == "tradechina" and config_feira.get("exhibition_id"):
        # O host da API responde "Disallow: /" para todo robô. Só coletamos se houver
        # autorização explícita e registrada no config — a decisão é do dono do
        # projeto, e fica visível, não enterrada no código.
        if not config_feira.get("permitir_apesar_do_robots"):
            return _resultado(
                SEM_LISTA, plataforma="tradechina",
                detalhe="robots.txt do host proíbe robôs; ative "
                        "'permitir_apesar_do_robots' no config para coletar mesmo assim",
            )
        try:
            dados = tradechina.coletar(config_feira["exhibition_id"])
            return _resultado(OK, plataforma="tradechina",
                              pagina="https://www.tradechina.com/search/supplier",
                              url_dados=config_feira["exhibition_id"],
                              expositores=dados["expositores"],
                              total_informado=dados["total_informado"])
        except Bloqueado as exc:
            return _resultado(BLOQUEADO, plataforma="tradechina", detalhe=exc.motivo)
        except FalhouDeVerdade as exc:
            return _resultado(ERRO, plataforma="tradechina", detalhe=exc.motivo)

    # 0b) The smarter E (Intersolar): endpoint proprio, exige navegador pelo csrfToken
    if config_feira.get("plataforma") == "smarter_e" and url_fixada:
        from ..core.perfil import pode_executar
        if not pode_executar(smarter_e.REQUER_RESIDENCIAL):
            return _resultado(BLOQUEADO, plataforma="smarter_e",
                              detalhe="exige navegador com JavaScript; roda no PC")
        try:
            dados = smarter_e.coletar(url_fixada)
            return _resultado(OK, plataforma="smarter_e", pagina=url_fixada,
                              url_dados=url_fixada, expositores=dados["expositores"],
                              total_informado=dados["total_informado"])
        except Bloqueado as exc:
            return _resultado(BLOQUEADO, plataforma="smarter_e", detalhe=exc.motivo)
        except FalhouDeVerdade as exc:
            return _resultado(ERRO, plataforma="smarter_e", detalhe=exc.motivo)

    # 0c) Noomis (plataforma da FEBRAVA/FEBRABAN): API publica com nome, pavilhao e
    #     estande em campos proprios — bem melhor que raspar a pagina.
    if config_feira.get("plataforma") == "noomis" and config_feira.get("slug_noomis"):
        try:
            dados = noomis.coletar(config_feira["slug_noomis"])
            return _resultado(OK, plataforma="noomis",
                              pagina=config_feira.get("pagina_expositores", ""),
                              url_dados=config_feira["slug_noomis"],
                              expositores=dados["expositores"],
                              total_informado=dados["total_informado"])
        except Bloqueado as exc:
            return _resultado(BLOQUEADO, plataforma="noomis", detalhe=exc.motivo)
        except FalhouDeVerdade as exc:
            return _resultado(ERRO, plataforma="noomis", detalhe=exc.motivo)

    # 1) plataforma já conhecida e apontada à mão
    if url_fixada and "/exhibitors/" in url_fixada and "event/" in url_fixada:
        try:
            dados = swapcard.coletar(url_fixada)
            return _resultado(OK, plataforma="swapcard", pagina=url_fixada,
                              url_dados=url_fixada, expositores=dados["expositores"],
                              total_informado=dados["total_informado"],
                              datas_evento=_datas_do_swapcard(dados))
        except Bloqueado as exc:
            return _resultado(BLOQUEADO, plataforma="swapcard", detalhe=exc.motivo)
        except FalhouDeVerdade as exc:
            return _resultado(ERRO, plataforma="swapcard", detalhe=exc.motivo)

    # 2) descoberta em camadas
    try:
        achado = descobrir(site, url_fixada)
    except Bloqueado as exc:
        return _resultado(BLOQUEADO, detalhe=exc.motivo)
    except Exception as exc:  # noqa: BLE001
        return _resultado(ERRO, detalhe=f"{type(exc).__name__}: {exc}")

    pagina = achado.get("pagina") or ""
    plataforma = achado.get("plataforma") or ""

    if plataforma == "swapcard" and achado.get("url_dados"):
        try:
            dados = swapcard.coletar(achado["url_dados"])
            return _resultado(OK, plataforma="swapcard", pagina=pagina,
                              url_dados=achado["url_dados"],
                              expositores=dados["expositores"],
                              total_informado=dados["total_informado"],
                              datas_evento=_datas_do_swapcard(dados))
        except Bloqueado as exc:
            return _resultado(BLOQUEADO, plataforma="swapcard", pagina=pagina, detalhe=exc.motivo)
        except FalhouDeVerdade as exc:
            return _resultado(ERRO, plataforma="swapcard", pagina=pagina, detalhe=exc.motivo)

    if not pagina:
        return _resultado(SEM_LISTA, detalhe=achado.get("erro") or "lista não localizada")

    # 3) RX (Reed) — identificada pelo componente exhibitor-directory
    try:
        html = buscar(pagina, ttl_horas=24)
    except Bloqueado as exc:
        return _resultado(BLOQUEADO, pagina=pagina, detalhe=exc.motivo)
    except FalhouDeVerdade as exc:
        return _resultado(ERRO, pagina=pagina, detalhe=exc.motivo)

    if detectar_plataforma(html) == "rx" or "reactSettings" in html:
        try:
            dados = rx.coletar(pagina)
            if dados["expositores"]:
                return _resultado(OK, plataforma="rx", pagina=pagina, url_dados=pagina,
                                  expositores=dados["expositores"],
                                  total_informado=dados["total_informado"])
        except Bloqueado as exc:
            return _resultado(BLOQUEADO, plataforma="rx", pagina=pagina, detalhe=exc.motivo)
        except FalhouDeVerdade as exc:
            plataforma = "rx"  # segue para o genérico, mas registra o que era

    # 2b) Astro com dados embutidos (e API paginada atras): pega a lista INTEIRA,
    #     nao so a primeira pagina que o botao "ver mais" revela.
    try:
        dados = astro.coletar(pagina, html)
        return _resultado(OK, plataforma="astro", pagina=pagina, url_dados=pagina,
                          expositores=dados["expositores"],
                          total_informado=dados["total_informado"])
    except FalhouDeVerdade:
        pass

    # 2c) WordPress headless: o CMS fica noutro dominio e os endpoints padrao estao
    #     fechados, mas a pagina publica revela o endpoint proprio.
    #
    #     Vem antes de qualquer adaptador que leia o HTML servido, e nao por gosto:
    #     na Beauty Fair o leitor de rotulos casava com os 14 itens da primeira pagina
    #     e devolvia com sucesso, encerrando o roteamento. A lista tem 391 em 27
    #     paginas. Um adaptador que le a pagina renderizada nunca vai ver alem da
    #     primeira; quem tem o endpoint paginado tem que decidir antes dele.
    if "custom/v1/node" in html:
        try:
            dados = wordpress.coletar_node(pagina, html)
            if dados["expositores"]:
                return _resultado(OK, plataforma="wordpress_node", pagina=pagina,
                                  url_dados=pagina, expositores=dados["expositores"],
                                  total_informado=dados["total_informado"])
        except Bloqueado as exc:
            return _resultado(BLOQUEADO, plataforma="wordpress_node", pagina=pagina,
                              detalhe=exc.motivo)
        except FalhouDeVerdade:
            pass

    # 2d) Planilha colada na pagina (data-sheets-root): sinal exato, entao decide cedo.
    #     Na FESQUA o raspador generico e o navegador viam 24 das 314 empresas, porque
    #     tabela crua sem classe nem link nao se parece com lista de expositores.
    if "data-sheets-root" in html or "data-sheets-baot" in html:
        try:
            dados = planilha.coletar(pagina, html)
            return _resultado(OK, plataforma="planilha", pagina=pagina, url_dados=pagina,
                              expositores=dados["expositores"],
                              total_informado=dados["total_informado"])
        except FalhouDeVerdade:
            pass

    # 3a) Lista rotulada ("Empresa: X / Estande: Y"), comum em site Elementor.
    #     Os rotulos dizem qual campo e qual — nao precisamos adivinhar como no generico.
    try:
        dados = rotulos.coletar(pagina, html)
        return _resultado(OK, plataforma="rotulos", pagina=pagina, url_dados=pagina,
                          expositores=dados["expositores"],
                          total_informado=dados["total_informado"])
    except FalhouDeVerdade:
        pass  # não é uma lista rotulada; segue o fluxo

    # 3b) WordPress REST: muitas feiras medias publicam a lista num tipo de conteudo
    #     proprio e nem sabem. Vem limpo e paginado, e ganha do raspador.
    if "wp-json" in html or "/wp-content/" in html:
        try:
            dados = wordpress.coletar(site)
            if dados["expositores"]:
                return _resultado(OK, plataforma="wordpress", pagina=pagina,
                                  url_dados=site, expositores=dados["expositores"],
                                  total_informado=dados["total_informado"])
        except Bloqueado as exc:
            return _resultado(BLOQUEADO, plataforma="wordpress", pagina=pagina,
                              detalhe=exc.motivo)
        except FalhouDeVerdade:
            pass  # sem tipo de conteúdo de expositor: segue para o genérico

    # 4) genérico sobre o HTML servido
    empresas = _parse_generico(pagina)
    if empresas:
        return _resultado(OK, plataforma=plataforma or "generico", pagina=pagina,
                          url_dados=pagina, expositores=empresas,
                          total_informado=len(empresas))

    # 4b) cartões repetidos no HTML servido (Framer, Wix): não precisa de navegador
    empresas = _parse_cartoes_estatico(html, pagina)
    if empresas:
        return _resultado(OK, plataforma="cartoes", pagina=pagina, url_dados=pagina,
                          expositores=empresas, total_informado=len(empresas))

    # 5) último recurso: renderizar com navegador. A maioria das listas que sobram é
    #    React/Webflow e só existe depois do JavaScript. Exige o PC (não roda na nuvem),
    #    então na nuvem devolvemos "bloqueado" para a tarefa esperar a rodada local.
    from ..core.perfil import pode_executar
    if not pode_executar(renderizado.REQUER_RESIDENCIAL):
        return _resultado(BLOQUEADO, plataforma=plataforma, pagina=pagina,
                          detalhe="lista só existe com JavaScript; roda no PC")
    try:
        dados = renderizado.coletar(pagina)
        return _resultado(OK, plataforma="renderizado", pagina=pagina, url_dados=pagina,
                          expositores=dados["expositores"],
                          total_informado=dados["total_informado"])
    except Bloqueado as exc:
        return _resultado(BLOQUEADO, plataforma=plataforma, pagina=pagina, detalhe=exc.motivo)
    except FalhouDeVerdade as exc:
        return _resultado(PLATAFORMA_NOVA, plataforma=plataforma, pagina=pagina,
                          detalhe=exc.motivo)
    except Exception as exc:  # noqa: BLE001 - navegador falha de muitas formas
        return _resultado(ERRO, plataforma=plataforma, pagina=pagina,
                          detalhe=f"{type(exc).__name__}: {exc}")

    return _resultado(PLATAFORMA_NOVA, plataforma=plataforma, pagina=pagina,
                      detalhe="lista existe mas nenhum adaptador soube ler")
