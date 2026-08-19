"""Acompanhamento de rodada em andamento.

O store só grava no fim da execução — por boa razão, senão cada tarefa reescreveria
arquivos inteiros. O efeito colateral é que, durante uma rodada de duas horas, não há
como saber se ela está avançando, travada ou perto do fim. Quem está esperando fica no
escuro, e no escuro a suspeita natural é que quebrou.

Este módulo resolve isso com um arquivo pequeno, reescrito a cada item processado:
etapa atual, quantos itens já foram, quanto falta e a previsão de término calculada
pelo ritmo real observado — não por estimativa fixa.

O arquivo é descartável e fica fora do Git. Se a rodada morrer, ele fica para trás com
a marca de tempo antiga, e é assim que sabemos que morreu.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .store import DATA_DIR

ARQUIVO = DATA_DIR / "_progresso.json"

# Considera a rodada morta se o arquivo não é tocado há mais que isso.
SILENCIO_SUSPEITO_S = 15 * 60


class Acompanhamento:
    """Registra o avanço de uma etapa. Usar como context manager."""

    def __init__(self, etapa: str, total: int, descricao: str = ""):
        self.etapa = etapa
        self.total = max(total, 0)
        self.descricao = descricao
        self.feitos = 0
        self.inicio = time.time()
        self.item_atual = ""

    def __enter__(self) -> "Acompanhamento":
        self._gravar()
        return self

    def passo(self, item: str = "", quantos: int = 1) -> None:
        self.feitos += quantos
        self.item_atual = item[:80]
        self._gravar()

    def __exit__(self, *_) -> None:
        self.item_atual = "concluída"
        self._gravar(final=True)

    # ----------------------------------------------------------------

    def _previsao(self) -> tuple[float, str]:
        """Segundos restantes e horário previsto, pelo ritmo observado até aqui."""
        decorrido = time.time() - self.inicio
        if self.feitos <= 0 or self.total <= 0:
            return 0.0, ""
        por_item = decorrido / self.feitos
        restantes = max(self.total - self.feitos, 0)
        faltam = por_item * restantes
        fim = datetime.now().astimezone() + timedelta(seconds=faltam)
        return faltam, fim.strftime("%H:%M")

    def _gravar(self, final: bool = False) -> None:
        faltam, previsto = self._previsao()
        dados = {
            "etapa": self.etapa,
            "descricao": self.descricao,
            "feitos": self.feitos,
            "total": self.total,
            "percentual": round(100 * self.feitos / self.total, 1) if self.total else 0,
            "item_atual": self.item_atual,
            "inicio": datetime.fromtimestamp(self.inicio, timezone.utc).isoformat(
                timespec="seconds"),
            "decorrido_s": round(time.time() - self.inicio),
            "faltam_s": round(faltam),
            "previsao_termino": previsto,
            "atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pid": os.getpid(),
            "encerrada": final,
        }
        try:
            ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
            temporario = ARQUIVO.with_suffix(".tmp")
            temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
            temporario.replace(ARQUIVO)   # troca atômica: leitor nunca vê meio arquivo
        except OSError:
            pass  # acompanhamento nunca pode derrubar a coleta


def ler() -> dict | None:
    if not ARQUIVO.exists():
        return None
    try:
        dados = json.loads(ARQUIVO.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    idade = time.time() - ARQUIVO.stat().st_mtime
    dados["silencio_s"] = round(idade)
    dados["parece_travada"] = (not dados.get("encerrada")) and idade > SILENCIO_SUSPEITO_S
    return dados


def formatar(dados: dict | None) -> str:
    """Uma linha legível para o terminal."""
    if not dados:
        return "nenhuma rodada registrada"
    if dados.get("encerrada"):
        return (f"{dados['etapa']}: concluída "
                f"({dados['feitos']}/{dados['total']} em {_duracao(dados['decorrido_s'])})")

    barra_cheia = int(dados["percentual"] / 5)
    barra = "█" * barra_cheia + "░" * (20 - barra_cheia)
    linha = (f"{dados['etapa']}  [{barra}] {dados['percentual']:5.1f}%  "
             f"{dados['feitos']}/{dados['total']}")
    if dados.get("previsao_termino"):
        linha += (f"  ·  faltam {_duracao(dados['faltam_s'])}, "
                  f"termina ~{dados['previsao_termino']}")
    if dados.get("item_atual"):
        linha += f"\n    agora: {dados['item_atual']}"
    if dados.get("parece_travada"):
        linha += (f"\n    ATENÇÃO: sem avanço há {_duracao(dados['silencio_s'])} — "
                  f"a rodada pode ter morrido")
    return linha


def _duracao(segundos: float) -> str:
    segundos = int(segundos)
    if segundos < 60:
        return f"{segundos}s"
    if segundos < 3600:
        return f"{segundos // 60}min"
    return f"{segundos // 3600}h{(segundos % 3600) // 60:02d}"
