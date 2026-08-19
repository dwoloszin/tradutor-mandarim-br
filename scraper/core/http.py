"""Cliente HTTP compartilhado: throttle por domínio, cache em disco e — o ponto central —
distinção entre "a página mudou" e "fui bloqueado".

Essa distinção é o que faz o modelo híbrido (nuvem de madrugada + PC durante o dia)
funcionar: quando a nuvem toma 403/CAPTCHA/Cloudflare, a tarefa NÃO é marcada como
fracassada nem como "sem dados" — ela vira "adiado_local" e espera a rodada no seu PC.
Se marcássemos como fracasso, perderíamos a empresa para sempre.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

RAIZ = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = RAIZ / "data" / "_cache_http"

TIMEOUT_PADRAO = 20
INTERVALO_MINIMO_POR_DOMINIO = 1.5  # segundos; educado com os sites-alvo

CABECALHOS_PADRAO = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8,zh-CN;q=0.7,zh;q=0.6",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Sinais no corpo da resposta de que levamos bloqueio, e não uma página de verdade.
SINAIS_BLOQUEIO = re.compile(
    r"(cf-browser-verification|checking your browser|cf_chl_opt|"
    r"captcha|recaptcha|hcaptcha|geetest|"
    r"access denied|acesso negado|forbidden|blocked|"
    r"unusual traffic|robot check|are you a human)",
    re.IGNORECASE,
)

# Mesma ideia, em chinês (Alibaba/1688/Baidu servem o desafio no idioma local).
SINAIS_BLOQUEIO_CJK = ("滑动验证", "验证码", "系统繁忙")

STATUS_BLOQUEIO = {401, 403, 405, 407, 409, 418, 429, 503}


class Bloqueado(Exception):
    """O alvo nos barrou (403, CAPTCHA, Cloudflare, rate limit). Vale tentar de outro IP."""

    def __init__(self, url: str, motivo: str):
        super().__init__(f"bloqueado em {url}: {motivo}")
        self.url = url
        self.motivo = motivo


class FalhouDeVerdade(Exception):
    """Erro que não adianta repetir de outro IP: 404, DNS inexistente, HTML sem o dado."""

    def __init__(self, url: str, motivo: str):
        super().__init__(f"falhou em {url}: {motivo}")
        self.url = url
        self.motivo = motivo


_ultimo_acesso: dict[str, float] = {}
_sessao: requests.Session | None = None


def _sessao_global() -> requests.Session:
    global _sessao
    if _sessao is None:
        _sessao = requests.Session()
        _sessao.headers.update(CABECALHOS_PADRAO)
    return _sessao


def dominio_de(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except ValueError:
        return ""


def _aguardar_vez(dominio: str) -> None:
    """Nunca bate duas vezes seguidas no mesmo domínio sem respirar."""
    agora = time.monotonic()
    ultimo = _ultimo_acesso.get(dominio)
    if ultimo is not None:
        espera = INTERVALO_MINIMO_POR_DOMINIO - (agora - ultimo)
        if espera > 0:
            time.sleep(espera + random.uniform(0, 0.4))
    _ultimo_acesso[dominio] = time.monotonic()


def _caminho_cache(url: str, sufixo: str) -> Path:
    chave = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return CACHE_DIR / f"{chave}{sufixo}"


def _ler_cache(url: str, ttl_horas: float, sufixo: str) -> str | None:
    if ttl_horas <= 0:
        return None
    caminho = _caminho_cache(url, sufixo)
    if not caminho.exists():
        return None
    idade_h = (time.time() - caminho.stat().st_mtime) / 3600
    if idade_h > ttl_horas:
        return None
    try:
        return caminho.read_text(encoding="utf-8")
    except OSError:
        return None


def _gravar_cache(url: str, conteudo: str, sufixo: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _caminho_cache(url, sufixo).write_text(conteudo, encoding="utf-8")
    except OSError:
        pass  # cache é otimização, nunca motivo para quebrar a coleta


def _corrigir_codificacao(resp: requests.Response) -> None:
    """Descobre a codificação real quando o servidor não a declara.

    Sem cabeçalho charset, o requests assume ISO-8859-1 e o texto vem corrompido:
    "Notícias" vira "NotÃ­cias" e nomes chineses viram lixo. O erro é silencioso —
    entra no banco parecendo dado bom. Aqui olhamos a declaração dentro do HTML e,
    na falta dela, deixamos o requests farejar pelo conteúdo.
    """
    tipo = (resp.headers.get("Content-Type") or "").lower()
    if "charset=" in tipo:
        return

    inicio = resp.content[:2048]
    achado = re.search(
        rb"""<meta[^>]+charset=["']?\s*([\w-]+)""", inicio, re.IGNORECASE
    )
    if achado:
        try:
            resp.encoding = achado.group(1).decode("ascii")
            return
        except (UnicodeDecodeError, LookupError):
            pass
    if resp.apparent_encoding:
        resp.encoding = resp.apparent_encoding


def _parece_desafio(texto: str) -> bool:
    if len(texto) >= 8000:
        return False
    if SINAIS_BLOQUEIO.search(texto):
        return True
    return any(sinal in texto for sinal in SINAIS_BLOQUEIO_CJK)


def buscar(
    url: str,
    *,
    ttl_horas: float = 12,
    tentativas: int = 2,
    timeout: int = TIMEOUT_PADRAO,
    referer: str | None = None,
    **kwargs,
) -> str:
    """Baixa uma URL e devolve o texto. Levanta Bloqueado ou FalhouDeVerdade.

    O cache em disco evita rebaixar a mesma página em rodadas próximas — importante
    porque o pipeline visita a mesma feira várias vezes ao explorar expositores.
    """
    em_cache = _ler_cache(url, ttl_horas, ".html")
    if em_cache is not None:
        return em_cache

    cabecalhos = dict(kwargs.pop("headers", {}) or {})
    if referer:
        cabecalhos["Referer"] = referer

    ultimo_erro: Exception | None = None
    for tentativa in range(tentativas):
        _aguardar_vez(dominio_de(url))
        try:
            resp = _sessao_global().get(
                url, timeout=timeout, headers=cabecalhos, allow_redirects=True, **kwargs
            )
        except requests.exceptions.SSLError as exc:
            raise FalhouDeVerdade(url, f"erro de SSL: {exc}") from exc
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            ultimo_erro = exc
            time.sleep(1.5 * (tentativa + 1))
            continue
        except requests.RequestException as exc:
            raise FalhouDeVerdade(url, str(exc)) from exc

        if resp.status_code in STATUS_BLOQUEIO:
            raise Bloqueado(url, f"HTTP {resp.status_code}")
        if resp.status_code == 404 or resp.status_code == 410:
            raise FalhouDeVerdade(url, f"HTTP {resp.status_code}")
        if resp.status_code >= 500:
            ultimo_erro = FalhouDeVerdade(url, f"HTTP {resp.status_code}")
            time.sleep(2 * (tentativa + 1))
            continue

        _corrigir_codificacao(resp)
        texto = resp.text
        # Página curta com cara de desafio anti-bot: 200 no status, bloqueio na prática.
        if _parece_desafio(texto):
            raise Bloqueado(url, "página de desafio anti-bot (CAPTCHA/Cloudflare)")

        _gravar_cache(url, texto, ".html")
        return texto

    raise FalhouDeVerdade(url, f"sem resposta apos {tentativas} tentativas: {ultimo_erro}")


def buscar_sopa(url: str, **kwargs) -> BeautifulSoup:
    return BeautifulSoup(buscar(url, **kwargs), "lxml")


def buscar_json(url: str, **kwargs):
    """Igual a buscar(), mas exige JSON de volta — várias plataformas de expositores
    (RX, MapYourShow, Swapcard) servem a lista por API mesmo quando a página é React."""
    cabecalhos = {"Accept": "application/json, text/plain, */*"}
    cabecalhos.update(kwargs.pop("headers", {}) or {})
    texto = buscar(url, headers=cabecalhos, **kwargs)
    try:
        return json.loads(texto)
    except json.JSONDecodeError as exc:
        raise FalhouDeVerdade(url, f"resposta nao e JSON valido: {exc}") from exc


def limpar_cache(mais_velho_que_horas: float = 0) -> int:
    """Apaga o cache HTTP. Retorna quantos arquivos foram removidos."""
    if not CACHE_DIR.exists():
        return 0
    removidos = 0
    limite = time.time() - mais_velho_que_horas * 3600
    for arquivo in CACHE_DIR.iterdir():
        if mais_velho_que_horas <= 0 or arquivo.stat().st_mtime < limite:
            arquivo.unlink()
            removidos += 1
    return removidos
