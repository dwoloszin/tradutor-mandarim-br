"""Scraper de vagas de tradutor/intérprete de mandarim em portais de emprego do Brasil.

Cobrimos os portais que respondem com HTML server-side (sem precisar de navegador).
LinkedIn, Indeed e Glassdoor bloqueiam scraping automatizado (anti-bot / termos de uso),
então para esses geramos apenas o link de busca pronto, para o usuário abrir manualmente.
"""
from urllib.parse import quote_plus

from .utils import get_soup, normalize_text, save_json

TERMOS_BUSCA = ["tradutor mandarim", "intérprete mandarim", "tradutor chinês"]

# Portais com busca lida diretamente (scraping best-effort).
FONTES_SCRAPING = {
    "InfoJobs": "https://www.infojobs.com.br/empregos.aspx?palabra={q}",
    "Catho": "https://www.catho.com.br/vagas/{slug}/",
}

# Portais que bloqueiam scraping automatizado: geramos apenas o link de busca pronto.
FONTES_LINK_APENAS = {
    "LinkedIn": "https://www.linkedin.com/jobs/search/?keywords={q}&location=Brasil",
    "Indeed": "https://br.indeed.com/jobs?q={q}&l=Brasil",
    "Glassdoor": "https://www.glassdoor.com.br/Vaga/brasil-{slug}-vagas-SRCH_IL.0,6_IN37_KO7,{end}.htm",
    "Vagas.com": "https://www.vagas.com.br/vagas-de-{slug}",
    "Gupy": "https://portal.gupy.io/job-search/term={q}",
    "Catho (busca)": "https://www.catho.com.br/vagas/{slug}/",
}


def _slug(term: str) -> str:
    return term.strip().lower().replace(" ", "-")


def scrape_infojobs(termo: str) -> list[dict]:
    url = FONTES_SCRAPING["InfoJobs"].format(q=quote_plus(termo))
    soup = get_soup(url)
    if soup is None:
        return []

    vagas = []
    for card in soup.select(".js_rowCard"):
        title_el = card.select_one(".js_vacancyTitle")
        if not title_el:
            continue
        titulo = normalize_text(title_el.get_text())
        link_el = card.select_one("a[href*='.aspx']")
        link = link_el.get("href") if link_el else None
        if link and link.startswith("/"):
            link = "https://www.infojobs.com.br" + link

        empresa_el = card.select_one("a[href*='empresa-']")
        empresa = normalize_text(empresa_el.get_text()) if empresa_el else ""

        local_el = card.select_one(".mb-8")
        local = normalize_text(local_el.get_text()) if local_el else ""

        vagas.append({
            "titulo": titulo,
            "empresa": empresa,
            "local": local,
            "link": link,
            "fonte": "InfoJobs",
            "termo_busca": termo,
        })
    return vagas


def scrape_catho(termo: str) -> list[dict]:
    url = FONTES_SCRAPING["Catho"].format(slug=_slug(termo))
    soup = get_soup(url)
    if soup is None:
        return []

    vagas = []
    for card in soup.select("article.offer"):
        title_el = card.select_one(".title_offer a")
        if not title_el:
            continue
        titulo = normalize_text(title_el.get_text())
        link = title_el.get("href")
        if link and link.startswith("/"):
            link = "https://www.catho.com.br" + link

        empresa_el = card.select_one(".text-12")
        empresa = normalize_text(empresa_el.get_text()) if empresa_el else ""

        local_el = card.select_one(".i_job_location")
        local = ""
        if local_el and local_el.parent:
            local = normalize_text(local_el.parent.get_text())

        vagas.append({
            "titulo": titulo,
            "empresa": empresa,
            "local": local,
            "link": link,
            "fonte": "Catho",
            "termo_busca": termo,
        })
    return vagas


def build_search_links(termo: str) -> dict:
    q = quote_plus(termo)
    slug = _slug(termo)
    links = {}
    for nome, template in FONTES_LINK_APENAS.items():
        links[nome] = template.format(q=q, slug=slug, end="")
    return links


def scrape_empregos() -> dict:
    vagas = []
    for termo in TERMOS_BUSCA:
        vagas.extend(scrape_infojobs(termo))
        vagas.extend(scrape_catho(termo))

    # remove duplicadas pelo link
    vistos = set()
    unicas = []
    for v in vagas:
        chave = v.get("link") or v.get("titulo")
        if chave in vistos:
            continue
        vistos.add(chave)
        unicas.append(v)

    return {
        "vagas": unicas,
        "links_de_busca_manual": {termo: build_search_links(termo) for termo in TERMOS_BUSCA},
    }


if __name__ == "__main__":
    resultado = scrape_empregos()
    print(f"{len(resultado['vagas'])} vagas encontradas via scraping")
    save_json("empregos.json", resultado)
