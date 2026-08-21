"""Linha de comando do projeto.

Uso típico:

  python -m scraper.cli tudo            # roda o ciclo completo (agenda, expositores, enriquecer, exportar)
  python -m scraper.cli pendentes       # SÓ o que a nuvem não conseguiu — é o comando do seu PC
  python -m scraper.cli agenda          # atualiza só o calendário de feiras
  python -m scraper.cli radar           # procura feiras novas na imprensa do setor
  python -m scraper.cli oportunidades   # vagas anunciadas pelo consulado chines
  python -m scraper.cli expositores     # baixa listas de expositores
  python -m scraper.cli enriquecer      # busca contato/WeChat das empresas chinesas
  python -m scraper.cli exportar        # regenera os JSON do site
  python -m scraper.cli situacao        # mostra o estado do banco e da fila
  python -m scraper.cli progresso       # acompanha a rodada em andamento
  python -m scraper.cli progresso -f    # idem, atualizando sozinho
  python -m scraper.cli investigar URL  # diagnostica a plataforma de uma feira nova
  python -m scraper.cli login           # abre o navegador para você logar no tradechina

Todos aceitam --limite N para controlar quantas tarefas processar numa rodada.
"""
from __future__ import annotations

import argparse
import sys


def _configurar_console() -> None:
    """Sem isso, imprimir nome de empresa em chinês quebra o console do Windows."""
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _imprimir(titulo: str, dados: dict) -> None:
    print(f"\n{titulo}")
    for chave, valor in dados.items():
        if isinstance(valor, dict):
            print(f"  {chave}:")
            for sub, subvalor in valor.items():
                print(f"      {sub:22s} {subvalor}")
        else:
            print(f"  {chave:22s} {valor}")


def main(argv: list[str] | None = None) -> int:
    _configurar_console()

    parser = argparse.ArgumentParser(prog="scraper.cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("comando", choices=[
        "tudo", "pendentes", "agenda", "radar", "oportunidades", "expositores",
        "enriquecer", "vagas", "exportar", "situacao", "progresso", "investigar", "login",
        "limpar-cache",
    ])
    parser.add_argument("argumento", nargs="?", default="")
    parser.add_argument("--limite", type=int, default=None,
                        help="máximo de tarefas nesta rodada")
    parser.add_argument("--sem-publicar", action="store_true",
                        help="não dá push no fim (por padrão a rodada local publica)")
    parser.add_argument("--esperar", type=int, default=0, metavar="SEGUNDOS",
                        help="espera a rodada em andamento terminar, em vez de recusar")
    args = parser.parse_args(argv)

    from .core.perfil import ambiente_atual
    print(f"[ambiente: {ambiente_atual().value}]")

    if args.comando == "progresso":
        # acompanhamento de rodada em andamento; nao precisa de trava (so le)
        import time as _tempo

        from .core.progresso import formatar, ler
        acompanhar_continuo = args.argumento in ("-f", "seguir", "acompanhar")
        while True:
            print("[2J[H" if acompanhar_continuo else "", end="")
            print(formatar(ler()))
            if not acompanhar_continuo:
                return 0
            _tempo.sleep(10)

    if args.comando == "situacao":
        from .pipeline import situacao_geral
        _imprimir("SITUAÇÃO", situacao_geral())
        return 0

    if args.comando == "investigar":
        if not args.argumento:
            print("informe a URL do site da feira")
            return 2
        from .fontes.plataformas.descoberta import descobrir
        from .fontes.plataformas.detectar import investigar
        _imprimir("DIAGNÓSTICO", investigar(args.argumento))
        _imprimir("DESCOBERTA EM CAMADAS", descobrir(args.argumento))
        return 0

    if args.comando == "login":
        from .core.navegador import abrir_para_login
        alvo = args.argumento or "https://www.tradechina.com/"
        abrir_para_login(alvo)
        return 0

    if args.comando == "limpar-cache":
        from .core.http import limpar_cache
        print(f"{limpar_cache()} arquivos de cache removidos")
        return 0

    from . import pipeline
    from .core.store import RodadaEmAndamento, trava_de_escrita

    # comandos que so leem nao precisam de trava
    if args.comando in ("tudo", "pendentes", "agenda", "radar", "oportunidades",
                        "expositores", "enriquecer", "vagas"):
        try:
            with trava_de_escrita(esperar_segundos=args.esperar):
                return _executar(args, pipeline)
        except RodadaEmAndamento as exc:
            print(f"\n{exc}")
            return 1
    return _executar(args, pipeline)


def _executar(args, pipeline) -> int:

    if args.comando in ("tudo", "agenda"):
        _imprimir("1) AGENDA DE FEIRAS", pipeline.etapa_agenda())

    if args.comando in ("tudo", "radar"):
        # imprensa do setor: descobre feira nova (e fora de SP) antes das agendas
        from .fontes.locais.radar_noticias import coletar as radar
        try:
            _imprimir("1b) RADAR DE FEIRAS NOVAS", radar())
        except Exception as exc:  # noqa: BLE001
            _imprimir("1b) RADAR DE FEIRAS NOVAS", {"falhou": f"{type(exc).__name__}: {exc}"})
    if args.comando in ("tudo", "oportunidades"):
        _imprimir("1c) OPORTUNIDADES (consulado)", pipeline.etapa_oportunidades())

    if args.comando in ("tudo", "expositores"):
        _imprimir("2) EXPOSITORES", pipeline.etapa_expositores(args.limite))
    if args.comando in ("tudo", "enriquecer"):
        _imprimir("3) ENRIQUECIMENTO", pipeline.etapa_enriquecer(args.limite))

    if args.comando in ("tudo", "vagas"):
        # herdado da primeira versão do projeto: vagas de tradutor de mandarim em
        # portais brasileiros. Complementa a prospecção direta nas feiras.
        from .empregos import scrape_empregos
        from .utils import save_json
        try:
            resultado = scrape_empregos()
            save_json("empregos.json", resultado)
            _imprimir("VAGAS DE TRADUTOR", {"vagas_encontradas": len(resultado["vagas"])})
        except Exception as exc:  # noqa: BLE001
            _imprimir("VAGAS DE TRADUTOR", {"falhou": f"{type(exc).__name__}: {exc}"})

    if args.comando == "pendentes":
        # o comando do seu PC: pega o que a nuvem deixou para trás
        from .core.perfil import na_nuvem
        if na_nuvem():
            print("aviso: 'pendentes' foi feito para rodar no seu computador, não na nuvem")
        _imprimir("OPORTUNIDADES", pipeline.etapa_oportunidades())
        _imprimir("EXPOSITORES PENDENTES", pipeline.etapa_expositores(args.limite))
        _imprimir("ENRIQUECIMENTO PENDENTE", pipeline.etapa_enriquecer(args.limite))

    if args.comando in ("tudo", "exportar", "pendentes"):
        from .export.site import exportar
        _imprimir("EXPORTAÇÃO PARA O SITE", exportar())

    _imprimir("SITUAÇÃO FINAL", pipeline.situacao_geral())

    # Publicar faz parte de terminar. Sem isto os dados ficavam no disco e o site
    # continuava servindo a versão anterior, sem erro nenhum à vista — a rodada
    # parecia ter dado certo e não tinha chegado a lugar nenhum.
    if args.comando in ("tudo", "pendentes", "exportar"):
        from .core.publicar import FalhaAoPublicar, publicar
        try:
            saida = publicar(sem_publicar=args.sem_publicar)
        except FalhaAoPublicar as exc:
            _imprimir("PUBLICAÇÃO", {"falhou": str(exc),
                                     "seus_dados": "estão salvos no disco"})
            return 0
        if saida["publicou"]:
            _imprimir("PUBLICAÇÃO", {
                "commit": saida["commit"],
                "site": "https://dwoloszin.github.io/tradutor-mandarim-br/",
                "conflitos_fundidos": len(saida["conflitos_fundidos"]),
                "obs": "o GitHub Pages leva ~1 min para servir a versão nova",
            })
        else:
            _imprimir("PUBLICAÇÃO", {"nao_publicou": saida["motivo"]})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
