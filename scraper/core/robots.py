"""Verificação de robots.txt — a promessa do projeto, cumprida pelo código.

O README diz que respeitamos robots.txt. Até aqui isso dependia de eu lembrar de
checar antes de escrever cada adaptador, o que não é garantia nenhuma: basta um
descuido para o projeto passar a fazer o que promete não fazer. Agora a checagem
acontece dentro do cliente HTTP, em toda requisição.

O caso concreto que motivou: o LinkedIn responde `Disallow: /` para robôs genéricos.
Sem esta camada, bastaria alguém apontar um adaptador para lá e o projeto estaria
violando os termos sem ninguém perceber.

Quando o robots.txt não existe ou não pode ser lido, liberamos — é a interpretação
padrão e a mesma que os buscadores usam.
"""
from __future__ import annotations

import threading
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

# Identificamo-nos como navegador nas requisições de conteúdo, mas para o robots
# o que vale é a regra do agente genérico: é sob ela que devemos nos enquadrar.
AGENTE = "*"

TTL_SEGUNDOS = 24 * 3600

_cache: dict[str, tuple[RobotFileParser | None, float]] = {}
_trava = threading.Lock()


class ProibidoPorRobots(Exception):
    """O robots.txt do site proíbe esta URL para robôs genéricos."""

    def __init__(self, url: str):
        super().__init__(f"robots.txt proíbe: {url}")
        self.url = url


def _carregar(host_base: str) -> RobotFileParser | None:
    try:
        resposta = requests.get(
            f"{host_base}/robots.txt",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; mandarimJob/1.0)"},
        )
    except requests.RequestException:
        return None

    if resposta.status_code != 200 or not resposta.text.strip():
        return None

    leitor = RobotFileParser()
    leitor.parse(resposta.text.splitlines())
    return leitor


def _leitor_para(url: str) -> RobotFileParser | None:
    partes = urlparse(url)
    if not partes.scheme or not partes.netloc:
        return None
    base = f"{partes.scheme}://{partes.netloc}"

    with _trava:
        guardado = _cache.get(base)
        if guardado and time.time() - guardado[1] < TTL_SEGUNDOS:
            return guardado[0]

    leitor = _carregar(base)
    with _trava:
        _cache[base] = (leitor, time.time())
    return leitor


def pode_buscar(url: str) -> bool:
    """True se o robots.txt permite (ou não se pronuncia sobre) esta URL."""
    leitor = _leitor_para(url)
    if leitor is None:
        return True  # sem robots.txt legível, o padrão é permitir
    try:
        return leitor.can_fetch(AGENTE, url)
    except Exception:  # noqa: BLE001 - robots malformado não deve travar a coleta
        return True


def exigir_permissao(url: str) -> None:
    if not pode_buscar(url):
        raise ProibidoPorRobots(url)


def motivo(url: str) -> str:
    """Texto explicativo para diagnóstico."""
    return "permitido" if pode_buscar(url) else "proibido pelo robots.txt do site"
