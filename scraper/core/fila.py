"""Fila de tarefas com estado — o mecanismo que faz nuvem e PC se completarem.

O fluxo que você desenhou, implementado:

  madrugada, GitHub Actions   -> processa tudo que funciona de IP de datacenter.
                                 O que der 403/CAPTCHA vira "adiado_local" (não é erro!).
  durante o dia, no seu PC    -> `python -m scraper.cli pendentes` pega exatamente
                                 os "adiado_local" e os fracassos com backoff vencido,
                                 e completa os buracos usando seu IP residencial.

Estados possíveis:
  pendente      nunca tentado
  ok            concluído; só será refeito quando o TTL vencer
  sem_dados     tentamos, a fonte realmente não tem esse dado (não insistir cedo)
  adiado_local  bloqueado na nuvem; aguarda rodada local
  falhou        erro transitório; volta com backoff exponencial
  desistido     falhou vezes demais; só volta se você mandar
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from .perfil import Ambiente, ambiente_atual
from .store import Tabela, agora_iso

PENDENTE = "pendente"
OK = "ok"
SEM_DADOS = "sem_dados"
ADIADO_LOCAL = "adiado_local"
FALHOU = "falhou"
DESISTIDO = "desistido"

MAX_TENTATIVAS = 5

# Quanto tempo até valer a pena refazer uma tarefa já concluída.
TTL_PADRAO_HORAS = {
    "agenda_local": 24,        # agenda de eventos muda todo dia
    "expositores": 72,         # lista de expositores cresce até a véspera da feira
    "enriquecer_empresa": 24 * 30,   # dados cadastrais mudam devagar
    "site_empresa": 24 * 30,
    "vagas": 24,
}


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def id_tarefa(tipo: str, alvo: str) -> str:
    """Id estável: mesma tarefa nunca duplica entre rodadas."""
    resumo = hashlib.sha1(f"{tipo}|{alvo}".encode("utf-8")).hexdigest()[:16]
    return f"{tipo}:{resumo}"


class Fila:
    def __init__(self):
        self.tabela = Tabela("fila").carregar()

    # ---------- registro ----------

    def adicionar(self, tipo: str, alvo: str, *, dados: dict | None = None,
                  prioridade: int = 5) -> dict:
        """Cadastra a tarefa se ela ainda não existe. Não mexe no estado se já existe."""
        identificador = id_tarefa(tipo, alvo)
        existente = self.tabela.obter(identificador)
        if existente is not None:
            if dados:
                existente.setdefault("dados", {}).update(dados)
            existente["prioridade"] = min(existente.get("prioridade", 5), prioridade)
            return existente

        return self.tabela.upsert({
            "id": identificador,
            "tipo": tipo,
            "alvo": alvo,
            "estado": PENDENTE,
            "prioridade": prioridade,
            "tentativas": 0,
            "dados": dados or {},
            "ultima_tentativa": None,
            "ultimo_ambiente": None,
            "erro": None,
        })

    # ---------- resultado ----------

    def marcar_ok(self, tipo: str, alvo: str, *, resumo: str = "") -> None:
        self._atualizar(tipo, alvo, estado=OK, erro=None, resumo=resumo, zerar_tentativas=True)

    def marcar_sem_dados(self, tipo: str, alvo: str, *, motivo: str = "",
                         voltar_em_horas: float | None = None) -> None:
        """A fonte não tinha o dado agora. Quem chama pode dizer quando vale reperguntar.

        Feira que ainda não publicou a lista de expositores é o caso típico: se ela
        acontece daqui a 10 dias, esperar o prazo padrão de 14 significa nunca mais
        olhar antes do evento — e perder a feira inteira.
        """
        self._atualizar(tipo, alvo, estado=SEM_DADOS, erro=motivo, zerar_tentativas=True)
        if voltar_em_horas is not None:
            registro = self._registro(tipo, alvo)
            registro["proxima_apos"] = (
                _agora() + timedelta(hours=voltar_em_horas)
            ).isoformat(timespec="seconds")

    def marcar_adiado_local(self, tipo: str, alvo: str, *, motivo: str = "") -> None:
        """Bloqueado. Na nuvem, isso não é fracasso: a tarefa fica intacta esperando
        a rodada no PC, sem gastar tentativa.

        Mas se já estamos no PC e ainda assim fomos bloqueados, não há para onde
        adiar — o alvo bloqueia todo mundo. Aí vira falha com backoff, senão a
        tarefa voltaria em toda rodada local, para sempre, sem nunca desistir.
        """
        if ambiente_atual() is Ambiente.LOCAL:
            self.marcar_falha(tipo, alvo, motivo=f"bloqueado também no IP local: {motivo}")
            return
        self._atualizar(tipo, alvo, estado=ADIADO_LOCAL, erro=motivo,
                        contar_tentativa=False)

    def marcar_falha(self, tipo: str, alvo: str, *, motivo: str = "") -> None:
        registro = self._registro(tipo, alvo)
        tentativas = registro.get("tentativas", 0) + 1
        estado = DESISTIDO if tentativas >= MAX_TENTATIVAS else FALHOU
        # backoff exponencial: 1h, 2h, 4h, 8h...
        proxima = _agora() + timedelta(hours=2 ** min(tentativas - 1, 6))
        registro.update({
            "estado": estado,
            "erro": motivo,
            "tentativas": tentativas,
            "ultima_tentativa": agora_iso(),
            "ultimo_ambiente": ambiente_atual().value,
            "proxima_apos": proxima.isoformat(timespec="seconds"),
            "atualizado_em": agora_iso(),
        })

    def _registro(self, tipo: str, alvo: str) -> dict:
        registro = self.tabela.obter(id_tarefa(tipo, alvo))
        if registro is None:
            registro = self.adicionar(tipo, alvo)
        return registro

    def _atualizar(self, tipo: str, alvo: str, *, estado: str, erro: str | None,
                   resumo: str = "", contar_tentativa: bool = True,
                   zerar_tentativas: bool = False) -> None:
        registro = self._registro(tipo, alvo)
        registro["estado"] = estado
        registro["erro"] = erro
        registro["ultima_tentativa"] = agora_iso()
        registro["ultimo_ambiente"] = ambiente_atual().value
        registro["atualizado_em"] = agora_iso()
        if resumo:
            registro["resumo"] = resumo
        if zerar_tentativas:
            registro["tentativas"] = 0
        elif contar_tentativa:
            registro["tentativas"] = registro.get("tentativas", 0) + 1
        registro.pop("proxima_apos", None)

    # ---------- seleção ----------

    def executavel_agora(self, registro: dict) -> bool:
        estado = registro.get("estado", PENDENTE)
        local = ambiente_atual() is Ambiente.LOCAL

        if estado == DESISTIDO:
            return False
        if estado == ADIADO_LOCAL:
            return local  # só o seu PC resolve estes
        if estado == PENDENTE:
            return True
        if estado == FALHOU:
            proxima = _parse(registro.get("proxima_apos"))
            return proxima is None or _agora() >= proxima
        if estado in (OK, SEM_DADOS):
            # prazo explícito definido por quem marcou tem precedência sobre o TTL fixo
            proxima = _parse(registro.get("proxima_apos"))
            if proxima is not None:
                return _agora() >= proxima
            ttl = TTL_PADRAO_HORAS.get(registro.get("tipo", ""), 24 * 7)
            if estado == SEM_DADOS:
                ttl = max(ttl, 24 * 14)  # não insistir onde já não havia nada
            ultima = _parse(registro.get("ultima_tentativa"))
            return ultima is None or _agora() - ultima > timedelta(hours=ttl)
        return True

    def proximas(self, tipo: str | None = None, limite: int | None = None) -> list[dict]:
        candidatas = [
            r for r in self.tabela.todos()
            if (tipo is None or r.get("tipo") == tipo) and self.executavel_agora(r)
        ]
        # prioridade primeiro; dentro dela, o que está parado há mais tempo
        candidatas.sort(key=lambda r: (
            r.get("prioridade", 5),
            r.get("ultima_tentativa") or "",
        ))
        return candidatas[:limite] if limite else candidatas

    def resumo(self) -> dict[str, dict[str, int]]:
        """Contagem por tipo e estado — é o que o painel do site mostra."""
        contagem: dict[str, dict[str, int]] = {}
        for registro in self.tabela.todos():
            por_tipo = contagem.setdefault(registro.get("tipo", "?"), {})
            estado = registro.get("estado", PENDENTE)
            por_tipo[estado] = por_tipo.get(estado, 0) + 1
        return contagem

    def salvar(self) -> None:
        self.tabela.salvar()
