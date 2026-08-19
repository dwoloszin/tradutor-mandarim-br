"""Navegador com sessão persistente, para as fontes que exigem verificação humana ou login.

Motivação: tradechina.com (plataforma da Meorient, onde ficam a China Homelife Brazil e a
China Machinery Fair — as feiras com maior densidade de expositores chineses do país) está
atrás do CAPTCHA do Tencent EdgeOne, e a ficha de cada expositor exige conta.

Como resolvemos, sem burlar nada:

  1. Você roda `python -m scraper.cli login` uma vez. Abre um Chrome de verdade, visível.
  2. Você resolve o CAPTCHA e entra com a SUA conta (o login do Google que você já usa).
  3. A sessão fica salva num perfil de navegador dedicado, em data/_sessao_navegador/.
  4. As rodadas seguintes reutilizam essa sessão e leem as mesmas páginas que você veria.

Regras que este módulo garante:
  - o perfil NUNCA vai para o Git (está no .gitignore) e NUNCA roda no GitHub Actions;
  - usamos um perfil separado, não o seu Chrome pessoal, para não tocar nos seus dados;
  - nenhuma tentativa de resolver CAPTCHA automaticamente: se aparecer desafio numa
    rodada automática, a tarefa volta para a fila pedindo sua intervenção.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

from .http import RAIZ
from .perfil import na_nuvem

PERFIL_DIR = RAIZ / "data" / "_sessao_navegador"

UA_PADRAO = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Textos que indicam "não sou eu quem decide, precisa de humano".
SINAIS_DESAFIO = (
    "security verification", "verifying the safety", "tencent cloud edgeone",
    "verificação de segurança", "please check the box", "unusual traffic",
    "滑动验证", "安全验证",
)
SINAIS_LOGIN = (
    "sign in", "log in", "entrar com google", "sign in with google",
    "please log in", "faça login", "登录",
)


class PrecisaIntervencao(Exception):
    """Apareceu CAPTCHA ou pedido de login numa rodada automática.

    Não é falha: a tarefa fica esperando você rodar `scraper.cli login`.
    """

    def __init__(self, url: str, tipo: str):
        super().__init__(f"{tipo} em {url} — precisa da sua sessão do navegador")
        self.url = url
        self.tipo = tipo


def sessao_existe() -> bool:
    return PERFIL_DIR.exists() and any(PERFIL_DIR.iterdir())


def _classificar_pagina(titulo: str, texto: str) -> str | None:
    misturado = f"{titulo} {texto[:3000]}".lower()
    if any(s in misturado for s in SINAIS_DESAFIO):
        return "captcha"
    if any(s in misturado for s in SINAIS_LOGIN) and len(texto) < 2500:
        return "login"
    return None


@contextlib.contextmanager
def contexto(visivel: bool = False, usar_chrome: bool = True):
    """Abre o contexto persistente. Levanta RuntimeError se chamado na nuvem."""
    if na_nuvem():
        raise RuntimeError(
            "navegador com sessão só roda localmente — na nuvem a tarefa deve ser adiada"
        )

    from playwright.sync_api import sync_playwright

    PERFIL_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        argumentos = {
            "user_data_dir": str(PERFIL_DIR),
            "headless": not visivel,
            "user_agent": UA_PADRAO,
            "locale": "pt-BR",
            "viewport": {"width": 1440, "height": 900},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if usar_chrome:
            argumentos["channel"] = "chrome"  # Chrome real passa em checagens que o Chromium não passa
        ctx = p.chromium.launch_persistent_context(**argumentos)
        try:
            yield ctx
        finally:
            ctx.close()


def abrir_para_login(url: str) -> None:
    """Abre o navegador visível e espera você resolver CAPTCHA/login."""
    print(f"\nAbrindo {url} num Chrome visível.")
    print("Resolva o CAPTCHA e faça login com a sua conta.")
    print("Quando a página estiver aberta e logada, volte aqui e tecle ENTER.\n")
    with contexto(visivel=True) as ctx:
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto(url, wait_until="domcontentloaded", timeout=90000)
        input("   ... tecle ENTER depois de logar: ")
        print("Sessão salva em", PERFIL_DIR)


def ler_pagina(
    url: str,
    *,
    esperar_seletor: str | None = None,
    esperar_ms: int = 4000,
    rolar: int = 0,
) -> str:
    """Abre a URL com a sessão salva e devolve o HTML já renderizado.

    Levanta PrecisaIntervencao se cair em CAPTCHA ou tela de login.
    """
    with contexto(visivel=False) as ctx:
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto(url, wait_until="domcontentloaded", timeout=60000)
        if esperar_seletor:
            with contextlib.suppress(Exception):
                pagina.wait_for_selector(esperar_seletor, timeout=esperar_ms)
        else:
            pagina.wait_for_timeout(esperar_ms)

        for _ in range(rolar):
            pagina.mouse.wheel(0, 5000)
            pagina.wait_for_timeout(1200)

        titulo = pagina.title()
        try:
            texto = pagina.inner_text("body")
        except Exception:
            texto = ""

        problema = _classificar_pagina(titulo, texto)
        if problema:
            raise PrecisaIntervencao(url, problema)

        return pagina.content()
