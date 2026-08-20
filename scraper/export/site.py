"""Gera os JSON que o site estático consome.

O site é uma página só, sem servidor: ele baixa estes arquivos e filtra tudo no
navegador. Por isso o formato aqui é pensado para a tela — cada empresa já vem com as
feiras em que expõe, os contatos achatados e os campos que viram filtro, para o
JavaScript não ter que cruzar tabelas.

Só entra no arquivo principal o que serve ao intérprete: empresa chinesa (confirmada ou
provável) com participação em feira que ainda vai acontecer. O resto vai para arquivos
separados (revisão e histórico), que a interface carrega só se você pedir.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from ..core.datas import dias_ate
from ..core.store import DATA_DIR, Tabela, escrever_json
from ..deteccao import china as china_mod

SAIDA = DATA_DIR.parent / "docs" / "data"


def _renovar_versao_dos_assets() -> None:
    """Carimba ?v=<data> no CSS e no JS a cada exportação.

    Sem isso o navegador continua servindo o JavaScript antigo depois de publicarmos
    uma correção: para o usuário, o bug simplesmente "não foi consertado" — e não há
    como ele saber que precisa limpar o cache.
    """
    import re

    indice = SAIDA.parent / "index.html"
    if not indice.exists():
        return
    versao = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    html = indice.read_text(encoding="utf-8")
    novo = re.sub(r"(assets/(?:estilo\.css|app\.js))(\?v=\d+)?", rf"\1?v={versao}", html)
    if novo != html:
        indice.write_text(novo, encoding="utf-8")


def _data_decrescente(iso: str) -> int:
    """Chave de ordenação que põe a data mais recente primeiro (sem data vai para o fim)."""
    if not iso:
        return 0
    try:
        return -int(iso.replace("-", ""))
    except ValueError:
        return 0


def _agrupar_participacoes(participacoes: list[dict]) -> dict[str, list[dict]]:
    por_empresa: dict[str, list[dict]] = {}
    for p in participacoes:
        por_empresa.setdefault(p["empresa_id"], []).append(p)
    for lista in por_empresa.values():
        lista.sort(key=lambda p: p.get("evento_inicio") or "9999")
    return por_empresa


def _linha_empresa(empresa: dict, participacoes: list[dict], hoje: date) -> dict:
    futuras = [p for p in participacoes if not p.get("_encerrada")]
    proxima = futuras[0] if futuras else (participacoes[0] if participacoes else {})

    return {
        "id": empresa["id"],
        "nome": empresa.get("nome", ""),
        "nome_zh": empresa.get("nome_zh", ""),
        "pais": empresa.get("pais", ""),
        "origem": empresa.get("origem", "china"),
        "cidade": empresa.get("cidade", ""),
        "provincia": empresa.get("provincia", ""),
        "setor": empresa.get("setor", ""),
        "produtos": empresa.get("produtos", [])[:8],
        "website": empresa.get("website", ""),
        "website_cn": empresa.get("website_cn", ""),
        "emails": empresa.get("emails", []),
        "telefones": empresa.get("telefones", []),
        "whatsapps": empresa.get("whatsapps", []),
        "wechat": empresa.get("wechat", ""),
        "contato_nome": empresa.get("contato_nome", ""),
        "funcionarios": empresa.get("funcionarios", ""),
        "ano_fundacao": empresa.get("ano_fundacao", ""),
        "tipo_negocio": empresa.get("tipo_negocio", ""),
        "receita_anual": empresa.get("receita_anual", ""),
        "porte": empresa.get("faixa_funcionarios", ""),
        "perfis": empresa.get("perfis", {}),
        "score_china": empresa.get("score_china", 0),
        "classificacao": empresa.get("classificacao_china", ""),
        "motivos": empresa.get("motivos_deteccao", [])[:4],
        "descricao": (empresa.get("descricao") or "")[:400],
        "links_pesquisa": china_mod.links_pesquisa(
            empresa.get("nome", ""), empresa.get("website", "")
        ),
        # o que a interface usa para filtrar e ordenar
        "tem_contato": bool(empresa.get("emails") or empresa.get("telefones")
                            or empresa.get("wechat") or empresa.get("whatsapps")),
        "feiras": [
            {
                "evento_id": p.get("evento_id", ""),
                "nome": p.get("evento_nome", ""),
                "inicio": p.get("evento_inicio", ""),
                "fim": p.get("evento_fim", ""),
                "local": p.get("local_nome", ""),
                "cidade": p.get("cidade", ""),
                "uf": p.get("uf", ""),
                "stand": p.get("stand", ""),
                "encerrada": p.get("_encerrada", False),
                "dias": p.get("_dias"),
            }
            for p in participacoes
        ],
        "proxima_feira": proxima.get("evento_nome", ""),
        "proxima_data": proxima.get("evento_inicio", ""),
        "proximo_stand": proxima.get("stand", ""),
        "dias_para_proxima": proxima.get("_dias"),
        "tem_feira_futura": bool(futuras),
        "atualizado_em": empresa.get("atualizado_em", ""),
    }


def exportar(hoje: date | None = None) -> dict:
    hoje = hoje or date.today()
    eventos_tab = Tabela("eventos").carregar()
    empresas_tab = Tabela("empresas").carregar()
    participacoes_tab = Tabela("participacoes").carregar()

    eventos_por_id = {e["id"]: e for e in eventos_tab.todos()}

    # marca cada participação com o estado da feira, para o filtro de encerradas
    participacoes = []
    for p in participacoes_tab.todos():
        evento = eventos_por_id.get(p.get("evento_id"), {})
        p = dict(p)
        p["_encerrada"] = bool(evento.get("encerrado"))
        p["_dias"] = dias_ate(p.get("evento_inicio", ""), hoje)
        participacoes.append(p)
    por_empresa = _agrupar_participacoes(participacoes)

    principais, revisao = [], []
    for empresa in empresas_tab.todos():
        classificacao = empresa.get("classificacao_china", "")
        if classificacao not in (china_mod.CONFIRMADA, china_mod.PROVAVEL,
                                 china_mod.SUSPEITA):
            continue
        linha = _linha_empresa(empresa, por_empresa.get(empresa["id"], []), hoje)
        if classificacao == china_mod.SUSPEITA:
            revisao.append(linha)
        else:
            principais.append(linha)

    # ordena por urgência: quem expõe antes aparece primeiro
    def ordem(linha):
        dias = linha.get("dias_para_proxima")
        if dias is None or dias < 0:
            return (2, 0, linha["nome"])
        return (0 if linha["tem_contato"] else 1, dias, linha["nome"])

    principais.sort(key=ordem)
    revisao.sort(key=lambda x: x["nome"])

    # Quantas chinesas por feira, contadas AGORA e não quando a feira foi coletada.
    #
    # O evento guarda um total_chinesas escrito no momento da coleta, e ele envelhece
    # mal: o enriquecimento reclassifica empresa o tempo todo (chinesa de nome
    # ocidental só se revela quando alguém vai buscar o telefone +86), mas a feira só
    # é recoletada semanas depois. A Beauty Fair ficou anunciando 3 chinesas no
    # calendário enquanto já tinha 39 na base — e quem olhasse a lista não teria como
    # saber que o número estava velho.
    chinesas_por_evento: dict[str, int] = {}
    for p in participacoes:
        empresa = empresas_tab.obter(p["empresa_id"]) or {}
        if empresa.get("classificacao_china") in (china_mod.CONFIRMADA,
                                                  china_mod.PROVAVEL):
            chinesas_por_evento[p["evento_id"]] = (
                chinesas_por_evento.get(p["evento_id"], 0) + 1
            )

    eventos_saida = []
    for evento in eventos_tab.todos():
        eventos_saida.append({
            "id": evento["id"],
            "nome": evento.get("nome", ""),
            "site": evento.get("site", ""),
            "pagina_expositores": evento.get("pagina_expositores", ""),
            "inicio": evento.get("data_inicio", ""),
            "fim": evento.get("data_fim", ""),
            "data_texto": evento.get("data_texto", ""),
            "local": evento.get("local_nome", ""),
            "cidade": evento.get("cidade", ""),
            "uf": evento.get("uf", ""),
            "pavilhao": evento.get("pavilhao", ""),
            "setor": evento.get("setor", ""),
            "densidade_china": evento.get("densidade_china", ""),
            "encerrada": bool(evento.get("encerrado")),
            "dias": dias_ate(evento.get("data_inicio", ""), hoje),
            "total_expositores": evento.get("total_expositores", 0),
            "total_chinesas": chinesas_por_evento.get(
                evento["id"], evento.get("total_chinesas", 0)
            ),
            "plataforma": evento.get("plataforma", ""),
            "prioridade": evento.get("prioridade", 5),
        })
    eventos_saida.sort(key=lambda e: (e["encerrada"], e["inicio"] or "9999"))

    SAIDA.mkdir(parents=True, exist_ok=True)
    _renovar_versao_dos_assets()
    escrever_json(SAIDA / "empresas.json", principais)
    escrever_json(SAIDA / "empresas_revisao.json", revisao)
    escrever_json(SAIDA / "feiras.json", eventos_saida)

    # Oportunidades: vagas anunciadas pelo consulado. Ficam em arquivo separado porque
    # sao poucas e o site as destaca no topo — nao entram na lista de empresas.
    oportunidades = []
    for item in Tabela("oportunidades").carregar().todos():
        dias = None
        if item.get("data_publicacao"):
            dias = -(dias_ate(item["data_publicacao"], hoje) or 0)
        oportunidades.append({
            "id": item["id"],
            "titulo": item.get("titulo", ""),
            "url": item.get("url", ""),
            "fonte": item.get("fonte", ""),
            "data": item.get("data_publicacao", ""),
            "tipo": item.get("tipo", "noticia"),
            "resumo": (item.get("resumo") or "")[:300],
            "dias_atras": dias,
        })
    # vaga primeiro, depois missão, e dentro de cada grupo a mais recente no topo.
    # A data vira negativa via chave invertida para ordenar decrescente sem gambiarra.
    ordem_tipo = {"vaga": 0, "missao": 1, "noticia": 2}
    oportunidades.sort(
        key=lambda o: (ordem_tipo.get(o["tipo"], 3), _data_decrescente(o["data"]))
    )
    escrever_json(SAIDA / "oportunidades.json", oportunidades)

    from ..pipeline import situacao_geral
    meta = {
        "atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gerado_em_ambiente": situacao_geral()["ambiente"],
        "total_empresas": len(principais),
        "empresas_com_contato": sum(1 for e in principais if e["tem_contato"]),
        "empresas_com_wechat": sum(1 for e in principais if e["wechat"]),
        "empresas_feira_futura": sum(1 for e in principais if e["tem_feira_futura"]),
        "total_revisao": len(revisao),
        "total_feiras": len(eventos_saida),
        "feiras_futuras": sum(1 for e in eventos_saida if not e["encerrada"]),
        "feiras_com_lista": sum(1 for e in eventos_saida if e["total_expositores"]),
        "vagas_consulado": sum(1 for o in oportunidades if o["tipo"] == "vaga"),
        "vagas_consulado_recentes": sum(
            1 for o in oportunidades
            if o["tipo"] == "vaga" and o["dias_atras"] is not None and o["dias_atras"] <= 90
        ),
    }
    escrever_json(SAIDA / "meta.json", meta)
    return meta
