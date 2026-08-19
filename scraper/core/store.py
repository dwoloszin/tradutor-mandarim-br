"""Armazenamento acumulativo em JSONL — a base de tudo.

Por que JSONL e não SQLite: a nuvem e o seu PC sincronizam pelo Git. Um .db binário
não faz merge — duas rodadas no mesmo dia viram conflito insolúvel. Um JSONL com uma
linha por registro, ordenado por id, gera diff limpo e o Git resolve sozinho quase sempre.

Por que acumulativo e não regenerado: achar o e-mail e o WeChat de uma empresa chinesa
custa muitas requisições. Se cada rodada recomeçasse do zero (como o pipeline antigo
fazia), jogaríamos fora esse trabalho todo dia. Aqui cada rodada faz upsert: campo
preenchido nunca é apagado por campo vazio.
"""
from __future__ import annotations

import contextlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

RAIZ = Path(__file__).resolve().parent.parent.parent
DATA_DIR = RAIZ / "data"
MANUAL_DIR = DATA_DIR / "manual"

# Campos que somam em vez de substituir: juntar e-mails de duas fontes é ganho,
# escolher "o mais novo" seria perda.
CAMPOS_LISTA = {
    "emails", "telefones", "fontes", "motivos_deteccao", "produtos",
    "categorias", "sites_alternativos", "whatsapps",
}

# Nunca sobrescritos por scraping: é o que você corrigiu na mão.
PREFIXO_MANUAL = "manual_"


def agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RodadaEmAndamento(Exception):
    """Já existe uma coleta escrevendo no banco."""


@contextlib.contextmanager
def trava_de_escrita(nome: str = "coleta", esperar_segundos: int = 0):
    """Impede que duas rodadas escrevam no store ao mesmo tempo.

    Cada tabela é carregada na memória no início da rodada e regravada inteira no fim.
    Se duas rodadas correm juntas, a que terminar por último apaga o trabalho da outra —
    aconteceu de verdade aqui: uma correção de 413 endereços foi sobrescrita por um
    enriquecimento que havia carregado o arquivo antes.

    A trava é um arquivo com o PID. Se o processo dono morreu, ela é considerada órfã
    e liberada, para um Ctrl+C não deixar o projeto travado para sempre.
    """
    import os
    import time

    caminho = DATA_DIR / f".trava-{nome}"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    limite = time.monotonic() + esperar_segundos

    while True:
        try:
            descritor = os.open(caminho, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descritor, f"{os.getpid()}\n{agora_iso()}".encode())
            os.close(descritor)
            break
        except FileExistsError:
            if _trava_orfa(caminho):
                caminho.unlink(missing_ok=True)
                continue
            if time.monotonic() >= limite:
                dono = caminho.read_text(encoding="utf-8", errors="replace").split("\n")[0]
                raise RodadaEmAndamento(
                    f"outra rodada (PID {dono}) está escrevendo em data/. "
                    f"Espere ela terminar ou remova {caminho} se sobrou de um processo morto."
                ) from None
            time.sleep(2)

    try:
        yield
    finally:
        caminho.unlink(missing_ok=True)


def _trava_orfa(caminho: Path) -> bool:
    """A trava ficou para trás de um processo que já morreu?"""
    import os
    try:
        pid = int(caminho.read_text(encoding="utf-8").split("\n")[0].strip())
    except (OSError, ValueError):
        return True
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)  # sinal 0: só testa se o processo existe
    except OSError:
        return True
    except Exception:
        return False
    return False


def _sem_vazios(valor) -> bool:
    """Um campo só substitui outro se realmente tiver conteúdo."""
    if valor is None:
        return False
    if isinstance(valor, str):
        return valor.strip() != ""
    if isinstance(valor, (list, dict, tuple, set)):
        return len(valor) > 0
    return True


def mesclar(antigo: dict, novo: dict) -> dict:
    """Funde o registro novo sobre o antigo, sem perder dado bom.

    Regras:
      - campo vazio no novo nunca apaga campo preenchido no antigo;
      - listas (emails, telefones, fontes...) viram união, preservando a ordem;
      - dicionários são mesclados recursivamente;
      - campos manual_* do antigo sempre vencem.
    """
    resultado = dict(antigo)

    for chave, valor_novo in novo.items():
        if chave.startswith(PREFIXO_MANUAL):
            # dado manual só é gravado se ainda não existir (ou se vier de manual)
            if chave not in resultado or _sem_vazios(valor_novo):
                resultado[chave] = valor_novo
            continue

        valor_antigo = resultado.get(chave)

        if chave in CAMPOS_LISTA:
            uniao = list(valor_antigo or [])
            for item in (valor_novo or []):
                if item not in uniao:
                    uniao.append(item)
            resultado[chave] = uniao
            continue

        if isinstance(valor_antigo, dict) and isinstance(valor_novo, dict):
            resultado[chave] = mesclar(valor_antigo, valor_novo)
            continue

        if _sem_vazios(valor_novo):
            resultado[chave] = valor_novo
        elif chave not in resultado:
            resultado[chave] = valor_novo

    return resultado


class Tabela:
    """Uma coleção de registros com id único, persistida como JSONL ordenado."""

    def __init__(self, nome: str, diretorio: Path | None = None):
        self.nome = nome
        self.caminho = (diretorio or DATA_DIR) / f"{nome}.jsonl"
        self._registros: dict[str, dict] = {}
        self._carregado = False

    # ---------- leitura / escrita ----------

    def carregar(self) -> "Tabela":
        self._registros = {}
        if self.caminho.exists():
            with open(self.caminho, "r", encoding="utf-8") as f:
                for numero, linha in enumerate(f, start=1):
                    linha = linha.strip()
                    if not linha:
                        continue
                    try:
                        registro = json.loads(linha)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"{self.caminho.name}, linha {numero}: JSON inválido ({exc})"
                        ) from exc
                    identificador = registro.get("id")
                    if identificador:
                        self._registros[identificador] = registro
        self._carregado = True
        return self

    def salvar(self) -> Path:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        # ordenar por id deixa o diff do Git estável entre rodadas
        with open(self.caminho, "w", encoding="utf-8", newline="\n") as f:
            for identificador in sorted(self._registros):
                registro = self._registros[identificador]
                f.write(json.dumps(registro, ensure_ascii=False, sort_keys=True) + "\n")
        return self.caminho

    def _garantir_carregado(self) -> None:
        if not self._carregado:
            self.carregar()

    # ---------- operações ----------

    def upsert(self, registro: dict) -> dict:
        """Insere ou funde pelo id. Retorna o registro final."""
        self._garantir_carregado()
        identificador = registro.get("id")
        if not identificador:
            raise ValueError(f"registro sem id para a tabela {self.nome}: {registro!r}")

        existente = self._registros.get(identificador)
        if existente is None:
            registro.setdefault("criado_em", agora_iso())
            registro["atualizado_em"] = agora_iso()
            self._registros[identificador] = registro
            return registro

        fundido = mesclar(existente, registro)
        # só carimba atualização se algo mudou de fato — evita commit diário vazio
        if fundido != existente:
            fundido["atualizado_em"] = agora_iso()
        self._registros[identificador] = fundido
        return fundido

    def obter(self, identificador: str) -> dict | None:
        self._garantir_carregado()
        return self._registros.get(identificador)

    def remover(self, identificador: str) -> bool:
        self._garantir_carregado()
        return self._registros.pop(identificador, None) is not None

    def todos(self) -> list[dict]:
        self._garantir_carregado()
        return [self._registros[k] for k in sorted(self._registros)]

    def filtrar(self, **criterios) -> list[dict]:
        return [
            r for r in self.todos()
            if all(r.get(campo) == valor for campo, valor in criterios.items())
        ]

    def __len__(self) -> int:
        self._garantir_carregado()
        return len(self._registros)

    def __iter__(self) -> Iterator[dict]:
        return iter(self.todos())


def aplicar_overrides_manuais(tabela: Tabela, arquivo: str) -> int:
    """Aplica correções feitas à mão por cima dos dados coletados.

    data/manual/<arquivo>.json guarda {"<id>": {campos...}}. É a válvula de escape para
    quando o robô erra: você corrige ali e nenhuma rodada futura desfaz.
    """
    caminho = MANUAL_DIR / f"{arquivo}.json"
    if not caminho.exists():
        return 0
    with open(caminho, "r", encoding="utf-8") as f:
        overrides = json.load(f)

    aplicados = 0
    for identificador, campos in overrides.items():
        existente = tabela.obter(identificador)
        if existente is None:
            campos = {"id": identificador, **campos}
            tabela.upsert(campos)
        else:
            existente.update(campos)  # manual vence sem discussão
            existente["atualizado_em"] = agora_iso()
        aplicados += 1
    return aplicados


def escrever_json(caminho: Path, dados) -> Path:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2, sort_keys=False)
    return caminho


def ler_json(caminho: Path, padrao=None):
    if not caminho.exists():
        return padrao
    with open(caminho, "r", encoding="utf-8") as f:
        return json.load(f)
