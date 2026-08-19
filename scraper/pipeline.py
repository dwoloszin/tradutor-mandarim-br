"""Pipeline de coleta, em etapas independentes e reentrantes.

Cada etapa lê e grava no store e conversa com a fila. Nenhuma etapa depende de a
anterior ter rodado na mesma execução — é isso que permite a nuvem fazer metade do
trabalho de madrugada e o seu PC completar o resto de dia, sem coordenação nenhuma
além dos arquivos versionados no Git.

Etapas:
  agenda        varre os centros de exposição e atualiza o calendário
  expositores   para cada feira que interessa, baixa a lista de empresas
  enriquecer    para cada empresa chinesa, busca contato, WeChat e porte
  exportar      gera os JSON que o site consome

Regra que atravessa tudo: feira encerrada não consome requisição. O intérprete não
pode oferecer serviço para um evento que já aconteceu, então gastar banda com isso
seria tirar orçamento das feiras que ainda vão acontecer.
"""
from __future__ import annotations

from datetime import date

from .core import fila as fila_mod
from .core.datas import dias_ate, encerrado
from .core.http import Bloqueado, FalhouDeVerdade
from .core.modelos import (
    chave_empresa,
    normalizar_url,
    chave_participacao,
    dominio_proprio,
    nome_canonico,
    nova_empresa,
)
from .core.perfil import ambiente_atual, na_nuvem
from .core.store import DATA_DIR, Tabela, agora_iso, aplicar_overrides_manuais, ler_json
from .deteccao import china as china_mod
from .fontes import expositores as expositores_mod
from .fontes.locais import agenda as agenda_mod

RAIZ_CONFIG = DATA_DIR.parent / "config"

# Quanto antes da feira vale a pena ter os dados prontos. Prospecção de intérprete
# acontece nas semanas anteriores; depois que a feira começa, o valor despenca.
JANELA_PROSPECCAO_DIAS = 240


def _config_feiras() -> dict[str, dict]:
    """Feiras priorizadas à mão, indexadas por nome canônico."""
    dados = ler_json(RAIZ_CONFIG / "feiras_prioritarias.json", {"feiras": []})
    indice = {}
    for feira in dados.get("feiras", []):
        indice[nome_canonico(feira["nome"])] = feira
    return indice


def _casar_config(evento: dict, config: dict[str, dict]) -> dict:
    """Liga o evento da agenda com a entrada curada, tolerando variação de nome.

    A agenda do local nem sempre usa o nome que conhecemos: a Intersolar aparece como
    "The smarter E South America". Sem os apelidos, a feira mais chinesa do calendário
    entrava como prioridade genérica e ficava no fim da fila.
    """
    canonico = nome_canonico(evento.get("nome", ""))
    if canonico in config:
        return config[canonico]

    for feira in config.values():
        for apelido in feira.get("apelidos", []):
            if nome_canonico(apelido) == canonico:
                return feira

    for chave, feira in config.items():
        if chave and (chave in canonico or canonico in chave):
            return feira
    return {}


# --------------------------------------------------------------------------- agenda

def etapa_agenda(hoje: date | None = None) -> dict:
    hoje = hoje or date.today()
    eventos_tab = Tabela("eventos").carregar()
    fila = fila_mod.Fila()
    config = _config_feiras()

    coletados, situacao = agenda_mod.coletar_todos(hoje)

    novos = 0
    for evento in coletados:
        feira_config = _casar_config(evento, config)
        if feira_config:
            evento["prioridade"] = feira_config.get("prioridade", 5)
            evento["densidade_china"] = feira_config.get("densidade_china", "")
            evento["setor"] = feira_config.get("setor", "")
            if feira_config.get("site") and not evento.get("site"):
                evento["site"] = feira_config["site"]

        if eventos_tab.obter(evento["id"]) is None:
            novos += 1
        eventos_tab.upsert(evento)

    # Feira curada cuja única aparição na agenda é a edição que já passou: o
    # data_texto do config descreve a PRÓXIMA edição, então criamos esse evento.
    # Sem isto, bienais como FEICON e EXPOMAFE ficam eternamente "encerradas".
    from .core.datas import interpretar_periodo as _periodo
    from .core.modelos import chave_evento as _chave, novo_evento as _novo
    for feira in config.values():
        texto_data = feira.get("data_texto")
        if not texto_data:
            continue
        inicio_cfg, fim_cfg = _periodo(texto_data, hoje)
        if not inicio_cfg or encerrado(fim_cfg, hoje):
            continue
        id_proxima = _chave(feira["nome"], inicio_cfg[:4])
        if eventos_tab.obter(id_proxima) is not None:
            continue
        eventos_tab.upsert(_novo(
            id=id_proxima, nome=feira["nome"], site=feira.get("site", ""),
            pagina_expositores=feira.get("pagina_expositores", ""),
            data_inicio=inicio_cfg, data_fim=fim_cfg, data_texto=texto_data,
            local_nome=feira.get("local", ""), cidade=feira.get("cidade", ""),
            uf=feira.get("uf", ""), setor=feira.get("setor", ""),
            prioridade=feira.get("prioridade", 5),
            densidade_china=feira.get("densidade_china", ""),
            encerrado=False, fontes=["config/feiras_prioritarias.json"],
        ))
        novos += 1

    # Feiras curadas que não apareceram em nenhuma agenda entram assim mesmo.
    # A checagem tem que considerar os apelidos: a Intersolar aparece na agenda do
    # Expo Center Norte como "The smarter E South America", e sem isso criávamos um
    # segundo evento para a mesma feira.
    nomes_vistos = {nome_canonico(e.get("nome", "")) for e in eventos_tab.todos()}
    for canonico, feira in config.items():
        apelidos = {nome_canonico(a) for a in feira.get("apelidos", [])}
        if canonico in nomes_vistos or (apelidos & nomes_vistos):
            continue
        from .core.datas import interpretar_periodo
        from .core.modelos import chave_evento, novo_evento
        inicio, fim = interpretar_periodo(feira.get("data_texto", ""), hoje)
        eventos_tab.upsert(novo_evento(
            id=chave_evento(feira["nome"], inicio[:4] if inicio else None),
            nome=feira["nome"],
            data_inicio=inicio,
            data_fim=fim,
            data_texto=feira.get("data_texto", ""),
            encerrado=encerrado(fim, hoje) if fim else False,
            site=feira.get("site", ""),
            local_nome=feira.get("local", ""),
            cidade=feira.get("cidade", ""),
            uf=feira.get("uf", ""),
            setor=feira.get("setor", ""),
            prioridade=feira.get("prioridade", 5),
            densidade_china=feira.get("densidade_china", ""),
            descricao=feira.get("observacao", ""),
            fontes=["config/feiras_prioritarias.json"],
        ))
        novos += 1

    # Reavalia "encerrado" a cada rodada: o tempo passa mesmo sem o site mudar.
    # Quem a própria fonte marcou como expirado continua expirado — nossa data pode
    # estar um ano à frente por falta de ano no texto original.
    for evento in eventos_tab.todos():
        if evento.get("expirado_na_fonte"):
            evento["encerrado"] = True
        elif evento.get("data_fim"):
            evento["encerrado"] = encerrado(evento["data_fim"], hoje)

    # agenda tarefas de expositores só para o que ainda vai acontecer
    agendadas = 0
    for evento in eventos_tab.todos():
        if evento.get("encerrado"):
            continue
        faltam = dias_ate(evento.get("data_inicio", ""), hoje)
        if faltam is not None and faltam > JANELA_PROSPECCAO_DIAS:
            continue
        if not (evento.get("site") or evento.get("pagina_local")):
            continue
        prioridade = evento.get("prioridade", 5)
        # Feira que abre em dias vale mais que feira daqui a um ano, independente do
        # setor: depois que ela começa, o intérprete perdeu a janela de prospecção.
        if faltam is not None and faltam >= 0:
            if faltam <= 14:
                prioridade = 0
            elif faltam <= 45:
                prioridade = max(1, prioridade - 3)
            elif faltam <= 90:
                prioridade = max(2, prioridade - 1)
        fila.adicionar("expositores", evento["id"], prioridade=prioridade)
        agendadas += 1

    aplicar_overrides_manuais(eventos_tab, "eventos")
    eventos_tab.salvar()
    fila.salvar()

    return {
        "eventos_total": len(eventos_tab),
        "eventos_novos": novos,
        "encerrados": sum(1 for e in eventos_tab.todos() if e.get("encerrado")),
        "tarefas_agendadas": agendadas,
        "situacao_locais": situacao,
    }


# ---------------------------------------------------------------------- expositores

def etapa_expositores(limite: int | None = None, hoje: date | None = None) -> dict:
    hoje = hoje or date.today()
    eventos_tab = Tabela("eventos").carregar()
    empresas_tab = Tabela("empresas").carregar()
    participacoes_tab = Tabela("participacoes").carregar()
    fila = fila_mod.Fila()
    config = _config_feiras()

    tarefas = fila.proximas("expositores", limite)
    resumo = {"processadas": 0, "ok": 0, "bloqueadas": 0, "sem_lista": 0,
              "plataforma_nova": 0, "erro": 0, "expositores": 0, "chinesas": 0}

    for tarefa in tarefas:
        evento = eventos_tab.obter(tarefa["alvo"])
        if evento is None:
            fila.marcar_sem_dados("expositores", tarefa["alvo"], motivo="evento sumiu do store")
            continue
        if evento.get("encerrado"):
            fila.marcar_sem_dados("expositores", tarefa["alvo"], motivo="feira encerrada")
            continue

        resumo["processadas"] += 1
        feira_config = _casar_config(evento, config)
        resultado = expositores_mod.coletar(evento, feira_config)

        evento["plataforma"] = resultado.get("plataforma") or evento.get("plataforma", "")
        if resultado.get("pagina"):
            evento["pagina_expositores"] = resultado["pagina"]

        # a plataforma sabe a data oficial melhor que a agenda do centro de exposições
        datas = resultado.get("datas_evento") or {}
        if datas.get("data_inicio"):
            evento["data_inicio"] = datas["data_inicio"]
            evento["data_fim"] = datas.get("data_fim") or datas["data_inicio"]
            evento["encerrado"] = encerrado(evento["data_fim"], hoje)

        if resultado["status"] == expositores_mod.BLOQUEADO:
            resumo["bloqueadas"] += 1
            fila.marcar_adiado_local("expositores", tarefa["alvo"],
                                     motivo=resultado.get("detalhe", "bloqueado"))
            continue
        if resultado["status"] == expositores_mod.SEM_LISTA:
            resumo["sem_lista"] += 1
            fila.marcar_sem_dados("expositores", tarefa["alvo"],
                                  motivo=resultado.get("detalhe", ""))
            continue
        if resultado["status"] == expositores_mod.PLATAFORMA_NOVA:
            resumo["plataforma_nova"] += 1
            # Falha com backoff, não "sem dados": pode ser plataforma que ainda não
            # sabemos ler (aí precisa de adaptador), mas pode ser tropeço passageiro —
            # e marcar como "sem dados" congelaria a feira por duas semanas.
            fila.marcar_falha("expositores", tarefa["alvo"],
                              motivo=resultado.get("detalhe", "precisa de adaptador"))
            continue
        if resultado["status"] == expositores_mod.ERRO:
            resumo["erro"] += 1
            fila.marcar_falha("expositores", tarefa["alvo"],
                              motivo=resultado.get("detalhe", "erro"))
            continue

        chinesas_aqui = _guardar_expositores(
            resultado["expositores"], evento, empresas_tab, participacoes_tab, fila
        )
        evento["total_expositores"] = len(resultado["expositores"])
        evento["total_chinesas"] = chinesas_aqui
        resumo["ok"] += 1
        resumo["expositores"] += len(resultado["expositores"])
        resumo["chinesas"] += chinesas_aqui
        fila.marcar_ok("expositores", tarefa["alvo"],
                       resumo=f"{len(resultado['expositores'])} expositores, "
                              f"{chinesas_aqui} chinesas")

    eventos_tab.salvar()
    empresas_tab.salvar()
    participacoes_tab.salvar()
    fila.salvar()
    return resumo


def _guardar_expositores(expositores, evento, empresas_tab, participacoes_tab, fila) -> int:
    """Grava empresas e participações; devolve quantas são chinesas."""
    chinesas = 0
    for bruto in expositores:
        nome = (bruto.get("nome") or "").strip()
        if not nome:
            continue

        avaliacao = china_mod.avaliar(bruto)
        relevante = avaliacao["classificacao"] in (china_mod.CONFIRMADA, china_mod.PROVAVEL)

        identificador = chave_empresa(nome, bruto.get("website"))

        # A mesma empresa aparece em varias feiras, e nem toda feira publica pais ou
        # telefone. Se a feira mais pobre sobrescrevesse o score, uma empresa
        # confirmada viraria "nao chinesa" e sumiria da lista. Fica a melhor evidencia.
        ja_existente = empresas_tab.obter(identificador) or {}
        if ja_existente.get("score_china", 0) > avaliacao["score"]:
            avaliacao = {
                "score": ja_existente["score_china"],
                "classificacao": ja_existente.get("classificacao_china", ""),
                "origem": ja_existente.get("origem", ""),
                "motivos": ja_existente.get("motivos_deteccao", []),
            }
            relevante = avaliacao["classificacao"] in (china_mod.CONFIRMADA,
                                                       china_mod.PROVAVEL)

        empresa = nova_empresa(
            id=identificador,
            nome=nome,
            nome_zh=bruto.get("nome_zh", ""),
            nome_canonico=nome_canonico(nome),
            pais=bruto.get("pais", ""),
            cidade=bruto.get("cidade", ""),
            provincia=bruto.get("provincia", ""),
            endereco=bruto.get("endereco", ""),
            website=normalizar_url(bruto.get("website", "")),
            contato_nome=bruto.get("contato_nome", ""),
            emails=[e for e in bruto.get("emails", []) if e],
            produtos=bruto.get("produtos", []),
            setor=evento.get("setor", ""),
            descricao=bruto.get("descricao", ""),
            perfis=bruto.get("perfis", {}),
            # porte vem preenchido quando a plataforma publica (TradeChina publica)
            funcionarios=bruto.get("funcionarios", ""),
            ano_fundacao=bruto.get("ano_fundacao", ""),
            receita_anual=bruto.get("receita_anual", ""),
            tipo_negocio=bruto.get("tipo_negocio", ""),
            score_china=avaliacao["score"],
            classificacao_china=avaliacao["classificacao"],
            origem=avaliacao["origem"],
            motivos_deteccao=avaliacao["motivos"],
            fontes=[bruto.get("fonte_url") or bruto.get("fonte_plataforma", "")],
        )
        empresas_tab.upsert(empresa)

        participacoes_tab.upsert({
            "id": chave_participacao(identificador, evento["id"]),
            "empresa_id": identificador,
            "evento_id": evento["id"],
            "evento_nome": evento.get("nome", ""),
            "evento_inicio": evento.get("data_inicio", ""),
            "evento_fim": evento.get("data_fim", ""),
            "local_nome": evento.get("local_nome", ""),
            "cidade": evento.get("cidade", ""),
            "uf": evento.get("uf", ""),
            "stand": bruto.get("stand", ""),
            "ficha_feira": bruto.get("ficha_feira", ""),
            "atualizado_em": agora_iso(),
        })

        if relevante:
            chinesas += 1
            prioridade = 2 if avaliacao["classificacao"] == china_mod.CONFIRMADA else 4
            if bruto.get("website"):
                fila.adicionar("site_empresa", identificador, prioridade=prioridade,
                               dados={"website": bruto["website"]})
            else:
                # sem site não dá para visitar: procura porte e contato nas bases chinesas
                fila.adicionar("enriquecer_empresa", identificador, prioridade=prioridade + 1,
                               dados={"nome": nome})
    return chinesas


# ----------------------------------------------------------------------- enriquecer

def etapa_enriquecer(limite: int | None = None) -> dict:
    from .fontes.enriquecimento import site_empresa

    empresas_tab = Tabela("empresas").carregar()
    fila = fila_mod.Fila()

    tarefas = fila.proximas("site_empresa", limite)
    resumo = {"processadas": 0, "com_email": 0, "com_wechat": 0,
              "adiadas": 0, "falhas": 0, "sem_dados": 0}

    for tarefa in tarefas:
        empresa = empresas_tab.obter(tarefa["alvo"])
        if empresa is None:
            fila.marcar_sem_dados("site_empresa", tarefa["alvo"], motivo="empresa sumiu")
            continue

        site = (tarefa.get("dados") or {}).get("website") or empresa.get("website")
        if not site:
            fila.marcar_sem_dados("site_empresa", tarefa["alvo"], motivo="sem site")
            resumo["sem_dados"] += 1
            continue

        resumo["processadas"] += 1
        try:
            achado = site_empresa.enriquecer(site)
        except Bloqueado as exc:
            resumo["adiadas"] += 1
            fila.marcar_adiado_local("site_empresa", tarefa["alvo"], motivo=exc.motivo)
            continue
        except FalhouDeVerdade as exc:
            resumo["falhas"] += 1
            fila.marcar_falha("site_empresa", tarefa["alvo"], motivo=exc.motivo)
            continue
        except Exception as exc:  # noqa: BLE001
            resumo["falhas"] += 1
            fila.marcar_falha("site_empresa", tarefa["alvo"],
                              motivo=f"{type(exc).__name__}: {exc}")
            continue

        empresas_tab.upsert({
            "id": empresa["id"],
            "emails": achado["emails"],
            "telefones": achado["telefones"],
            "whatsapps": achado["whatsapps"],
            "wechat": achado["wechat"] or empresa.get("wechat", ""),
            "website_cn": achado["website_cn"] or empresa.get("website_cn", ""),
            "enriquecida_em": agora_iso(),
            "fontes": ["site_empresa"],
        })
        if achado["emails"]:
            resumo["com_email"] += 1
        if achado["wechat"]:
            resumo["com_wechat"] += 1

        fila.marcar_ok("site_empresa", tarefa["alvo"],
                       resumo=f"{len(achado['emails'])} e-mails, "
                              f"{len(achado['telefones'])} telefones")

    resumo.update(_enriquecer_sem_site(empresas_tab, fila, limite))

    aplicar_overrides_manuais(empresas_tab, "empresas")
    empresas_tab.salvar()
    fila.salvar()
    return resumo


def _enriquecer_sem_site(empresas_tab, fila, limite: int | None) -> dict:
    """Empresas que a feira listou sem site: procura num diretório B2B chinês.

    Só grava quando o nome bate exatamente. Um contato de empresa parecida seria pior
    que nenhum — o intérprete abordaria a fábrica errada achando que é a expositora.
    """
    from .core.perfil import pode_executar
    from .fontes.enriquecimento import diretorios_b2b

    resumo = {"por_nome_processadas": 0, "por_nome_confirmadas": 0,
              "por_nome_so_parecidos": 0, "por_nome_adiadas": 0}

    tarefas = fila.proximas("enriquecer_empresa", limite)
    if tarefas and not pode_executar(diretorios_b2b.REQUER_RESIDENCIAL):
        # na nuvem esses diretórios barram: devolve tudo para a rodada local de uma vez
        for tarefa in tarefas:
            fila.marcar_adiado_local("enriquecer_empresa", tarefa["alvo"],
                                     motivo="diretório B2B exige IP residencial")
        resumo["por_nome_adiadas"] = len(tarefas)
        return resumo

    for tarefa in tarefas:
        empresa = empresas_tab.obter(tarefa["alvo"])
        if empresa is None:
            fila.marcar_sem_dados("enriquecer_empresa", tarefa["alvo"], motivo="empresa sumiu")
            continue

        nome = empresa.get("nome", "")
        resumo["por_nome_processadas"] += 1
        try:
            achado = diretorios_b2b.enriquecer_por_nome(nome)
        except Bloqueado as exc:
            resumo["por_nome_adiadas"] += 1
            fila.marcar_adiado_local("enriquecer_empresa", tarefa["alvo"], motivo=exc.motivo)
            continue
        except FalhouDeVerdade as exc:
            fila.marcar_falha("enriquecer_empresa", tarefa["alvo"], motivo=exc.motivo)
            continue
        except Exception as exc:  # noqa: BLE001
            fila.marcar_falha("enriquecer_empresa", tarefa["alvo"],
                              motivo=f"{type(exc).__name__}: {exc}")
            continue

        if not achado.get("encontrado"):
            resumo["por_nome_so_parecidos"] += 1
            fila.marcar_sem_dados("enriquecer_empresa", tarefa["alvo"],
                                  motivo=achado.get("motivo", "sem correspondência exata"))
            continue

        empresas_tab.upsert({
            "id": empresa["id"],
            "funcionarios": achado.get("funcionarios", ""),
            "ano_fundacao": achado.get("ano_fundacao", ""),
            "tipo_negocio": achado.get("tipo_negocio", ""),
            "provincia": achado.get("provincia", "") or empresa.get("provincia", ""),
            "produtos": achado.get("produtos", []),
            "perfis": {"made_in_china": achado.get("perfil_mic", "")},
            "nome_confirmado_em": achado.get("fonte", ""),
            "enriquecida_em": agora_iso(),
            "fontes": ["made_in_china"],
        })
        resumo["por_nome_confirmadas"] += 1
        fila.marcar_ok("enriquecer_empresa", tarefa["alvo"],
                       resumo=f"confirmada em {achado.get('fonte')}: "
                              f"{achado.get('funcionarios') or 'porte n/d'}")
    return resumo


# -------------------------------------------------------------------------- resumo

def situacao_geral() -> dict:
    eventos = Tabela("eventos").carregar()
    empresas = Tabela("empresas").carregar()
    participacoes = Tabela("participacoes").carregar()
    fila = fila_mod.Fila()

    relevantes = [
        e for e in empresas.todos()
        if e.get("classificacao_china") in (china_mod.CONFIRMADA, china_mod.PROVAVEL)
    ]
    return {
        "ambiente": ambiente_atual().value,
        "na_nuvem": na_nuvem(),
        "eventos": len(eventos),
        "eventos_futuros": sum(1 for e in eventos.todos() if not e.get("encerrado")),
        "empresas": len(empresas),
        "empresas_china": len(relevantes),
        "com_email": sum(1 for e in relevantes if e.get("emails")),
        "com_wechat": sum(1 for e in relevantes if e.get("wechat")),
        "com_funcionarios": sum(1 for e in relevantes if e.get("funcionarios")),
        "participacoes": len(participacoes),
        "fila": fila.resumo(),
    }
