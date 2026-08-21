"""Publica no GitHub o que a rodada local coletou, para o site refletir.

Na nuvem o workflow já commita e dá push. No seu PC não havia nada disso: os dados
ficavam no disco e o site continuava servindo a versão anterior. O sintoma era o site
"não atualizar" sem nenhum erro à vista.

O trabalho de verdade aqui não é o push — é o conflito. A rodada da madrugada publica
sozinha, então quando o seu PC vai publicar o histórico já andou. E conflito em JSONL
não pode ser resolvido escolhendo um lado: o lado da nuvem tem empresas que o seu PC
não viu, e o seu PC tem empresas que a nuvem não viu. Escolher qualquer um dos dois
apaga metade do dia. Por isso os dois lados são fundidos com o mesmo `mesclar()` do
store, que é a única resolução que não perde dado.

Os arquivos de docs/data são derivados, então não são fundidos: depois de resolver os
JSONL, o export roda de novo e reescreve os dois a partir do resultado.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

# Só o que a coleta produz. Nunca `git add -A`: o código em que você está mexendo não
# deve entrar num commit de dados sem você pedir.
CAMINHOS = ["data", "docs/data", "docs/index.html"]

TENTATIVAS_PUSH = 2


class FalhaAoPublicar(RuntimeError):
    pass


def _git(*args: str, checar: bool = True) -> str:
    processo = subprocess.run(
        ["git", *args], cwd=RAIZ, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if checar and processo.returncode != 0:
        raise FalhaAoPublicar(
            f"git {' '.join(args)} falhou: {(processo.stderr or '').strip()[:300]}"
        )
    return (processo.stdout or "").strip()


def _ha_o_que_commitar() -> bool:
    return bool(_git("diff", "--cached", "--name-only"))


def _resumo() -> str:
    """Descreve o commit pelos números do site, não por 'atualização de dados'."""
    try:
        d = json.loads((RAIZ / "docs" / "data" / "meta.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "dados: coleta local"
    return (f"dados: coleta local - {d.get('total_empresas', 0)} chinesas, "
            f"{d.get('empresas_com_contato', 0)} com contato, "
            f"{d.get('feiras_com_lista', 0)} feiras com lista")


def _lado(estagio: int, caminho: str) -> list[dict]:
    bruto = subprocess.run(["git", "show", f":{estagio}:{caminho}"],
                           cwd=RAIZ, capture_output=True)
    return [json.loads(linha) for linha in bruto.stdout.decode("utf-8").splitlines()
            if linha.strip()]


def _fundir_jsonl_em_conflito() -> list[str]:
    """Resolve cada JSONL conflitado fundindo os dois lados, registro a registro."""
    from .store import mesclar

    conflitados = [c for c in _git("diff", "--name-only", "--diff-filter=U").splitlines()
                   if c.strip()]
    resolvidos = []

    for caminho in conflitados:
        if not caminho.endswith(".jsonl"):
            continue

        # Durante o rebase, :2 é o que já está publicado e :3 é o que estamos aplicando
        # por cima. A ordem importa: o nosso entra depois para que, num campo que os
        # dois preencheram, prevaleça o mais recente.
        juntos: dict[str, dict] = {}
        for registro in _lado(2, caminho) + _lado(3, caminho):
            chave = registro.get("id")
            if chave is None:
                continue
            juntos[chave] = (mesclar(juntos[chave], registro) if chave in juntos
                             else registro)

        (RAIZ / caminho).write_text(
            "\n".join(json.dumps(juntos[k], ensure_ascii=False, sort_keys=True)
                      for k in sorted(juntos)) + "\n",
            encoding="utf-8", newline="\n",
        )
        _git("add", caminho)
        resolvidos.append(caminho)

    # docs/data é derivado: não adianta fundir JSON de saída. Aceitamos um lado só para
    # o rebase seguir e reescrevemos tudo com o export logo depois.
    for caminho in conflitados:
        if not caminho.endswith(".jsonl"):
            _git("checkout", "--theirs", "--", caminho, checar=False)
            _git("add", caminho, checar=False)

    return resolvidos


def publicar(sem_publicar: bool = False) -> dict:
    """Commita, integra o que a nuvem publicou e dá push. Devolve o que aconteceu."""
    from .perfil import na_nuvem

    resultado = {"publicou": False, "motivo": "", "conflitos_fundidos": [], "commit": ""}

    if sem_publicar:
        resultado["motivo"] = "desativado por --sem-publicar"
        return resultado
    if na_nuvem():
        resultado["motivo"] = "na nuvem quem publica é o workflow"
        return resultado

    ramo = _git("rev-parse", "--abbrev-ref", "HEAD")
    if ramo != "main":
        # Publicar de outro ramo não atualiza o site e ainda espalha commit de dados
        # onde ninguém espera.
        resultado["motivo"] = f"ramo '{ramo}' não é main; nada publicado"
        return resultado

    _git("add", "--", *CAMINHOS, checar=False)
    if not _ha_o_que_commitar():
        resultado["motivo"] = "nada mudou desde a última publicação"
        return resultado

    _git("commit", "-m", _resumo())

    for tentativa in range(TENTATIVAS_PUSH):
        _git("fetch", "--quiet", "origin", "main")

        if _git("rev-list", "--count", "HEAD..origin/main") not in ("", "0"):
            rebase = subprocess.run(["git", "rebase", "origin/main"], cwd=RAIZ,
                                    capture_output=True, text=True,
                                    encoding="utf-8", errors="replace")
            if rebase.returncode != 0:
                fundidos = _fundir_jsonl_em_conflito()
                if not fundidos:
                    _git("rebase", "--abort", checar=False)
                    raise FalhaAoPublicar(
                        "rebase falhou por algo que não é JSONL de dados. Não mexi em "
                        "nada e desfiz: rode 'git status' e me chame."
                    )
                resultado["conflitos_fundidos"] = fundidos

                seguir = subprocess.run(
                    ["git", "-c", "core.editor=true", "rebase", "--continue"],
                    cwd=RAIZ, capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                )
                if seguir.returncode != 0:
                    _git("rebase", "--abort", checar=False)
                    raise FalhaAoPublicar(
                        f"não consegui concluir o rebase: "
                        f"{(seguir.stderr or '').strip()[:200]}"
                    )

                # A fusão mudou os dados, então docs/data ficou velho na mesma hora.
                from ..export.site import exportar
                exportar()
                _git("add", "--", *CAMINHOS, checar=False)
                if _ha_o_que_commitar():
                    _git("commit", "--amend", "--no-edit")

        push = subprocess.run(["git", "push", "origin", "main"], cwd=RAIZ,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if push.returncode == 0:
            resultado["publicou"] = True
            resultado["commit"] = _git("rev-parse", "--short", "HEAD")
            return resultado

        if tentativa == TENTATIVAS_PUSH - 1:
            raise FalhaAoPublicar(
                f"push recusado {TENTATIVAS_PUSH}x: {(push.stderr or '').strip()[:200]}"
            )

    return resultado
