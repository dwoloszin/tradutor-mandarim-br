"""Testes das partes que, se quebrarem em silêncio, estragam os dados sem avisar.

Não testamos os scrapers contra a internet (isso muda todo dia e não é culpa nossa).
Testamos a lógica que decide o que é dado bom: mesclagem sem perda, identidade de
empresa, leitura de datas e pontuação de origem chinesa.

Rodar:  python testes.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scraper.core.datas import dias_ate, encerrado, interpretar_periodo  # noqa: E402
from scraper.core.modelos import (  # noqa: E402
    chave_empresa,
    chave_feira,
    dominio_proprio,
    nome_canonico,
)
from scraper.core.store import Tabela, mesclar  # noqa: E402
from scraper.deteccao.china import avaliar  # noqa: E402

falhas: list[str] = []


def conferir(condicao: bool, descricao: str) -> None:
    if condicao:
        print(f"  ok   {descricao}")
    else:
        print(f"  FALHOU  {descricao}")
        falhas.append(descricao)


# --------------------------------------------------------------- store

print("\nstore — não perder dado bom")

r = mesclar({"id": "x", "emails": ["a@x.cn"], "wechat": "abc123"},
            {"id": "x", "emails": [], "wechat": ""})
conferir(r["emails"] == ["a@x.cn"], "campo vazio não apaga e-mail já coletado")
conferir(r["wechat"] == "abc123", "campo vazio não apaga WeChat já coletado")

r = mesclar({"id": "x", "emails": ["a@x.cn"], "fontes": ["feira"]},
            {"id": "x", "emails": ["b@x.cn"], "fontes": ["site"]})
conferir(r["emails"] == ["a@x.cn", "b@x.cn"], "e-mails de fontes diferentes se somam")
conferir(r["fontes"] == ["feira", "site"], "fontes se acumulam")

r = mesclar({"id": "x", "manual_email": "chefe@x.cn"}, {"id": "x", "manual_email": ""})
conferir(r["manual_email"] == "chefe@x.cn", "correção manual sobrevive à rodada seguinte")

with tempfile.TemporaryDirectory() as pasta:
    caminho = Path(pasta)
    t = Tabela("t", caminho).carregar()
    t.upsert({"id": "b", "nome": "Beta"})
    t.upsert({"id": "a", "nome": "Alfa"})
    t.salvar()
    linhas = (caminho / "t.jsonl").read_text(encoding="utf-8").strip().split("\n")
    conferir(linhas[0].startswith('{"atualizado_em'), "JSONL com chaves ordenadas")
    conferir('"id": "a"' in linhas[0], "registros ordenados por id (diff estável no Git)")

    t2 = Tabela("t", caminho).carregar()
    carimbo = t2.obter("a")["atualizado_em"]
    t2.upsert({"id": "a", "nome": "Alfa"})
    conferir(t2.obter("a")["atualizado_em"] == carimbo,
             "upsert idêntico não carimba data nova (evita commit vazio todo dia)")

# --------------------------------------------------------------- identidade

print("\nmodelos — juntar a mesma empresa escrita de formas diferentes")

conferir(
    chave_empresa("GUANGDONG AURICAN HARDWARE TECHNOLOGY CO., LTD.")
    == chave_empresa("Guangdong Aurican Hardware Technology Co.,Ltd"),
    "maiúsculas e pontuação não criam empresa duplicada",
)
conferir(
    chave_empresa("JinkoSolar", "https://www.jinkosolar.com/pt/")
    == chave_empresa("Jinko Solar Co., Ltd", "http://jinkosolar.com"),
    "mesmo domínio junta nomes diferentes",
)
conferir(dominio_proprio("https://www.alibaba.com/company/x.html") == "",
         "perfil de Alibaba não vira identidade da empresa")
conferir(chave_feira("41ª ABIMAD") == chave_feira("ABIMAD"),
         "número da edição não cria feira nova")
conferir(nome_canonico("Shanghai Best Co., Ltd") == "SHANGHAI BEST",
         "sufixo societário sai do nome canônico")

# --------------------------------------------------------------- datas

print("\ndatas — saber o que já encerrou")

hoje = date(2026, 8, 19)
conferir(interpretar_periodo("16 - 18 set. 2026", hoje) == ("2026-09-16", "2026-09-18"),
         "período com ano explícito")
conferir(interpretar_periodo("27 - 30 jan", hoje)[0] == "2027-01-27",
         "mês que já passou vai para o ano seguinte")
conferir(interpretar_periodo("29 set a 02 out", hoje) == ("2026-09-29", "2026-10-02"),
         "período que atravessa dois meses")
conferir(encerrado("2026-08-07", hoje) and not encerrado("2026-09-16", hoje),
         "encerrado compara com a data de hoje")
conferir(not encerrado("", hoje),
         "sem data, a feira aparece (melhor mostrar do que esconder)")
conferir(dias_ate("2026-09-16", hoje) == 28, "contagem de dias até a feira")

# --------------------------------------------------------------- detecção

print("\ndetecção — achar as chinesas sem encher de falso positivo")

conferir(avaliar({"nome": "Aurican Hardware", "pais": "China"})["classificacao"] == "confirmada",
         "país China basta para confirmar")
conferir(avaliar({"nome": "浙江正泰电器股份有限公司"})["classificacao"] == "confirmada",
         "nome em chinês confirma")
conferir(avaliar({"nome": "Zhejiang Chint Electric Co., Ltd"})["classificacao"] == "provavel",
         "pinyin de província + Co.,Ltd é provável (não tem a palavra 'china')")
conferir(avaliar({"nome": "CHINA GLASS"})["classificacao"] == "suspeita",
         "menção solta a 'china' fica só como suspeita, fora da lista principal")
conferir(avaliar({"nome": "Tramontina S.A.", "pais": "Brasil"})["classificacao"] == "nao",
         "empresa brasileira não entra")
conferir(avaliar({"nome": "Taiwan Precision", "pais": "Taiwan"})["origem"] == "taiwan",
         "Taiwan é marcada com origem própria")
conferir(len(avaliar({"nome": "Foshan Nanhai", "telefones": ["+86 757 8888 9999"]})["motivos"]) >= 2,
         "os motivos da detecção ficam registrados para conferência")

# --------------------------------------------------------------- fila

print("\nfila — nuvem e PC se completam")

import os  # noqa: E402

os.environ["MJ_AMBIENTE"] = "nuvem"
for modulo in [m for m in list(sys.modules) if m.startswith("scraper.core")]:
    del sys.modules[modulo]
from scraper.core.fila import Fila  # noqa: E402
from scraper.core.store import DATA_DIR  # noqa: E402

with tempfile.TemporaryDirectory() as pasta:
    import scraper.core.store as store_mod
    original = store_mod.DATA_DIR
    store_mod.DATA_DIR = Path(pasta)
    try:
        f = Fila()
        f.adicionar("site_empresa", "emp:1")
        f.adicionar("site_empresa", "emp:2")
        conferir(len(f.proximas("site_empresa")) == 2, "tarefas novas são executáveis")

        from scraper.core.fila import id_tarefa
        f.marcar_adiado_local("site_empresa", "emp:1", motivo="HTTP 403")
        conferir(len(f.proximas("site_empresa")) == 1,
                 "na nuvem, tarefa bloqueada sai da fila executável")

        bloqueada = f.tabela.obter(id_tarefa("site_empresa", "emp:1"))
        conferir(bloqueada["tentativas"] == 0,
                 "bloqueio não gasta tentativa (a empresa não se perde por falta de IP)")
        conferir(bloqueada["estado"] == "adiado_local",
                 "bloqueio marca adiado_local, não falha")

        os.environ["MJ_AMBIENTE"] = "local"
        conferir(len(f.proximas("site_empresa")) == 2,
                 "no PC, a tarefa adiada volta para a fila")
    finally:
        store_mod.DATA_DIR = original

# --------------------------------------------------------------- resultado

print()
if falhas:
    print(f"{len(falhas)} teste(s) falharam:")
    for f_ in falhas:
        print("   -", f_)
    raise SystemExit(1)
print("todos os testes passaram")
