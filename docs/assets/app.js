/* Tradutor Mandarim BR — aplicação de página única.
 *
 * Tudo roda no navegador: baixa os JSON gerados pelo scraper e filtra na memória.
 * Sem servidor, sem login, sem custo — e o estado de prospecção de cada intérprete
 * (quem já contatei, minhas notas) fica no localStorage do próprio navegador,
 * então uma pessoa não vê nem atrapalha o trabalho da outra.
 */

const estado = {
  empresas: [],
  feiras: [],
  meta: {},
  revisao: [],
  revisaoCarregada: false,
  aba: 'empresas',
  marcacoes: carregarMarcacoes(),
};

/* ----------------------------------------------------------- estado local */

const CHAVE_ARMAZENAMENTO = 'mandarim-br:prospeccao:v1';

function carregarMarcacoes() {
  try {
    return JSON.parse(localStorage.getItem(CHAVE_ARMAZENAMENTO) || '{}');
  } catch (e) {
    return {};
  }
}

function salvarMarcacoes() {
  try {
    localStorage.setItem(CHAVE_ARMAZENAMENTO, JSON.stringify(estado.marcacoes));
  } catch (e) {
    console.warn('não consegui salvar suas marcações', e);
  }
}

function marcacao(id) {
  return estado.marcacoes[id] || { contatado: false, nota: '' };
}

function alternarContatado(id) {
  const atual = marcacao(id);
  estado.marcacoes[id] = { ...atual, contatado: !atual.contatado };
  salvarMarcacoes();
  renderizar();
}

function anotar(id, texto) {
  estado.marcacoes[id] = { ...marcacao(id), nota: texto };
  salvarMarcacoes();
}

/* ----------------------------------------------------------- utilidades */

function escapar(texto) {
  return String(texto == null ? '' : texto)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* Um endereco sem "https://" vira link relativo e o navegador abre
 * "https://nosso-site/www.empresa.com". Blindamos aqui tambem, para o site nao
 * depender de o coletor ter gravado certo. */
function urlAbsoluta(url) {
  const limpo = String(url || '').trim();
  if (!limpo) return '';
  if (/^https?:\/\//i.test(limpo)) return limpo;
  if (limpo.startsWith('//')) return 'https:' + limpo;
  if (limpo.includes('@') || !limpo.includes('.')) return '';
  return 'https://' + limpo.replace(/^\/+/, '');
}

function formatarData(iso) {
  if (!iso) return 'data a confirmar';
  const [ano, mes, dia] = iso.split('-');
  return `${dia}/${mes}/${ano}`;
}

function textoPrazo(dias) {
  if (dias == null) return '';
  if (dias < 0) return 'já aconteceu';
  if (dias === 0) return 'é hoje';
  if (dias === 1) return 'amanhã';
  if (dias <= 45) return `em ${dias} dias`;
  return '';
}

async function copiar(texto, elemento) {
  try {
    await navigator.clipboard.writeText(texto);
  } catch (e) {
    // navegadores sem permissão de área de transferência: seleção manual
    const campo = document.createElement('textarea');
    campo.value = texto;
    document.body.appendChild(campo);
    campo.select();
    document.execCommand('copy');
    campo.remove();
  }
  if (elemento) {
    elemento.classList.add('copiado');
    const original = elemento.textContent;
    elemento.textContent = '✓ copiado';
    setTimeout(() => {
      elemento.classList.remove('copiado');
      elemento.textContent = original;
    }, 1200);
  }
}

/* ----------------------------------------------------------- filtros */

function lerFiltros() {
  const v = (id) => (document.getElementById(id) || {}).value || '';
  const m = (id) => !!(document.getElementById(id) || {}).checked;
  return {
    busca: v('f-busca').trim().toLowerCase(),
    feira: v('f-feira'),
    uf: v('f-uf'),
    setor: v('f-setor'),
    origem: v('f-origem'),
    porte: v('f-porte'),
    ordem: v('f-ordem'),
    ocultarEncerradas: m('f-ocultar-encerradas'),
    soComContato: m('f-so-contato'),
    ocultarContatados: m('f-ocultar-contatados'),
    soConfirmadas: m('f-so-confirmadas'),
  };
}

/* Converte "51-100 pessoas", "mais de 1000 pessoas", "menos de 5 pessoas" no maior
 * numero da faixa. Usamos o teto porque o interesse do interprete e saber ate onde a
 * empresa chega de tamanho: quem tem "301-500" e um cliente diferente de quem tem "5-10".
 */
function tetoFuncionarios(texto) {
  if (!texto) return null;
  const numeros = String(texto).match(/\d+/g);
  if (!numeros) return null;
  return Math.max(...numeros.map(Number));
}

function portePassa(empresa, escolha) {
  if (!escolha) return true;
  const teto = tetoFuncionarios(empresa.funcionarios);
  if (teto == null) return false;           // sem porte informado, fica fora de qualquer faixa
  if (escolha === 'informado') return true;
  if (escolha === 'grande') return teto > 300;
  if (escolha === 'media') return teto > 50 && teto <= 300;
  if (escolha === 'pequena') return teto <= 50;
  return true;
}

function empresaPassa(empresa, filtros) {
  // O pedido central: feira que já aconteceu não serve para oferecer serviço.
  // Escondemos a empresa cuja única participação é passada.
  if (filtros.ocultarEncerradas && !empresa.tem_feira_futura) return false;
  if (filtros.soComContato && !empresa.tem_contato) return false;
  if (filtros.soConfirmadas && empresa.classificacao !== 'confirmada') return false;
  if (filtros.ocultarContatados && marcacao(empresa.id).contatado) return false;
  if (filtros.origem && empresa.origem !== filtros.origem) return false;
  if (filtros.setor && empresa.setor !== filtros.setor) return false;
  if (!portePassa(empresa, filtros.porte)) return false;

  const feirasVisiveis = filtros.ocultarEncerradas
    ? empresa.feiras.filter((f) => !f.encerrada)
    : empresa.feiras;

  if (filtros.feira && !feirasVisiveis.some((f) => f.evento_id === filtros.feira)) return false;
  if (filtros.uf && !feirasVisiveis.some((f) => f.uf === filtros.uf)) return false;

  if (filtros.busca) {
    const alvo = [
      empresa.nome, empresa.nome_zh, empresa.cidade, empresa.provincia,
      empresa.setor, (empresa.produtos || []).join(' '),
      (empresa.emails || []).join(' '), empresa.website, empresa.descricao,
      feirasVisiveis.map((f) => f.nome + ' ' + f.stand).join(' '),
    ].join(' ').toLowerCase();
    if (!alvo.includes(filtros.busca)) return false;
  }
  return true;
}

function ordenar(lista, ordem) {
  const copia = [...lista];
  if (ordem === 'nome') {
    copia.sort((a, b) => a.nome.localeCompare(b.nome, 'pt-BR'));
  } else if (ordem === 'porte') {
    const peso = (e) => {
      const teto = tetoFuncionarios(e.funcionarios);
      return teto == null ? 1 : -teto;   // maiores primeiro; sem porte, por último
    };
    copia.sort((a, b) => peso(a) - peso(b));
  } else {
    // padrão: urgência — quem expõe primeiro, e com contato disponível, no topo
    copia.sort((a, b) => {
      const da = a.dias_para_proxima, db = b.dias_para_proxima;
      const va = da == null || da < 0 ? 99999 : da;
      const vb = db == null || db < 0 ? 99999 : db;
      if (va !== vb) return va - vb;
      if (a.tem_contato !== b.tem_contato) return a.tem_contato ? -1 : 1;
      return a.nome.localeCompare(b.nome, 'pt-BR');
    });
  }
  return copia;
}

/* ----------------------------------------------------------- desenho */

function desenharContatos(empresa) {
  const itens = [];

  // A pessoa de contato vem do cadastro da feira. Abrir a conversa chamando
  // alguem pelo nome, em mandarim, muda a taxa de resposta.
  if (empresa.contato_nome) {
    itens.push(`<span class="contato pessoa" data-copiar="${escapar(empresa.contato_nome)}">👤 ${escapar(empresa.contato_nome)}</span>`);
  }
  (empresa.emails || []).slice(0, 3).forEach((e) => {
    itens.push(`<span class="contato" data-copiar="${escapar(e)}">✉ ${escapar(e)}</span>`);
  });
  (empresa.telefones || []).slice(0, 2).forEach((t) => {
    itens.push(`<span class="contato" data-copiar="${escapar(t)}">☎ ${escapar(t)}</span>`);
  });
  (empresa.whatsapps || []).slice(0, 1).forEach((w) => {
    const limpo = String(w).replace(/\D/g, '');
    itens.push(`<a class="contato" href="https://wa.me/${limpo}" target="_blank" rel="noopener">WhatsApp ${escapar(w)}</a>`);
  });
  if (empresa.wechat) {
    itens.push(`<span class="contato wechat" data-copiar="${escapar(empresa.wechat)}">微信 ${escapar(empresa.wechat)}</span>`);
  }
  const site = urlAbsoluta(empresa.website);
  if (site) {
    itens.push(`<a class="contato" href="${escapar(site)}" target="_blank" rel="noopener">🌐 site</a>`);
  }
  const siteCn = urlAbsoluta(empresa.website_cn);
  if (siteCn) {
    itens.push(`<a class="contato" href="${escapar(siteCn)}" target="_blank" rel="noopener">🇨🇳 site chinês</a>`);
  }
  if (!itens.length) {
    itens.push('<span class="contato vazio">sem e-mail/WeChat ainda — abra o site ou os links de pesquisa abaixo</span>');
  } else if (!empresa.wechat) {
    // deixa explicito o que ainda falta, para o interprete saber onde procurar
    itens.push('<span class="contato vazio">WeChat não encontrado</span>');
  }
  return itens.join('');
}

function desenharFeiras(empresa, ocultarEncerradas) {
  const lista = ocultarEncerradas ? empresa.feiras.filter((f) => !f.encerrada) : empresa.feiras;
  if (!lista.length) return '<span class="pilula-feira encerrada">sem feira futura</span>';
  return lista.slice(0, 4).map((f) => {
    const urgente = f.dias != null && f.dias >= 0 && f.dias <= 45;
    const classe = f.encerrada ? 'encerrada' : (urgente ? 'urgente' : '');
    const prazo = textoPrazo(f.dias);
    const stand = f.stand ? ` · <span class="stand">estande ${escapar(f.stand)}</span>` : '';
    return `<span class="pilula-feira ${classe}" title="${escapar(f.local)} — ${escapar(f.cidade)}/${escapar(f.uf)}">`
      + `${escapar(f.nome)} · ${formatarData(f.inicio)}${prazo ? ' · ' + prazo : ''}${stand}</span>`;
  }).join('');
}

function desenharCartao(empresa, filtros) {
  const marca = marcacao(empresa.id);
  const links = empresa.links_pesquisa || {};
  const linksHtml = ['google', 'baidu', 'alibaba', 'made_in_china', 'qcc', 'linkedin']
    .filter((k) => links[k])
    .map((k) => `<a href="${escapar(links[k])}" target="_blank" rel="noopener">${k.replace(/_/g, '-')}</a>`)
    .join('');

  const porte = empresa.funcionarios
    ? `<span class="etiqueta porte">${escapar(empresa.funcionarios)} func.</span>` : '';
  const fundacao = empresa.ano_fundacao
    ? `<span class="etiqueta porte">desde ${escapar(empresa.ano_fundacao)}</span>` : '';
  const local = [empresa.cidade, empresa.provincia].filter(Boolean).join(', ');
  const negocio = empresa.tipo_negocio
    ? `<span class="etiqueta porte">${escapar(empresa.tipo_negocio)}</span>` : '';

  return `
  <article class="cartao ${marca.contatado ? 'contatado' : ''}" data-id="${escapar(empresa.id)}">
    <div>
      <div class="cabecalho-cartao">
        <span class="nome-empresa">${escapar(empresa.nome)}</span>
        ${empresa.nome_zh ? `<span class="nome-zh">${escapar(empresa.nome_zh)}</span>` : ''}
        <span class="etiqueta ${escapar(empresa.origem === 'china' ? empresa.classificacao : empresa.origem)}">
          ${empresa.origem === 'taiwan' ? 'Taiwan' : empresa.origem === 'hong_kong' ? 'Hong Kong' : empresa.classificacao}
        </span>
        ${porte}${fundacao}${negocio}
      </div>
      <div class="meta-empresa">
        ${local ? `<span>📍 ${escapar(local)}</span>` : ''}
        ${empresa.setor ? `<span>🏷 ${escapar(empresa.setor)}</span>` : ''}
        ${(empresa.produtos || []).length ? `<span>${escapar(empresa.produtos.slice(0, 3).join(' · '))}</span>` : ''}
      </div>
      <div class="feira-linha">${desenharFeiras(empresa, filtros.ocultarEncerradas)}</div>
      <div class="contatos">${desenharContatos(empresa)}</div>
      ${linksHtml ? `<div class="links-pesquisa"><span style="font-size:.76rem;color:var(--texto-fraquissimo)">pesquisar:</span>${linksHtml}</div>` : ''}
      ${(empresa.motivos || []).length ? `<div class="motivos">detectada como chinesa por: ${escapar(empresa.motivos.join('; '))}</div>` : ''}
      <textarea class="nota" placeholder="suas anotações sobre esta empresa..."
        data-nota="${escapar(empresa.id)}">${escapar(marca.nota)}</textarea>
    </div>
    <div class="coluna-acoes">
      <button class="botao pequeno ${marca.contatado ? '' : 'primario'}" data-contatar="${escapar(empresa.id)}">
        ${marca.contatado ? '↩ desmarcar' : '✓ já contatei'}
      </button>
      ${(empresa.emails || []).length
        ? `<button class="botao pequeno" data-copiar-todos="${escapar((empresa.emails || []).join(', '))}">copiar e-mails</button>`
        : ''}
    </div>
  </article>`;
}

function desenharFeirasAba(filtros) {
  const lista = estado.feiras.filter((f) => {
    if (filtros.ocultarEncerradas && f.encerrada) return false;
    if (filtros.uf && f.uf !== filtros.uf) return false;
    if (filtros.busca && !(`${f.nome} ${f.local} ${f.cidade} ${f.setor}`.toLowerCase().includes(filtros.busca))) return false;
    return true;
  });

  if (!lista.length) return '<div class="vazio">Nenhuma feira encontrada com esses filtros.</div>';

  const linhas = lista.map((f) => {
    const prazo = textoPrazo(f.dias);
    const densidade = f.densidade_china
      ? `<span class="densidade ${escapar(f.densidade_china)}">${escapar(f.densidade_china)}</span>` : '';
    const verEmpresas = f.total_chinesas
      ? `<button class="botao pequeno primario" data-ver-feira="${escapar(f.id)}">ver ${f.total_chinesas} empresas</button>`
      : '<span style="font-size:.8rem;color:var(--texto-fraquissimo)">lista ainda não publicada</span>';
    return `<tr class="${f.encerrada ? 'encerrada' : ''}">
      <td><strong>${escapar(f.nome)}</strong>${f.setor ? `<br><span style="font-size:.8rem;color:var(--texto-fraco)">${escapar(f.setor)}</span>` : ''}</td>
      <td>${f.inicio ? formatarData(f.inicio) : escapar(f.data_texto || '—')}${prazo ? `<br><span style="font-size:.8rem;color:var(--primaria)">${prazo}</span>` : ''}</td>
      <td>${escapar(f.local)}<br><span style="font-size:.8rem;color:var(--texto-fraco)">${escapar(f.cidade)}/${escapar(f.uf)}</span></td>
      <td>${densidade}</td>
      <td style="text-align:right">${f.total_expositores || '—'}</td>
      <td style="text-align:right"><strong>${f.total_chinesas || '—'}</strong></td>
      <td>${verEmpresas}${f.site ? ` <a href="${escapar(f.site)}" target="_blank" rel="noopener">site</a>` : ''}</td>
    </tr>`;
  }).join('');

  return `<div class="rolagem-tabela"><table class="tabela-feiras">
    <thead><tr>
      <th>Feira</th><th>Data</th><th>Local</th><th>Presença chinesa</th>
      <th style="text-align:right">Expositores</th><th style="text-align:right">Chinesas</th><th></th>
    </tr></thead>
    <tbody>${linhas}</tbody>
  </table></div>`;
}

function renderizar() {
  const filtros = lerFiltros();
  const conteudo = document.getElementById('conteudo');
  const contagem = document.getElementById('contagem');

  if (estado.aba === 'feiras') {
    contagem.textContent = '';
    conteudo.innerHTML = desenharFeirasAba(filtros);
    return;
  }

  const base = estado.aba === 'revisao' ? estado.revisao : estado.empresas;
  const filtradas = ordenar(base.filter((e) => empresaPassa(e, filtros)), filtros.ordem);

  const comContato = filtradas.filter((e) => e.tem_contato).length;
  contagem.textContent = `${filtradas.length} empresa(s) — ${comContato} com contato pronto`
    + (filtros.ocultarEncerradas ? ' · feiras encerradas ocultas' : ' · incluindo feiras encerradas');

  conteudo.innerHTML = filtradas.length
    ? filtradas.map((e) => desenharCartao(e, filtros)).join('')
    : '<div class="vazio">Nenhuma empresa com esses filtros.<br>Tente desmarcar “só com contato” ou mostrar feiras encerradas.</div>';
}

/* ----------------------------------------------------------- exportar */

function exportarCSV() {
  const filtros = lerFiltros();
  const base = estado.aba === 'revisao' ? estado.revisao : estado.empresas;
  const linhas = ordenar(base.filter((e) => empresaPassa(e, filtros)), filtros.ordem);

  const colunas = ['nome', 'pais', 'cidade', 'setor', 'emails', 'telefones', 'wechat',
    'whatsapps', 'website', 'website_cn', 'funcionarios', 'ano_fundacao',
    'proxima_feira', 'proxima_data', 'proximo_stand', 'ja_contatei', 'minhas_notas'];

  const escapaCSV = (v) => `"${String(v == null ? '' : v).replace(/"/g, '""')}"`;
  const corpo = linhas.map((e) => {
    const m = marcacao(e.id);
    return [
      e.nome, e.pais, e.cidade, e.setor, (e.emails || []).join(' | '),
      (e.telefones || []).join(' | '), e.wechat, (e.whatsapps || []).join(' | '),
      e.website, e.website_cn, e.funcionarios, e.ano_fundacao,
      e.proxima_feira, e.proxima_data, e.proximo_stand,
      m.contatado ? 'sim' : 'não', m.nota,
    ].map(escapaCSV).join(',');
  });

  const csv = '﻿' + [colunas.join(','), ...corpo].join('\r\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `empresas-chinesas-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* ----------------------------------------------------------- montagem */

function preencherSeletores() {
  const seletorFeira = document.getElementById('f-feira');
  const comExpositores = estado.feiras
    .filter((f) => f.total_chinesas > 0)
    .sort((a, b) => {
      // futuras primeiro, por data; encerradas no fim
      if (a.encerrada !== b.encerrada) return a.encerrada ? 1 : -1;
      return (a.inicio || '9999').localeCompare(b.inicio || '9999');
    });
  seletorFeira.innerHTML = '<option value="">todas as feiras</option>'
    + comExpositores.map((f) => `<option value="${escapar(f.id)}">${escapar(f.nome)}`
      + `${f.encerrada ? ' (encerrada)' : ''} — ${f.total_chinesas} chinesas</option>`).join('');

  const ufs = [...new Set(estado.feiras.map((f) => f.uf).filter(Boolean))].sort();
  document.getElementById('f-uf').innerHTML = '<option value="">todos os estados</option>'
    + ufs.map((u) => `<option value="${escapar(u)}">${escapar(u)}</option>`).join('');

  const setores = [...new Set(estado.empresas.map((e) => e.setor).filter(Boolean))].sort();
  document.getElementById('f-setor').innerHTML = '<option value="">todos os setores</option>'
    + setores.map((s) => `<option value="${escapar(s)}">${escapar(s)}</option>`).join('');
}

function desenharIndicadores() {
  const m = estado.meta;
  // A "proxima feira" tem que ser a proxima COM empresas chinesas. Sem esse filtro
  // o destaque cai em coisas como o Feirao Autoshow (venda de carro usado), que nao
  // gera trabalho nenhum para o interprete e ainda parece um dado quebrado.
  const proxima = estado.feiras.find(
    (f) => !f.encerrada && f.dias != null && f.dias >= 0 && f.total_chinesas > 0
  );
  const alvo = document.getElementById('indicadores');
  alvo.innerHTML = `
    <div class="indicador destaque"><div class="numero">${m.total_empresas || 0}</div><div class="rotulo">empresas chinesas</div></div>
    <div class="indicador"><div class="numero">${m.empresas_com_contato || 0}</div><div class="rotulo">com contato pronto</div></div>
    <div class="indicador"><div class="numero">${m.empresas_feira_futura || 0}</div><div class="rotulo">em feira que ainda vem</div></div>
    <div class="indicador"><div class="numero">${m.feiras_futuras || 0}</div><div class="rotulo">feiras futuras</div></div>
    <div class="indicador ${proxima && proxima.dias <= 45 ? 'destaque' : ''}">
      <div class="numero">${proxima ? textoPrazo(proxima.dias) || formatarData(proxima.inicio) : '—'}</div>
      <div class="rotulo">próxima com chinesas: ${escapar((proxima && proxima.nome ? proxima.nome : '—').slice(0, 24))}</div></div>`;
}

async function carregarJSON(caminho, padrao) {
  try {
    const resposta = await fetch(caminho, { cache: 'no-cache' });
    if (!resposta.ok) throw new Error(resposta.status);
    return await resposta.json();
  } catch (e) {
    console.warn('não consegui carregar', caminho, e);
    return padrao;
  }
}

async function iniciar() {
  const [empresas, feiras, meta] = await Promise.all([
    carregarJSON('data/empresas.json', []),
    carregarJSON('data/feiras.json', []),
    carregarJSON('data/meta.json', {}),
  ]);
  estado.empresas = empresas;
  estado.feiras = feiras;
  estado.meta = meta;

  document.getElementById('atualizado').textContent = meta.atualizado_em
    ? new Date(meta.atualizado_em).toLocaleString('pt-BR')
    : 'ainda não gerado';

  preencherSeletores();
  desenharIndicadores();
  renderizar();
}

/* ----------------------------------------------------------- eventos */

document.addEventListener('DOMContentLoaded', () => {
  iniciar();

  document.querySelectorAll('.filtros input, .filtros select').forEach((el) => {
    el.addEventListener(el.type === 'text' ? 'input' : 'change', renderizar);
  });

  document.getElementById('btn-csv').addEventListener('click', exportarCSV);

  document.getElementById('btn-limpar').addEventListener('click', () => {
    document.querySelectorAll('.filtros input[type="text"]').forEach((el) => { el.value = ''; });
    document.querySelectorAll('.filtros select').forEach((el) => { el.selectedIndex = 0; });
    document.getElementById('f-ocultar-encerradas').checked = true;
    document.getElementById('f-so-contato').checked = false;
    document.getElementById('f-ocultar-contatados').checked = false;
    document.getElementById('f-so-confirmadas').checked = false;
    renderizar();
  });

  document.querySelectorAll('.aba').forEach((botao) => {
    botao.addEventListener('click', async () => {
      document.querySelectorAll('.aba').forEach((b) => b.classList.remove('ativa'));
      botao.classList.add('ativa');
      estado.aba = botao.dataset.aba;
      if (estado.aba === 'revisao' && !estado.revisaoCarregada) {
        estado.revisao = await carregarJSON('data/empresas_revisao.json', []);
        estado.revisaoCarregada = true;
      }
      renderizar();
    });
  });

  // delegação: os cartões são redesenhados o tempo todo
  document.getElementById('conteudo').addEventListener('click', (evento) => {
    // da tabela de feiras direto para as empresas daquela feira
    const verFeira = evento.target.closest('[data-ver-feira]');
    if (verFeira) {
      const id = verFeira.dataset.verFeira;
      const seletor = document.getElementById('f-feira');
      // a feira pode estar encerrada e, nesse caso, escondida pelo filtro padrão
      const feira = estado.feiras.find((f) => f.id === id);
      if (feira && feira.encerrada) {
        document.getElementById('f-ocultar-encerradas').checked = false;
      }
      seletor.value = id;
      document.querySelectorAll('.aba').forEach((b) => b.classList.remove('ativa'));
      document.querySelector('[data-aba="empresas"]').classList.add('ativa');
      estado.aba = 'empresas';
      renderizar();
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }
    const copiavel = evento.target.closest('[data-copiar]');
    if (copiavel) { copiar(copiavel.dataset.copiar, copiavel); return; }
    const todos = evento.target.closest('[data-copiar-todos]');
    if (todos) { copiar(todos.dataset.copiarTodos, todos); return; }
    const contatar = evento.target.closest('[data-contatar]');
    if (contatar) { alternarContatado(contatar.dataset.contatar); }
  });

  document.getElementById('conteudo').addEventListener('input', (evento) => {
    const nota = evento.target.closest('[data-nota]');
    if (nota) anotar(nota.dataset.nota, nota.value);
  });
});
