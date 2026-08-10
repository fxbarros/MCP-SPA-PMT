<h1 align="center">
    <img alt="MCP SPA-PMT" src="https://raw.githubusercontent.com/fxbarros/MCP-SPA-PMT/main/docs/assets/banner.svg?sanitize=true">
    <br>
    <small>Caixas, prazos, triagem e pasta judicial do SPA em linguagem natural — sem nunca escrever no sistema</small>
</h1>

<p align="center">
    <img alt="Python" src="https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white">
    <img alt="Ferramentas" src="https://img.shields.io/badge/ferramentas-12-brightgreen">
    <img alt="MCP" src="https://img.shields.io/badge/MCP-Claude%20Desktop-d97757">
    <img alt="HTTP puro" src="https://img.shields.io/badge/HTTP%20puro-sem%20browser-blue">
    <img alt="Somente leitura" src="https://img.shields.io/badge/SPA-somente%20leitura-8b0000">
</p>

<p align="center">
    <a href="#-funcionalidades"><strong>Funcionalidades</strong></a>
    &middot;
    <a href="#%EF%B8%8F-as-12-ferramentas"><strong>Ferramentas</strong></a>
    &middot;
    <a href="#-instala%C3%A7%C3%A3o"><strong>Instalação</strong></a>
    &middot;
    <a href="#-exemplos-de-uso"><strong>Exemplos</strong></a>
    &middot;
    <a href="#-como-funciona-por-dentro"><strong>Por dentro</strong></a>
    &middot;
    <a href="#-seguran%C3%A7a"><strong>Segurança</strong></a>
    &middot;
    <a href="#%EF%B8%8F-avisos-importantes"><strong>Avisos</strong></a>
</p>

Servidor [MCP](https://modelcontextprotocol.io) que permite ao Claude Desktop consultar o **SPA — Sistema de Processos Automatizados** da Prefeitura Municipal de Teresina (`spa.pmt.pi.gov.br`, plataforma Rails/Devise da Coreplan) diretamente em linguagem natural.

> ⚡ **Sem browser**: o SPA não tem Cloudflare nem captcha, então tudo é feito em **HTTP puro** com [Scrapling](https://github.com/D4Vinci/Scrapling) — login Devise, tabelas DataTables e PDFs. Rápido, leve e sem janela de Chrome abrindo.

## ✨ Funcionalidades

- 🔐 **Login 100% automatizado**: e-mail + senha do Keychain do macOS, com relogin transparente quando a sessão expira
- 📥 **Caixas de processos** (fluxos): listagem paginada com busca textual local
- ⏰ **Prazos urgentes** em todas as caixas, ordenados do mais urgente ao menos urgente — com detecção do prazo *vigente* (o campo do SPA acumula prazos históricos)
- 🆕 **Entradas recentes**: o que chegou numa caixa nos últimos N dias
- 🗞️ **Triagem em uma chamada**: briefing completo da banca — tudo que entrou nos últimos dias, já com o resumo dos documentos judiciais mais recentes de cada processo
- ⚖️ **Pasta do Processo judicial**: lista, lê (texto paginado, direto na conversa) e baixa os documentos que o SPA recebe do PJe por integração SOAP/MNI
- 🔍 **Busca global** por número CNJ, nome de parte ou CPF/CNPJ
- 📄 **Download do PDF integral** do processo administrativo
- 🔔 **Notificações** do sino do sistema
- 🛡️ **Leitura passiva**: nenhuma ferramenta toma ciência, executa passo de fluxo ou escreve no SPA

## 🛠️ As 12 ferramentas

**Caixas e painel**

| Ferramenta | O que faz |
|---|---|
| `listar_fluxos` | as caixas (fluxos) do usuário com o total de processos em cada uma — o equivalente às abas de "Meus Processos" |
| `listar_caixa` | processos de uma caixa, com paginação e busca textual (filtro aplicado no cliente — ver [avisos](#%EF%B8%8F-avisos-importantes)) |
| `prazos_urgentes` | varre TODAS as caixas e retorna os processos com prazo vigente nos próximos N dias (negativo = atrasado), com rótulo e hora do prazo |
| `entradas_recentes` | processos que ENTRARAM numa caixa nos últimos N dias, do mais recente ao mais antigo |
| `triagem` | briefing em uma chamada: entradas recentes de todas as caixas + resumo dos documentos judiciais mais novos de cada processo (paralelizado, leitura passiva) |
| `notificacoes` | notificações do sino do SPA |

**Consulta e busca**

| Ferramenta | O que faz |
|---|---|
| `buscar_processo` | busca global do topo do sistema: nº CNJ, nome de parte ou CPF/CNPJ |
| `obter_processo` | abre um processo pelo id interno: campos do cabeçalho (partes, classe, vara, responsável, status, prazos) + timeline de andamentos |

**Documentos e autos**

| Ferramenta | O que faz |
|---|---|
| `baixar_processo` | PDF integral do processo administrativo (todas as peças num único arquivo, como o SPA monta) |
| `listar_documentos_judiciais` | a "Pasta do Processo" judicial — documentos que o SPA baixa do tribunal por comunicação eletrônica (inicial, despachos, certidões, intimações...) |
| `baixar_documento_judicial` | baixa UM documento da pasta judicial pelo id — ou, sem id, os autos completos num único PDF |
| `ler_documento_judicial` | extrai o TEXTO de um documento judicial direto na conversa, paginado, sem salvar arquivo — com cache (continuar a leitura não rebaixa o PDF) e detecção dos ids do PJe citados no texto |

## 🧰 Requisitos

- macOS (credenciais no Keychain — em Linux/Windows funciona com backend `keyring` equivalente)
- Python 3.12+ e [uv](https://docs.astral.sh/uv/)
- Claude Desktop instalado
- Conta ativa no SPA da PMT (login por e-mail e senha)

## 📦 Instalação

### 1) Clone o repositório

```bash
git clone https://github.com/fxbarros/MCP-SPA-PMT.git spa-mcp
cd spa-mcp
```

### 2) Instale as dependências

```bash
uv sync
```

### 3) Salve as credenciais no Keychain

```bash
uv run setup_credenciais.py
```

O script pergunta o e-mail e a senha do SPA. Tudo fica criptografado no Keychain do macOS (service `mcp-spa`) — nunca em arquivo. Alternativa sem Keychain: exporte `SPA_EMAIL` e `SPA_SENHA` no ambiente do processo.

### 4) Teste o login (opcional mas recomendado)

```bash
uv run diagnostico_login.py
```

O diagnóstico faz o ciclo completo (CSRF → POST → rota autenticada) e imprime os módulos que a sua conta enxerga. Se o layout do login mudar um dia, há um fallback com Chrome real: `uv run diagnostico_login_browser.py`.

### 5) Registre o MCP no Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (ajuste o caminho):

```json
{
  "mcpServers": {
    "spa-pmt": {
      "command": "uv",
      "args": [
        "--directory", "/Users/SEU_USUARIO/spa-mcp",
        "run", "spa-mcp"
      ]
    }
  }
}
```

### 6) Reinicie o Claude Desktop

`Cmd+Q` e abra de novo — as ferramentas devem aparecer.

## 💬 Exemplos de uso

```
Faz a triagem da minha caixa no SPA

Quais meus prazos urgentes nos próximos 15 dias?

O que entrou na caixa de Processo Patrimonial nos últimos 7 dias?

Lista minhas caixas no SPA com os totais

Busca o processo 0000000-00.0000.0.00.0000 no SPA

Abre o processo 12345 e me resume o cabeçalho e a timeline

Lista os documentos judiciais do processo 0000000-00.0000.0.00.0000

Lê a última decisão da pasta judicial desse processo

Baixa os autos completos desse processo pra ~/Downloads

Tenho notificações no SPA?
```

## 🏗️ Estrutura do projeto

```
spa-mcp/
├── README.md                     # este arquivo
├── pyproject.toml                # dependências e entry point (uv)
├── setup_credenciais.py          # setup inicial (rodar 1x)
├── diagnostico_login.py          # diagnóstico de login HTTP puro
├── diagnostico_login_browser.py  # fallback com Chrome real (patchright, headed)
├── docs/assets/banner.svg        # arte do repositório
└── src/spa_mcp/
    └── server.py                 # servidor MCP completo (sessão, parsers e as 12 tools)
```

## 🔬 Como funciona por dentro

Detalhes de engenharia reversa do SPA que este MCP encapsula — úteis se o sistema mudar ou se você for adaptar para outra instalação da mesma plataforma:

**Login e sessão** — autenticação Devise clássica: `GET /users/sign_in` para extrair o token CSRF do `form#new_user`, depois `POST` com `user[email]`/`user[password]`. Os cookies persistem em `~/.mcp-spa-session.json` (chmod 600) e são reaproveitados entre chamadas; quando qualquer requisição cai na tela de login, o servidor reloga automaticamente **uma vez** — com um lock de thread, porque ferramentas como a `triagem` rodam requisições em paralelo e dois relogins simultâneos disputariam a sessão.

**Caixas = DataTables server-side** — a página `/procedures?flow_id=N` não traz os dados: a tabela `#procedures-box` aponta o endpoint JSON no atributo `data-url` (`/procedures/inbox?...`). Esse endpoint devolve cada processo como uma **lista de células HTML**; o valor limpo vem no texto visível (o truncamento do SPA é só CSS) com o atributo `title` de fallback. Os nomes das colunas saem dos `data-name` dos `<th>`.

**Dois bugs server-side contornados** — enviar `search[value]` preenchido **ou** qualquer cláusula `order[...]` ao inbox responde **HTTP 500** (bugs do próprio SPA). Por isso busca e ordenação são feitas 100% no cliente: com busca, o MCP baixa até 500 linhas da caixa e filtra localmente (substring case-insensitive em todos os campos; com 4+ dígitos, compara também só os dígitos — acha CNJ com ou sem pontuação).

**Número CNJ nas caixas de expediente** — a listagem dessas caixas não tem coluna de número de processo. A única fonte é o link de "Documentos Externos" na coluna Ações (`/judicial_processes/<cnj>/documents?soap_setting_id=N`), de onde o MCP extrai o CNJ (20 dígitos) e o `soap_setting_id` (origem da comunicação — ex.: TJPI 1º grau).

**Prazo vigente** — o campo de prazo do SPA acumula o histórico ("Ciência tácita 17/07/2026 - 01:00 | ..."). O MCP descarta datas administrativas ("Data da criação: ..."), escolhe a **data futura mais próxima** (ou, se todas passaram, a mais recente) e devolve `data_prazo` + `dias_para_prazo` (negativo = atrasado), mantendo o texto completo em `prazo_completo` para conferência.

**Data de entrada na caixa** — exata quando o tooltip traz a data por extenso ("03 de Julho de 2026, 17:28"); senão aproximada (±1 dia) a partir do texto relativo ("aproximadamente 23 horas", "3 meses").

**Leitura de PDFs** — `ler_documento_judicial` extrai o texto com PyMuPDF em páginas, trunca em `max_chars` e indica a próxima `pagina_inicial`; os últimos 5 PDFs ficam num cache LRU em memória, então continuar a leitura de um documento longo **não** rebaixa o arquivo do tribunal. PDFs escaneados sem camada de texto são detectados e a resposta orienta baixar para OCR externo.

**Honestidade estatística** — toda ferramenta que avalia uma amostra da caixa (busca, prazos, entradas recentes, triagem) avisa explicitamente quando a caixa tem mais processos do que os avaliados, para o modelo não concluir "não há nada" a partir de uma amostra parcial.

## 🔒 Segurança

- **Credenciais** ficam no Keychain do macOS (service `mcp-spa`), nunca em arquivo nem no código
- **Cookies de sessão** em `~/.mcp-spa-session.json` com permissão `600` (só o seu usuário lê)
- **Nenhuma ação de escrita no SPA**: este MCP só **lê** — não toma ciência, não executa passos de fluxo, não protocola nem altera nada; os downloads gravam apenas no seu disco local
- **Nenhum dado de processo no repositório**: o código não contém números de processo, nomes de parte nem credenciais

## ⚠️ Avisos importantes

**Fragilidade de scraping** — o projeto depende do HTML e dos endpoints atuais do SPA. Se a plataforma for atualizada: rode `uv run diagnostico_login.py` para ver onde trava, inspecione as páginas salvas em `/tmp/spa_*.html` e ajuste os seletores em `src/spa_mcp/server.py`. Para depurar visualmente há o fallback headed: `uv run diagnostico_login_browser.py`.

**Busca e ordenação são locais** — o endpoint de inbox do SPA responde HTTP 500 a `search[value]` e `order[...]` (bugs do sistema, não deste projeto). A busca baixa até 500 linhas da caixa e filtra no cliente; caixas maiores que isso retornam aviso de amostra parcial.

**Datas aproximadas** — quando o SPA só expõe texto relativo ("há 2 dias"), a data de entrada é aproximada em ±1 dia. A resposta indica quando a data é exata (tooltip por extenso) e quando é estimada.

**Uso responsável** — sistema interno de trabalho: use com a sua conta, para os seus processos, respeitando as normas do órgão. Nada de varredura massiva.

## 🔄 Adaptando para outras instalações

O SPA da Coreplan atende outros entes públicos. Para adaptar: mude `BASE_URL` em `src/spa_mcp/server.py`, confira o `id` do form de login (`new_user`) e da tabela de inbox (`procedures-box`) no DevTools, ajuste os `flow_id` de exemplo nas docstrings e renomeie o MCP (`FastMCP("spa-pmt")`).

## 🚧 Roadmap

- [ ] OCR local para documentos judiciais escaneados sem camada de texto
- [ ] Suporte a outros `soap_setting_id` (tribunais de origem) descobertos automaticamente
- [ ] Cache opcional em disco da listagem de caixas para triagens mais rápidas

## 📝 Licença e créditos

Uso pessoal e profissional, sem garantias — use por sua conta e risco, respeitando as regras do órgão. Construído por [Fábio Ximenes Barros](https://github.com/fxbarros) com ajuda do [Claude](https://www.anthropic.com/claude), usando [FastMCP](https://gofastmcp.com), [Scrapling](https://github.com/D4Vinci/Scrapling), [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/) e [PyMuPDF](https://pymupdf.readthedocs.io).

<p align="center"><sub>Arte do banner: original — marca dos projetos MCP do autor.</sub></p>
