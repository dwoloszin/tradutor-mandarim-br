"""Perfil de execução: a mesma base de código roda na nuvem (GitHub Actions) e no seu PC.

A diferença entre os dois não é o que o robô *quer* fazer, é o que ele *consegue*:
IPs de datacenter são bloqueados por Alibaba, Baidu, Cloudflare e vários sites de feira,
enquanto o seu IP residencial brasileiro passa.

Por isso cada fonte declara se exige IP residencial. Na nuvem, essas fontes não são
tentadas às cegas: a tarefa é marcada como "adiado_local" na fila e fica esperando a
próxima rodada no seu computador, que varre exatamente essa lista.
"""
from __future__ import annotations

import os
from enum import Enum


class Ambiente(str, Enum):
    NUVEM = "nuvem"
    LOCAL = "local"


def ambiente_atual() -> Ambiente:
    """Detecta onde estamos rodando. MJ_AMBIENTE força manualmente (útil para testar)."""
    forcado = os.environ.get("MJ_AMBIENTE", "").strip().lower()
    if forcado in ("nuvem", "local"):
        return Ambiente(forcado)
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        return Ambiente.NUVEM
    return Ambiente.LOCAL


def na_nuvem() -> bool:
    return ambiente_atual() is Ambiente.NUVEM


def pode_executar(requer_residencial: bool) -> bool:
    """True se a fonte pode ser tentada no ambiente atual."""
    return not (requer_residencial and na_nuvem())
