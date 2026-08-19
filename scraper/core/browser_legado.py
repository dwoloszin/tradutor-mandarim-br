"""Renderização de páginas com JavaScript usando Playwright (Chromium headless).

Usado apenas quando o conteúdo não vem completo no HTML estático (ex.: paginação
"Carregar Mais" do Modern Events Calendar, que busca mais eventos via JS).
"""
from playwright.sync_api import sync_playwright

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def renderizar(url: str, wait_selector: str | None = None, wait_ms: int = 3000) -> str:
    """Abre a URL num navegador headless e devolve o HTML já processado pelo
    JavaScript da página (sem clicar em nada) — útil pra sites React/Vue que
    montam o conteúdo via API depois do carregamento inicial."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=DEFAULT_UA)
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=wait_ms)
                except Exception:
                    pass
            else:
                page.wait_for_timeout(wait_ms)
            html = page.content()
        finally:
            browser.close()
    return html


def renderizar_com_carregar_mais(
    url: str,
    load_more_selector: str = ".mec-load-more-button",
    max_clicks: int = 40,
    wait_after_click_ms: int = 900,
) -> str:
    """Abre a URL num navegador headless e clica no botão "Carregar Mais"
    repetidamente até ele sumir/parar de trazer eventos novos, ou até max_clicks.
    Retorna o HTML final da página (com todos os eventos carregados)."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=DEFAULT_UA)
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            _fechar_aviso_cookies(page)

            def article_count() -> int:
                return page.eval_on_selector_all("article", "els => els.length")

            for _ in range(max_clicks):
                button = page.query_selector(load_more_selector)
                if button is None or not button.is_visible():
                    break

                count_before = article_count()
                try:
                    button.click(timeout=5000)
                except Exception:
                    break

                # espera o DOM crescer (até ~6s) em vez de um sleep fixo, pra não
                # confundir "ainda carregando" com "acabaram os eventos".
                cresceu = False
                for _ in range(20):
                    page.wait_for_timeout(wait_after_click_ms // 3)
                    if article_count() > count_before:
                        cresceu = True
                        break
                if not cresceu:
                    break  # botão clicado mas nada novo apareceu: fim da lista

            html = page.content()
        finally:
            browser.close()
    return html


def _fechar_aviso_cookies(page) -> None:
    for texto in ["Aceitar Todos", "Aceitar todos", "Accept all", "Aceitar"]:
        try:
            botao = page.get_by_text(texto, exact=False).first
            if botao and botao.is_visible(timeout=1000):
                botao.click(timeout=1000)
                return
        except Exception:
            continue
