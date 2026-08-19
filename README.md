# Tradutor Mandarim BR

Plataforma que ajuda **intérpretes de mandarim** a encontrar trabalho presencial em feiras
de negócios no Brasil, indo direto na origem da demanda: as **empresas chinesas expositoras**.

O robô descobre quais feiras vão acontecer, quais empresas chinesas expõem em cada uma,
em que estande elas estarão e como falar com elas (e-mail, telefone, WeChat, WhatsApp,
site chinês, porte da empresa). O intérprete filtra por feira, setor ou data, copia o
contato e oferece o serviço antes do evento.

---

## Como usar no dia a dia

```powershell
# Rodada completa (agenda, expositores, enriquecimento, exportação)
python -m scraper.cli tudo

# Só o que a nuvem não conseguiu — este é o comando do seu PC
python -m scraper.cli pendentes

# Ver o estado do banco e da fila
python -m scraper.cli situacao

# Diagnosticar uma feira nova (qual plataforma ela usa?)
python -m scraper.cli investigar https://www.exemplo.com.br/

# Entrar no TradeChina com a sua conta (uma vez só)
python -m scraper.cli login
```

Para ver o site localmente:

```powershell
python -m http.server 8000 --directory docs
# abra http://localhost:8000
```

Abrir o `index.html` direto pelo `file://` **não funciona** — o navegador bloqueia o
`fetch()` dos JSON locais.

---

## A ideia central: nuvem e PC se completam

Muitos sites que interessam (Alibaba, TradeChina, feiras com Cloudflare) bloqueiam IPs
de datacenter, mas atendem normalmente um IP residencial brasileiro. Em vez de escolher
entre nuvem e PC, o projeto usa os dois:

```
03:00, GitHub Actions        →  coleta tudo que funciona de IP de nuvem.
   (MJ_AMBIENTE=nuvem)          O que der 403/CAPTCHA NÃO vira erro:
                                a tarefa é marcada "adiado_local" na fila.
                                       │
                                       ▼  (dados versionados em data/*.jsonl)
quando você rodar, no seu PC  →  `atualizar.ps1` dá git pull, pega exatamente
   (MJ_AMBIENTE=local)           as tarefas adiadas e completa com seu IP,
                                 depois commita e publica.
```

O detalhe que faz isso funcionar é o cliente HTTP distinguir **"a página mudou"** de
**"fui bloqueado"** ([scraper/core/http.py](scraper/core/http.py)). Bloqueio não conta
como falha e não gasta tentativa — a empresa continua na fila, só esperando outro IP.

Agende a rodada local no Windows (uma vez, como administrador):

```powershell
$acao    = New-ScheduledTaskAction -Execute "powershell.exe" `
           -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\atualizar.ps1`""
$gatilho = New-ScheduledTaskTrigger -Daily -At 19:00
Register-ScheduledTask -TaskName "MandarimJob - coleta local" -Action $acao -Trigger $gatilho
```

---

## Estrutura

```
config/
  feiras_prioritarias.json   # ← você edita: quais feiras merecem esforço

scraper/
  cli.py                     # ponto de entrada de tudo
  pipeline.py                # as 4 etapas: agenda → expositores → enriquecer → exportar
  core/
    http.py                  # requisições, cache, throttle, Bloqueado vs FalhouDeVerdade
    store.py                 # armazenamento acumulativo em JSONL (upsert, nunca perde dado)
    fila.py                  # tarefas com estado; é o que liga nuvem e PC
    perfil.py                # detecta se estamos na nuvem ou no seu PC
    modelos.py               # identidade das empresas (dedupe) e formato dos registros
    datas.py                 # interpreta "27 - 30 jan" e decide o que já encerrou
    navegador.py             # Chrome com sessão salva, para CAPTCHA/login
  fontes/
    locais/agenda.py         # agendas: SP Expo, Anhembi, Expo Center Norte, Riocentro
    plataformas/
      swapcard.py            # Informa Markets, Agrishow  (GraphQL)
      rx.py                  # RX/Reed: FEICON, FEBRAVA, FENATRAN, AUTOMEC  (Algolia)
      descoberta.py          # acha a lista de expositores em camadas
      detectar.py            # identifica a plataforma de uma feira nova
    enriquecimento/
      site_empresa.py        # e-mail, telefone, WeChat no site da própria empresa
      tradechina.py          # porte: funcionários, ano, capital, faturamento
    expositores.py           # roteador: escolhe o adaptador certo
  deteccao/china.py          # pontuação de "é empresa chinesa?" com motivos
  export/site.py             # gera os JSON que o site consome

data/                        # banco versionado (JSONL, uma linha por registro)
  eventos.jsonl  empresas.jsonl  participacoes.jsonl  fila.jsonl
  manual/                    # suas correções à mão — nunca sobrescritas
docs/                        # site estático publicado no GitHub Pages
```

### Por que JSONL versionado e não SQLite

Nuvem e PC sincronizam pelo Git. Um `.db` binário não faz merge: duas rodadas no mesmo
dia viram conflito insolúvel. Um JSONL ordenado por id gera diff limpo, e o Git resolve
sozinho quase sempre.

### Por que acumulativo

Achar o e-mail e o WeChat de uma fábrica chinesa custa muitas requisições. O pipeline
antigo regenerava tudo do zero a cada rodada e jogava fora esse trabalho. Agora cada
rodada faz *upsert*: **campo preenchido nunca é apagado por campo vazio**, listas de
e-mails se somam, e o que você corrigir em `data/manual/` sempre vence.

---

## Como as empresas chinesas são identificadas

[scraper/deteccao/china.py](scraper/deteccao/china.py) dá **pontos** em vez de responder
sim ou não, e registra o motivo de cada ponto — o site mostra isso para você conferir.

| Sinal | Pontos |
|---|---|
| País do expositor = China | 6 |
| Nome em caracteres chineses | 6 |
| Domínio `.cn` | 5 |
| Telefone +86 | 5 |
| E-mail em 163/QQ/.cn | 4 |
| Província chinesa citada | 4 |
| Cidade chinesa citada | 3–4 |
| Pavilhão chinês / CCPIT | 4 |
| Menção genérica a "China" | 2 |

- **6+ = confirmada**, **3–5 = provável** → entram na lista principal
- **1–2 = suspeita** → vão para a aba "Revisar duvidosas", fora do caminho

A versão anterior marcava qualquer coisa com "china" no texto. Isso pegava "CHINA GLASS"
(nome de um pavilhão) e perdia "Zhejiang Chint Electric", que não tem a palavra China em
lugar nenhum. A pontuação resolve os dois casos.

---

## Adicionando uma feira nova

1. Rode o diagnóstico:

   ```powershell
   python -m scraper.cli investigar https://site-da-feira.com.br/
   ```

2. Se a saída disser `swapcard` ou `rx`, não há nada a fazer: já temos adaptador.
   Basta adicionar a feira em [config/feiras_prioritarias.json](config/feiras_prioritarias.json).

3. Se disser outra coisa, a lista existe mas ninguém sabe lê-la. Aí é escrever um
   adaptador novo em `scraper/fontes/plataformas/`, seguindo o modelo do `swapcard.py`.

4. Se a descoberta errar a URL da lista, fixe-a à mão no config, no campo
   `pagina_expositores` — a URL fixada sempre vence a heurística.

### Plataformas já cobertas

| Plataforma | Como funciona | Feiras |
|---|---|---|
| **TradeChina / Meorient** | API de busca de fornecedores, por `exhibition_id` | China Homelife Machinex e irmãs — 1.934 empresas, todas chinesas |
| **Swapcard** | GraphQL público em `/api/graphql` | Intermodal, Agrishow, Plástico Brasil, e o resto da Informa Markets |
| **RX / Reed** | índice Algolia, chave lida da própria página | AUTOMEC, FENATRAN, FEICON, FEBRAVA |
| genérico | listagem repetida em HTML | feiras em WordPress/Webflow |

---

## Radar de feiras novas (imprensa do setor)

```powershell
python -m scraper.cli radar
```

As agendas dos centros de exposição só mostram o que já está contratado, e só cobrem os
locais que monitoramos. O [portaleventos.com.br](https://www.portaleventos.com.br/) é um
site de notícias do setor — não tem lista de expositores, mas anuncia feira nova antes de
todo mundo e cobre o Brasil inteiro. Foi assim que apareceram **Feicon Rio** e
**Febrava Rio** (6–8/out, Riocentro), que não estavam em nenhuma agenda nossa.

Por ser texto livre de notícia, o resultado vai para `data/radar_feiras.jsonl` como
**pista para revisão**, nunca direto para o calendário — sai ruído junto (manchete que
não é feira), e promover isso automaticamente sujaria os dados. Você olha a lista e move
o que interessa para `config/feiras_prioritarias.json`.

## TradeChina (China Homelife e as feiras 100% chinesas)

As feiras da Meorient — **China Homelife Brazil**, China Machinery, Appliance &
Electronics Show, Decoration & Furniture, Building, INTEX — acontecem no São Paulo Expo e
são compostas **só por expositores chineses**. É a maior concentração de clientes
possíveis num único lugar.

**A lista de expositores é coletada por API, sem navegador e sem login.** O site
tradechina.com fica atrás do CAPTCHA do Tencent EdgeOne, mas a busca de fornecedores é
servida por outro host (`global-all-api.tradechina.com`), que responde a requisição
normal — inclusive da nuvem. Um único `exhibition_id` cobre todas as feiras irmãs.

O que vem daí é o melhor conjunto de dados do projeto, porque é a própria plataforma que
cadastra o fornecedor: **nome em chinês**, nome comercial, cidade, **faixa de
funcionários**, ano de fundação, certificações e produtos em português.

Para ler a *ficha individual* de um fornecedor (com capital registrado e faturamento),
aí sim é preciso passar pelo CAPTCHA. O projeto lida com isso **sem burlar nada**:

- `python -m scraper.cli login` abre um Chrome de verdade, visível;
- você resolve o CAPTCHA e entra com a **sua** conta;
- a sessão fica em `data/_sessao_navegador/`, que está no `.gitignore` e **nunca** vai
  para o GitHub nem roda no Actions;
- as rodadas seguintes leem as mesmas páginas que você veria no seu navegador.

Respeitamos o `robots.txt` deles, que proíbe URLs com query string — só lemos caminhos
limpos como `/supplier/<empresa>_<id>.html`.

Dessas fichas sai o **porte da empresa**, que é público e vem em português:

```
Total de Empregados   5-10          Ano Estabelecido   2019
Capital Registrado    RMB 800.000   Faturamento anual  US$ 1–3 milhões
Localização           Foshan, Guangdong
```

> **Atenção:** automatizar acesso com conta própria pode contrariar os termos de uso da
> plataforma, com risco de suspensão da sua conta. O uso é moderado e com pausas, mas a
> decisão de usar esse caminho é sua.

---

## A interface

Página única em `docs/`, sem servidor e sem login, publicada de graça no GitHub Pages.
Filtros disponíveis:

- busca livre (nome, produto, cidade, e-mail, estande)
- feira, estado, setor, **porte** (grande / média / pequena), origem (China / Taiwan / HK)
- **ocultar feiras encerradas** (ligado por padrão — feira que já passou não gera trabalho)
- só empresas com contato pronto
- ocultar as que você já contatou
- só chinesas confirmadas
- ordenar por feira mais próxima, nome ou porte

Cada intérprete marca **"já contatei"** e escreve anotações; isso fica no `localStorage`
do próprio navegador — ninguém mais vê, nada é enviado para servidor. O botão
**baixar CSV** exporta a lista filtrada já com suas marcações.

---

## Publicando no GitHub Pages

1. Suba o repositório para o GitHub (público, plano gratuito).
2. **Settings → Pages** → Source: *Deploy from a branch* → branch `main`, pasta `/docs`.
3. Em **Settings → Actions → General**, marque *Read and write permissions* para o
   workflow poder commitar os dados.
4. O site fica em `https://<seu-usuario>.github.io/<repo>/`.

---

## Limites conhecidos

- **Nem toda feira publica a lista de expositores.** Muitas só divulgam perto da data;
  o robô volta a tentar sozinho conforme o evento se aproxima.
- **Contato ainda é o elo mais fraco.** Temos 2.045 empresas, mas poucas com e-mail:
  a maioria das feiras não publica contato, e o e-mail vem de visitar o site da empresa
  (1.703 têm site próprio, e a coleta roda aos poucos, na fila).
- **Busca por nome em diretório B2B rende pouco** (~3%): o Made-in-China raramente tem a
  empresa exata, e quando tem, nem sempre publica o porte. Está implementado com
  casamento estrito de nome — nunca aceita empresa parecida —, mas não é a via principal.
- **A detecção erra às vezes.** Por isso cada empresa mostra por que foi marcada, e as
  duvidosas ficam numa aba separada.
- **A agenda do Riocentro ainda retorna vazia** — o seletor do site mudou e precisa de ajuste.
- Os dados são ponto de partida: confirme o contato antes de abordar.

## Postura de coleta

- Só lemos páginas públicas; não contornamos paywall nem quebramos proteção.
- Respeitamos `robots.txt` e mantemos pausa entre requisições ao mesmo domínio.
- Onde há login, usamos a conta do próprio usuário, localmente, nunca no CI.
- Se um site pedir para parar, o adaptador correspondente deve ser removido.
