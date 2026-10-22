"""Servidor MCP para o SPA-PMT (Sistema de Processos Automatizados, Prefeitura de Teresina).

Acesso HTTP puro via Scrapling (o SPA não tem Cloudflare). Autenticação Devise
com credenciais do Keychain (service mcp-spa); cookies persistidos em
~/.mcp-spa-session.json e relogin automático quando a sessão expira.

As caixas de processos (/procedures?flow_id=N) são tabelas DataTables
server-side: a tabela #procedures-box aponta o endpoint de dados no atributo
data-url (/procedures/inbox?...), que devolve JSON com uma lista de células
HTML por processo — o valor limpo vem no atributo title de cada célula.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import keyring
from bs4 import BeautifulSoup
from fastmcp import FastMCP
from scrapling.fetchers import FetcherSession

SERVICE = "mcp-spa"
BASE_URL = "https://spa.pmt.pi.gov.br"
SESSION_FILE = Path.home() / ".mcp-spa-session.json"

XHR_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

mcp = FastMCP("spa-pmt")


# ---------------------------------------------------------------- sessão

def _carregar_cookies() -> dict[str, str]:
    if SESSION_FILE.exists():
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    return {}


def _salvar_cookies(cookies: dict[str, str]) -> None:
    SESSION_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
    SESSION_FILE.chmod(0o600)


# Serializa o relogin: tools podem rodar em paralelo (ex.: triagem) e não
# queremos dois POSTs de sign_in disputando a sessão.
_LOGIN_LOCK = threading.Lock()


def _logar(sessao: FetcherSession) -> dict[str, str]:
    """Login Devise: extrai o CSRF do form#new_user e POSTa as credenciais."""
    email = os.environ.get("SPA_EMAIL") or keyring.get_password(SERVICE, "email")
    senha = os.environ.get("SPA_SENHA") or keyring.get_password(SERVICE, "senha")
    if not email or not senha:
        raise RuntimeError(
            "Credenciais não encontradas no Keychain (service mcp-spa) nem nas "
            "variáveis SPA_EMAIL/SPA_SENHA. Rode: uv run setup_credenciais.py"
        )

    cookies: dict[str, str] = {}
    r = sessao.get(f"{BASE_URL}/users/sign_in")
    cookies.update(r.cookies)

    form = next((f for f in r.css("form") if f.attrib.get("id") == "new_user"), None)
    if form is None:
        raise RuntimeError("form#new_user não encontrado — layout do login mudou?")

    dados = {
        i.attrib.get("name"): i.attrib.get("value") or ""
        for i in form.css("input[type=hidden]")
        if i.attrib.get("name")
    }
    dados["user[email]"] = email
    dados["user[password]"] = senha
    dados["user[remember_me]"] = "1"
    acao = form.attrib.get("action") or "/users/sign_in"

    r = sessao.post(f"{BASE_URL}{acao}", data=dados, cookies=cookies)
    cookies.update(r.cookies)
    if "/users/sign_in" in r.url:
        raise RuntimeError("Login recusado pelo SPA — confira as credenciais no Keychain.")

    _salvar_cookies(cookies)
    return cookies


def _get_autenticado(sessao: FetcherSession, url: str, **kwargs):
    """GET com a sessão salva; reloga uma vez se cair na tela de login."""
    cookies = _carregar_cookies()
    r = sessao.get(url, cookies=cookies, **kwargs)
    if "/users/sign_in" in r.url or r.status == 401:
        with _LOGIN_LOCK:
            cookies = _carregar_cookies()  # outra thread pode ter relogado
            r = sessao.get(url, cookies=cookies, **kwargs)
            if "/users/sign_in" in r.url or r.status == 401:
                cookies = _logar(sessao)
                r = sessao.get(url, cookies=cookies, **kwargs)
        if "/users/sign_in" in r.url:
            raise RuntimeError("Sessão não segurou mesmo após relogin.")
    return r


# ---------------------------------------------------------------- parser

def _texto_celula(fragmento: Any) -> str:
    """Extrai o valor limpo de uma célula do DataTables.

    As colunas de dados vêm como '<span title="VALOR">VALOR</span>'. O texto
    visível é a fonte primária (o truncamento do SPA é só CSS); o title fica
    de fallback para células que só renderizam ícone.
    """
    if not fragmento:
        return ""
    soup = BeautifulSoup(str(fragmento), "html.parser")
    texto = " ".join(soup.get_text(" ").split())
    if texto:
        return texto
    marcado = soup.find(attrs={"title": True})
    return marcado["title"].strip() if marcado else ""


def _cabecalhos_e_dataurl(pagina) -> tuple[list[str], str]:
    """Da página /procedures?flow_id=N tira os nomes das colunas e o data-url."""
    tabela = next(
        (t for t in pagina.css("table") if t.attrib.get("id") == "procedures-box"),
        None,
    )
    if tabela is None:
        raise RuntimeError("Tabela #procedures-box não encontrada — flow_id inválido?")
    data_url = tabela.attrib.get("data-url")
    if not data_url:
        raise RuntimeError("Tabela sem data-url — layout mudou?")
    nomes = []
    for th in tabela.css("th"):
        nome = th.attrib.get("data-name") or " ".join((th.text or "").split())
        nomes.append(nome or "col")
    return nomes, data_url


def _params_datatables(n_colunas: int, start: int, length: int) -> dict[str, str]:
    """Corpo DataTables mínimo que o inbox aceita.

    NUNCA enviar `search[value]` preenchido nem cláusula `order[...]`: ambos
    respondem HTTP 500 (bugs server-side do SPA). Busca e ordenação são feitas
    no cliente.
    """
    params = {
        "draw": "1",
        "start": str(start),
        "length": str(length),
        "search[value]": "",
        "search[regex]": "false",
    }
    for i in range(n_colunas):
        params[f"columns[{i}][data]"] = str(i)
        params[f"columns[{i}][name]"] = ""
        params[f"columns[{i}][searchable]"] = "true"
        params[f"columns[{i}][orderable]"] = "false"
        params[f"columns[{i}][search][value]"] = ""
        params[f"columns[{i}][search][regex]"] = "false"
    return params


def _montar_processo(nomes: list[str], celulas: list[Any]) -> dict[str, Any]:
    """Converte uma linha do inbox (lista de células) num dict legível."""
    proc: dict[str, Any] = {}

    meta = celulas[0] if celulas and isinstance(celulas[0], dict) else {}
    proc["id"] = meta.get("procedure_id")
    proc["url"] = meta.get("procedure_url")
    proc["visto"] = meta.get("was_seen")

    # CNJ + soap_setting_id vêm do link de Documentos Externos (coluna Ações) —
    # única fonte de CNJ nas caixas de expediente, que não têm essa coluna.
    docs_ext = _extrair_docs_externos(celulas)
    if docs_ext:
        proc["numero_cnj"] = _formatar_cnj(docs_ext[0])
        proc["soap_setting_id"] = docs_ext[1]

    ignorar = {"markers", "Ações", "priority"}
    recebido_bruto: str | None = None
    for nome, celula in zip(nomes, celulas):
        if nome in ignorar or isinstance(celula, dict):
            continue
        valor = _texto_celula(celula)
        if nome == "deadline":
            # célula de prazo: o texto visível repete blocos e os tooltips
            # carregam detalhe em HTML (<br/>) — dedup e normaliza os dois
            soup = BeautifulSoup(str(celula), "html.parser")
            partes: list[str] = []
            for trecho in [soup.get_text(" ")] + [
                t["title"] for t in soup.find_all(attrs={"title": True})
            ]:
                trecho = " ".join(trecho.replace("<br/>", " | ").split())
                if trecho and trecho not in partes:
                    partes.append(trecho)
            proc["prazo"] = " | ".join(partes) or None
            continue
        if nome == "received_at" or "receb" in nome.lower():
            recebido_bruto = str(celula) if isinstance(celula, str) else None
        proc[nome] = valor

    # prazo VIGENTE (data futura mais próxima; senão a mais recente)
    hoje = date.today()
    d = _parse_data_prazo(proc.get("prazo"), hoje)
    if d is not None:
        proc["data_prazo"] = d.isoformat()
        proc["dias_para_prazo"] = (d - hoje).days  # negativo = atrasado

    # data de entrada na caixa: exata se o tooltip trouxer a data por extenso;
    # senão aproximada (±1 dia) a partir do texto relativo
    exata = _parse_data_longa(recebido_bruto)
    if exata is not None:
        proc["data_recebido"] = exata.isoformat()
        proc["recebido_dias"] = (hoje - exata).days
    else:
        dias = _parse_recebido_dias(proc.get("received_at") or proc.get("Recebido"))
        if dias is not None:
            proc["recebido_dias"] = dias
            proc["data_recebido"] = (hoje - timedelta(days=dias)).isoformat()
    return proc


def _descobrir_fluxos(sessao: FetcherSession) -> dict[int, str]:
    """Da página /procedures tira as abas de fluxo (id -> nome) do usuário."""
    r = _get_autenticado(sessao, f"{BASE_URL}/procedures")
    soup = BeautifulSoup(r.body.decode("utf-8", errors="replace"), "html.parser")
    fluxos: dict[int, str] = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r"[?&]flow_id=(\d+)$", a["href"])
        if not m:
            continue
        fid = int(m.group(1))
        nome = re.sub(r"\s*\d+\s*$", "", " ".join(a.get_text(" ").split())).strip()
        if nome:
            fluxos[fid] = nome
        else:
            fluxos.setdefault(fid, f"fluxo {fid}")
    if not fluxos:
        raise RuntimeError("Nenhuma aba de fluxo encontrada em /procedures.")
    return fluxos


# célula de prazo já normalizada: "Ciência tácita 17/07/2026 - 01:00 | ..."
PRAZO_RE = re.compile(
    r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ ']*?)\s*(\d{2}/\d{2}/\d{4})(?:\s*-\s*(\d{2}:\d{2}))?"
)

CNJ_RE = re.compile(r"\d{7}-?\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")

_DATA_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

# Link "Documentos Externos" (coluna Ações): integração SOAP/MNI com o tribunal
# de origem. Traz o CNJ (só dígitos) e o soap_setting_id (1 = TJPI 1º grau...).
# É a ÚNICA fonte de CNJ nas caixas de expediente, cuja listagem não tem coluna
# de número de processo.
_DOCS_EXTERNOS_RE = re.compile(
    r"/judicial_processes/(\d{14,25})/documents\?soap_setting_id=(\d+)"
)

# Ids numéricos do PJe citados no texto dos PDFs — rodapé "Num. 104857191 -
# Pag. 3" ou referências no corpo: "(Id 45678901)", "Ids. 99336360 e 99336361".
_ID_PJE_RE = re.compile(
    r"\b(?:Num|ID|Id)s?\s*\.?\s*:?\s*(\d{5,12}(?:\s*(?:,|e)\s+\d{5,12})*)"
)
_ID_PJE_DIGITOS_RE = re.compile(r"\d{5,12}")

# `recebido` em texto relativo: "aproximadamente 23 horas", "5 dias", "3 meses"
_RECEBIDO_REL_RE = re.compile(
    r"(\d+)\s*(segundo|minuto|hora|dia|semana|m[eê]s|ano)", re.I
)

# data exata por extenso no tooltip: "03 de Julho de 2026, 17:28"
_MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}
_DATA_LONGA_RE = re.compile(r"(\d{1,2})\s+de\s+([A-Za-zçÇ]+)\s+de\s+(\d{4})", re.I)


def _numero_processo(proc: dict[str, Any]) -> str:
    if proc.get("numero_cnj"):
        return proc["numero_cnj"]
    for valor in proc.values():
        if isinstance(valor, str):
            m = CNJ_RE.search(valor)
            if m:
                return m.group(0)
    return ""


def _formatar_cnj(digitos: str) -> str:
    d = re.sub(r"\D", "", digitos or "").zfill(20)
    if len(d) != 20:
        return digitos
    return f"{d[0:7]}-{d[7:9]}.{d[9:13]}.{d[13]}.{d[14:16]}.{d[16:20]}"


def _extrair_docs_externos(celulas: list[Any]) -> tuple[str, int] | None:
    """(cnj_20_digitos, soap_setting_id) do link de Documentos Externos, se houver."""
    for celula in celulas or []:
        if not isinstance(celula, str):
            continue
        m = _DOCS_EXTERNOS_RE.search(celula)
        if m:
            return m.group(1), int(m.group(2))
    return None


# datas administrativas no tooltip que NÃO são vencimento (ex.: "Data da
# criação: 06/07/2026 - 13:00") — remover antes de escolher o prazo vigente
_DATA_ADMIN_RE = re.compile(
    r"Data d[ae][a-zà-ÿ ]*:\s*\d{2}/\d{2}/\d{4}(?:\s*-\s*\d{2}:\d{2})?", re.I
)


def _parse_data_prazo(prazo: str | None, ref: date | None = None) -> date | None:
    """Data VIGENTE de um campo de prazo (o campo acumula prazos históricos).

    Regra: a data futura mais próxima de hoje; se todas já passaram, a mais
    recente. Evita dias_para_prazo absurdos vindos de prazo antigo. Datas
    administrativas ("Data da criação: ...") são ignoradas.
    """
    if not prazo:
        return None
    prazo = _DATA_ADMIN_RE.sub(" ", prazo)
    datas: list[date] = []
    for m in _DATA_RE.finditer(prazo):
        dia, mes, ano = (int(g) for g in m.groups())
        try:
            datas.append(date(ano, mes, dia))
        except ValueError:
            continue
    if not datas:
        return None
    ref = ref or date.today()
    futuras = [d for d in datas if d >= ref]
    return min(futuras) if futuras else max(datas)


def _parse_recebido_dias(recebido: str | None) -> int | None:
    """Texto relativo de `recebido` -> nº aproximado de dias atrás (±1 dia)."""
    if not recebido:
        return None
    m = _RECEBIDO_REL_RE.search(recebido)
    if not m:
        return None
    n, unidade = int(m.group(1)), m.group(2).lower()
    if unidade.startswith(("segundo", "minuto")):
        return 0
    if unidade.startswith("hora"):
        return round(n / 24)
    if unidade.startswith("dia"):
        return n
    if unidade.startswith("semana"):
        return n * 7
    if unidade.startswith("ano"):
        return n * 365
    return n * 30  # mês/meses (aproximado)


def _parse_data_longa(texto: str | None) -> date | None:
    """1ª data por extenso ("03 de Julho de 2026") num texto/HTML, se houver."""
    m = _DATA_LONGA_RE.search(texto or "")
    if not m:
        return None
    mes = _MESES_PT.get(m.group(2).lower())
    if not mes:
        return None
    try:
        return date(int(m.group(3)), mes, int(m.group(1)))
    except ValueError:
        return None


def _extrair_ids_pje(texto: str | None, max_ids: int = 30) -> list[str]:
    """Ids do PJe citados no texto, sem repetição, na ordem em que aparecem."""
    vistos: dict[str, None] = {}
    for m in _ID_PJE_RE.finditer(texto or ""):
        for id_pje in _ID_PJE_DIGITOS_RE.findall(m.group(1)):
            vistos.setdefault(id_pje)
        if len(vistos) >= max_ids:
            break
    return list(vistos)[:max_ids]


def _combina_busca(proc: dict[str, Any], busca: str) -> bool:
    """Filtro textual LOCAL (o search[value] server-side do inbox dá HTTP 500).

    Substring case-insensitive em todos os campos; com 4+ dígitos na busca,
    compara também só os dígitos (acha CNJ com ou sem pontuação).
    """
    alvo = (busca or "").strip().lower()
    if not alvo:
        return True
    blob = " ".join(str(v) for v in proc.values()).lower()
    if alvo in blob:
        return True
    digitos = re.sub(r"\D", "", alvo)
    return len(digitos) >= 4 and digitos in re.sub(r"\D", "", blob)


def _aviso_amostra_parcial(total: Any, avaliados: int) -> str | None:
    try:
        total = int(total)
    except (TypeError, ValueError):
        return None
    if total <= avaliados:
        return None
    return (
        f"ATENÇÃO: avaliados apenas {avaliados} de {total} processos da caixa — "
        "o resultado pode estar incompleto. Aumente o parâmetro de limite."
    )


# ---------------------------------------------------------------- tools

@mcp.tool
def listar_caixa(flow_id: int, pagina: int = 1, por_pagina: int = 25, busca: str = "") -> dict:
    """Lista os processos de uma caixa do SPA-PMT (fluxo).

    Caixas conhecidas da Procuradoria Patrimonial (use listar_fluxos
    para a lista atualizada com totais):
      12 = Processo Patrimonial
      26 = Processo Patrimonial - Procurador(a)-Chefe
      40 = TRF1 - Processos Judiciais 1º grau
      41 = TRF1 - Processos Judiciais 2º grau
      15 = Inventário e Partilha - Fiscal
      22 = Solicitação ao(a) Assessor(a) da Patrimonial

    Args:
        flow_id: id do fluxo (caixa) no SPA.
        pagina: página da listagem (1-based).
        por_pagina: processos por página.
        busca: filtro de texto livre — aplicado NO CLIENTE (enviar
            search[value] ao inbox do SPA responde HTTP 500, bug do sistema);
            com busca, baixamos até 500 linhas e filtramos localmente.
    """
    busca = (busca or "").strip()
    # com busca, baixa a caixa inteira (até 500) e pagina após o filtro local
    start = 0 if busca else (pagina - 1) * por_pagina
    length = 500 if busca else por_pagina

    with FetcherSession(follow_redirects=True) as sessao:
        r = _get_autenticado(sessao, f"{BASE_URL}/procedures?flow_id={flow_id}")
        nomes, data_url = _cabecalhos_e_dataurl(r)
        if data_url.startswith("/"):
            data_url = BASE_URL + data_url

        r2 = _get_autenticado(
            sessao,
            data_url,
            params=_params_datatables(len(nomes), start, length),
            headers=XHR_HEADERS,
        )
        dados = json.loads(r2.body)

    processos = [_montar_processo(nomes, linha) for linha in dados.get("data", [])]
    total_filtrado = dados.get("recordsFiltered")
    if busca:
        filtrados = [p for p in processos if _combina_busca(p, busca)]
        total_filtrado = len(filtrados)
        processos = filtrados[(pagina - 1) * por_pagina : pagina * por_pagina]
    resultado = {
        "flow_id": flow_id,
        "total": dados.get("recordsTotal"),
        "filtrados": total_filtrado,
        "pagina": pagina,
        "por_pagina": por_pagina,
        "processos": processos,
    }
    if busca:
        aviso = _aviso_amostra_parcial(dados.get("recordsTotal"), len(dados.get("data", [])))
        if aviso:
            resultado["aviso"] = aviso
    return resultado


@mcp.tool
def listar_fluxos() -> dict:
    """Lista as caixas (fluxos) do usuário no SPA-PMT com o total de
    processos em cada uma — o equivalente às abas de "Meus Processos"."""
    with FetcherSession(follow_redirects=True) as sessao:
        fluxos = _descobrir_fluxos(sessao)
    resultado = []
    for fid, nome in sorted(fluxos.items()):
        caixa = listar_caixa(fid, por_pagina=1)
        resultado.append({"flow_id": fid, "nome": nome, "total": caixa["total"]})
    return {"fluxos": resultado}


@mcp.tool
def buscar_processo(termo: str) -> dict:
    """Busca processos no SPA-PMT por número CNJ, nome de parte ou
    CPF/CNPJ (a busca global do topo do sistema)."""
    with FetcherSession(follow_redirects=True) as sessao:
        r = _get_autenticado(
            sessao,
            f"{BASE_URL}/procedures/search?gq={quote(termo)}",
            headers=XHR_HEADERS,
        )
        dados = json.loads(r.body)
    return {
        "total": dados.get("total_count"),
        "resultados": [
            {
                "id": item.get("id"),
                "descricao": item.get("text"),
                "url": f"{BASE_URL}/procedures/{item.get('id')}",
            }
            for item in dados.get("items", [])
        ],
    }


@mcp.tool
def obter_processo(id_processo: int) -> dict:
    """Abre um processo do SPA-PMT pelo id interno (ex.: 25615) e retorna
    os campos do cabeçalho (partes, classe, vara, responsável, status,
    prazos, observações) e a timeline de andamentos com seus documentos."""
    with FetcherSession(follow_redirects=True) as sessao:
        r = _get_autenticado(sessao, f"{BASE_URL}/procedures/{id_processo}")
    soup = BeautifulSoup(r.body.decode("utf-8", errors="replace"), "html.parser")

    campos: dict[str, str] = {}
    for forte in soup.find_all(["strong", "b"]):
        rotulo = " ".join(forte.get_text(" ").split()).rstrip(":")
        if not rotulo or len(rotulo) > 60 or forte.parent is None:
            continue
        texto_pai = " ".join(forte.parent.get_text(" ").split())
        valor = texto_pai.replace(" ".join(forte.get_text(" ").split()), "", 1).strip(" :")
        valor = re.sub(r"\s*Copiar\b", "", valor).strip()
        if valor and len(valor) < 500 and rotulo not in campos:
            campos[rotulo] = valor

    timeline = []
    for item in soup.select(".timeline-item"):
        texto = " ".join(item.get_text(" ").split())
        docs = [
            " ".join(d.get_text(" ").split())
            for d in item.select(".timeline-document")
            if d.get_text(strip=True)
        ]
        if texto or docs:
            timeline.append({"texto": texto[:600], "documentos": docs})

    secoes = [
        " ".join(h.get_text(" ").split())
        for h in soup.find_all(["h1", "h2", "h3", "h4"])
        if h.get_text(strip=True)
    ]
    return {
        "id": id_processo,
        "url": f"{BASE_URL}/procedures/{id_processo}",
        "campos": campos,
        "timeline": timeline,
        "secoes": secoes,
    }


@mcp.tool
def baixar_processo(id_processo: int, pasta: str = "~/Downloads") -> dict:
    """Baixa o PDF integral do processo do SPA-PMT (todas as peças num
    único arquivo, como o sistema monta) para a pasta indicada."""
    with FetcherSession(follow_redirects=True) as sessao:
        r = _get_autenticado(sessao, f"{BASE_URL}/procedures/{id_processo}/documents")
    if r.body[:4] != b"%PDF":
        raise RuntimeError(
            f"/procedures/{id_processo}/documents não retornou PDF (HTTP {r.status})."
        )
    destino = Path(pasta).expanduser() / f"processo-{id_processo}.pdf"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(r.body)
    return {"arquivo": str(destino), "bytes": len(r.body)}


def _cnj_digitos(numero: str) -> str:
    digitos = re.sub(r"\D", "", numero)
    if len(digitos) != 20:
        raise RuntimeError(
            f"Número CNJ inválido: {numero!r} (esperados 20 dígitos, vieram {len(digitos)})."
        )
    return digitos


# Origem da comunicação no SPA (soap_setting_id), validada nas caixas:
# 1 = TJPI 1º grau, 3 = TJPI (outra instância, mesmo CNJ), 11 = TRF1.
# O CNJ traz o ramo (J) e o tribunal (TR), mas não o grau — por isso a
# dedução só escolhe o padrão do tribunal; o valor exato vem de listar_caixa.
_SOAP_POR_TRIBUNAL = {("8", "18"): 1, ("4", "01"): 11}


def _soap_setting(cnj: str, soap_setting_id: int) -> int:
    """soap_setting_id informado, ou o padrão do tribunal deduzido do CNJ."""
    if soap_setting_id:
        return soap_setting_id
    return _SOAP_POR_TRIBUNAL.get((cnj[13], cnj[14:16]), 1)


def _chave_juntada(doc: dict) -> datetime:
    try:
        return datetime.strptime(doc.get("juntado_em") or "", "%d/%m/%Y %H:%M")
    except ValueError:
        return datetime.min


# rótulo de item da Pasta do Processo: "(99672168) - Despacho juntado em 29/06/2026 - 11:52"
DOC_JUDICIAL_RE = re.compile(
    r"\((\d+)\)\s*-\s*(.*?)\s*juntado em\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}:\d{2})"
)


@mcp.tool
def listar_documentos_judiciais(numero_processo: str, soap_setting_id: int = 0) -> dict:
    """Lista os documentos externos do processo judicial — a "Pasta do
    Processo" que o SPA baixa do PJe/TJPI por comunicação eletrônica
    (petição inicial, despachos, certidões, intimações etc.).

    Args:
        numero_processo: número CNJ, com ou sem pontuação.
        soap_setting_id: origem da comunicação (1 = TJPI 1º grau, 3 = TJPI
            outra instância, 11 = TRF1). 0 = deduzir do CNJ (TJPI → 1,
            TRF1 → 11); listar_caixa devolve o valor exato de cada processo.
    """
    cnj = _cnj_digitos(numero_processo)
    soap_setting_id = _soap_setting(cnj, soap_setting_id)
    with FetcherSession(follow_redirects=True) as sessao:
        r = _get_autenticado(
            sessao,
            f"{BASE_URL}/judicial_processes/{cnj}/documents?soap_setting_id={soap_setting_id}",
        )
    soup = BeautifulSoup(r.body.decode("utf-8", errors="replace"), "html.parser")

    documentos: dict[str, dict] = {}
    for a in soup.find_all("a", href=re.compile(r"viewer\.html\?file=")):
        m = re.search(r"/document/(\d+)\.pdf", unquote(a["href"]))
        if not m:
            continue
        doc_id = m.group(1)
        if doc_id in documentos:
            continue
        bloco = a
        for _ in range(5):
            texto = " ".join(bloco.get_text(" ").split())
            if len(texto) > 25 or bloco.parent is None:
                break
            bloco = bloco.parent
        mm = DOC_JUDICIAL_RE.search(texto)
        if mm and mm.group(1) == doc_id:
            documentos[doc_id] = {
                "id": doc_id,
                "descricao": mm.group(2).strip(),
                "juntado_em": f"{mm.group(3)} {mm.group(4)}",
            }
        else:
            documentos[doc_id] = {"id": doc_id, "descricao": texto[:100], "juntado_em": None}

    return {
        "numero_processo": numero_processo,
        "soap_setting_id": soap_setting_id,
        "total": len(documentos),
        # mais recente primeiro (sem data vão para o fim)
        "documentos": sorted(documentos.values(), key=_chave_juntada, reverse=True),
        "dica": "use baixar_documento_judicial com o id para obter o PDF; "
        "sem id, baixa os autos completos",
    }


@mcp.tool
def baixar_documento_judicial(
    numero_processo: str,
    documento_id: str = "",
    pasta: str = "~/Downloads",
    soap_setting_id: int = 0,
) -> dict:
    """Baixa um documento da Pasta do Processo judicial (PJe/TJPI) pelo id
    retornado por listar_documentos_judiciais — ou, sem `documento_id`,
    baixa os autos completos num único PDF. soap_setting_id 0 = deduzir
    do CNJ (ver listar_documentos_judiciais)."""
    cnj = _cnj_digitos(numero_processo)
    soap_setting_id = _soap_setting(cnj, soap_setting_id)
    if documento_id:
        caminho = f"/judicial_processes/{cnj}/document/{documento_id}.pdf"
        nome = f"doc-{documento_id}-{cnj}.pdf"
    else:
        caminho = f"/judicial_processes/{cnj}/documents.pdf"
        nome = f"autos-{cnj}.pdf"
    with FetcherSession(follow_redirects=True) as sessao:
        r = _get_autenticado(sessao, f"{BASE_URL}{caminho}?soap_setting_id={soap_setting_id}")
    if r.body[:4] != b"%PDF":
        raise RuntimeError(f"{caminho} não retornou PDF (HTTP {r.status}).")
    destino = Path(pasta).expanduser() / nome
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(r.body)
    return {"arquivo": str(destino), "bytes": len(r.body)}


# Cache dos últimos PDFs baixados: a leitura paginada de um documento longo
# chama a tool várias vezes e não deve rebaixar o arquivo do SPA.
_PDF_CACHE: OrderedDict[tuple[str, str, int], bytes] = OrderedDict()
_PDF_CACHE_LOCK = threading.Lock()
_PDF_CACHE_MAX = 5


def _pdf_da_pasta(cnj: str, documento_id: str, soap_setting_id: int) -> bytes:
    chave = (cnj, documento_id, soap_setting_id)
    with _PDF_CACHE_LOCK:
        if chave in _PDF_CACHE:
            _PDF_CACHE.move_to_end(chave)
            return _PDF_CACHE[chave]
    with FetcherSession(follow_redirects=True) as sessao:
        r = _get_autenticado(
            sessao,
            f"{BASE_URL}/judicial_processes/{cnj}/document/{documento_id}.pdf"
            f"?soap_setting_id={soap_setting_id}",
        )
    if r.body[:4] != b"%PDF":
        raise RuntimeError(
            f"Documento {documento_id} não retornou PDF (HTTP {r.status}). "
            "Pode ser sigilo ou indisponibilidade do tribunal."
        )
    with _PDF_CACHE_LOCK:
        _PDF_CACHE[chave] = r.body
        while len(_PDF_CACHE) > _PDF_CACHE_MAX:
            _PDF_CACHE.popitem(last=False)
    return r.body


@mcp.tool
def ler_documento_judicial(
    numero_processo: str,
    documento_id: str,
    pagina_inicial: int = 1,
    max_chars: int = 20000,
    soap_setting_id: int = 0,
) -> dict:
    """Extrai o TEXTO de um documento da Pasta do Processo judicial
    (despacho, decisão, sentença, petição...) direto na conversa, sem
    salvar arquivo. Use listar_documentos_judiciais para obter o id.

    Documento longo: o texto é truncado em max_chars e a resposta indica a
    próxima pagina_inicial para continuar (o PDF fica em cache — continuar
    a leitura não rebaixa o arquivo). Também detecta os ids do PJe citados
    no texto (ids_pje_detectados), úteis para citar peças em minutas.

    Anexo escaneado sem camada de texto vem vazio — a resposta avisa e o
    caminho é baixar_documento_judicial para OCR externo."""
    cnj = _cnj_digitos(numero_processo)
    conteudo = _pdf_da_pasta(cnj, documento_id, _soap_setting(cnj, soap_setting_id))

    import pymupdf

    partes: list[str] = []
    chars = 0
    tem_texto = False
    with pymupdf.open(stream=conteudo, filetype="pdf") as pdf:
        total_paginas = len(pdf)
        inicio = max(1, min(pagina_inicial, total_paginas))
        ultima_lida = inicio - 1
        for i in range(inicio - 1, total_paginas):
            txt = pdf[i].get_text("text").strip()
            tem_texto = tem_texto or bool(txt)
            partes.append(f"[página {i + 1}]\n{txt}")
            chars += len(partes[-1])
            ultima_lida = i + 1
            if chars >= max_chars:
                break

    texto = "\n\n".join(partes)[:max_chars]
    resultado: dict[str, Any] = {
        "numero_cnj": _formatar_cnj(cnj),
        "documento_id": documento_id,
        "total_paginas": total_paginas,
        "paginas_lidas": f"{inicio}-{ultima_lida}",
        "texto": texto,
    }
    ids_pje = _extrair_ids_pje(texto)
    if ids_pje:
        resultado["ids_pje_detectados"] = ids_pje
    if not tem_texto:
        resultado["aviso"] = (
            "PDF sem camada de texto nas páginas lidas (provavelmente "
            "digitalizado). Use baixar_documento_judicial e faça OCR."
        )
    elif ultima_lida < total_paginas:
        resultado["aviso"] = (
            f"Texto truncado (lidas as páginas {inicio}-{ultima_lida} de "
            f"{total_paginas}). Chame de novo com pagina_inicial={ultima_lida + 1}."
        )
    return resultado


@mcp.tool
def prazos_urgentes(
    dias: int = 30, por_fluxo: int = 100, incluir_atrasados: bool = True
) -> dict:
    """Varre todas as caixas do usuário no SPA-PMT e retorna os processos
    com prazo VIGENTE nos próximos `dias` dias, ordenados do mais urgente
    ao menos urgente (dias_restantes negativo = atrasado).

    O campo de prazo do SPA acumula prazos históricos; considera-se vigente
    a data futura mais próxima (ou, se todas passaram, a mais recente). O
    texto completo do campo vai em `prazo_completo` para conferência."""
    with FetcherSession(follow_redirects=True) as sessao:
        fluxos = _descobrir_fluxos(sessao)
    urgentes = []
    avisos: list[str] = []
    for fid, nome in fluxos.items():
        caixa = listar_caixa(fid, por_pagina=por_fluxo)
        aviso = _aviso_amostra_parcial(caixa.get("total"), len(caixa["processos"]))
        if aviso:
            avisos.append(f"[{nome}] {aviso}")
        for proc in caixa["processos"]:
            restam = proc.get("dias_para_prazo")
            if restam is None:
                continue
            if restam < 0 and not incluir_atrasados:
                continue
            if restam <= dias:
                # rótulo e hora do prazo vigente: o trecho do campo cuja data
                # bate com a data_prazo escolhida
                tipo_prazo = hora = None
                alvo = proc.get("data_prazo", "")
                for rotulo, dt, hr in PRAZO_RE.findall(proc.get("prazo") or ""):
                    if f"{dt[6:10]}-{dt[3:5]}-{dt[0:2]}" == alvo:
                        tipo_prazo, hora = rotulo.strip(), hr or None
                        break
                urgentes.append(
                    {
                        "fluxo": nome,
                        "id": proc.get("id"),
                        "url": proc.get("url"),
                        "numero_processo": _numero_processo(proc),
                        "tipo_prazo": tipo_prazo,
                        "data_prazo": proc.get("data_prazo"),
                        "hora": hora,
                        "dias_restantes": restam,
                        "prazo_completo": proc.get("prazo"),
                        "tarefa_atual": proc.get("Último andamento")
                        or proc.get("ultimo_andamento"),
                    }
                )
    urgentes.sort(key=lambda u: u["dias_restantes"])
    resultado = {
        "referencia": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "criterio": {"ate_dias": dias, "incluir_atrasados": incluir_atrasados},
        "prazos": urgentes,
    }
    if avisos:
        resultado["avisos"] = avisos
    return resultado


@mcp.tool
def entradas_recentes(flow_id: int, dias: int = 7, limite: int = 200) -> dict:
    """Processos que ENTRARAM numa caixa do SPA nos últimos `dias` dias,
    do mais recente ao mais antigo. A data de entrada é exata quando o SPA
    expõe o tooltip por extenso; senão é aproximada (±1 dia) a partir do
    texto relativo ("há 2 dias", "aproximadamente 1 mês")."""
    limite = max(1, min(limite, 500))
    caixa = listar_caixa(flow_id, por_pagina=limite)
    com_data = [p for p in caixa["processos"] if p.get("recebido_dias") is not None]
    filtrados = [p for p in com_data if p["recebido_dias"] <= dias]
    filtrados.sort(key=lambda p: p["recebido_dias"])
    hoje = date.today()
    resultado = {
        "flow_id": flow_id,
        "hoje": hoje.isoformat(),
        "janela_dias": dias,
        "desde": (hoje - timedelta(days=dias)).isoformat(),
        "total_na_caixa": caixa.get("total"),
        "avaliados": len(caixa["processos"]),
        "retornados": len(filtrados),
        "processos": filtrados,
    }
    if not filtrados and com_data:
        # janela vazia: diz quando foi a última entrada, para o vazio não
        # parecer falha de leitura (caixas que só retêm processos parados)
        ultimo = min(com_data, key=lambda p: p["recebido_dias"])
        resultado["entrada_mais_recente"] = {
            "id": ultimo.get("id"),
            "url": ultimo.get("url"),
            "data_recebido": ultimo.get("data_recebido"),
            "recebido_dias": ultimo["recebido_dias"],
        }
        resultado["observacao"] = (
            f"Nenhuma entrada nos últimos {dias} dias; a mais recente foi há "
            f"{ultimo['recebido_dias']} dias ({ultimo.get('data_recebido')})."
        )
    aviso = _aviso_amostra_parcial(caixa.get("total"), len(caixa["processos"]))
    if aviso:
        resultado["aviso"] = aviso
    return resultado


@mcp.tool
def triagem(dias: int = 3, max_processos: int = 40, max_documentos: int = 5) -> dict:
    """TRIAGEM em uma chamada — briefing da caixa do procurador: varre
    todas as caixas, junta o que entrou nos últimos `dias` dias e, para
    cada processo com integração judicial, resume os documentos mais
    recentes da Pasta do Processo (descrição/data, sem baixar PDF).
    Leitura passiva: não toma ciência nem executa passo de fluxo."""
    with FetcherSession(follow_redirects=True) as sessao:
        fluxos = _descobrir_fluxos(sessao)

    itens: list[dict[str, Any]] = []
    erros_caixa: list[dict[str, Any]] = []
    ultimas: list[dict[str, Any]] = []
    for fid, nome in fluxos.items():
        try:
            rec = entradas_recentes(fid, dias=dias)
        except Exception as exc:
            erros_caixa.append({"flow_id": fid, "caixa": nome, "erro": str(exc)})
            continue
        if "entrada_mais_recente" in rec:
            ultimas.append({"flow_id": fid, "caixa": nome, **rec["entrada_mais_recente"]})
        for p in rec["processos"]:
            itens.append({"flow_id": fid, "caixa": nome, **p})

    itens.sort(key=lambda i: i.get("recebido_dias", 10**6))
    truncado = len(itens) > max_processos
    itens = itens[:max_processos]

    def _anexar_pasta(item: dict[str, Any]) -> None:
        cnj = item.get("numero_cnj")
        sid = item.get("soap_setting_id")
        if not cnj or sid is None:
            item["pasta_erro"] = (
                "listagem sem numero_cnj/soap_setting_id — abra o url do "
                "processo manualmente."
            )
            return
        try:
            pasta = listar_documentos_judiciais(cnj, soap_setting_id=sid)
        except Exception as exc:
            item["pasta_erro"] = str(exc)[:200]
            return
        item["total_documentos"] = pasta["total"]
        item["documentos_recentes"] = [
            {"id": d["id"], "descricao": d["descricao"], "juntado_em": d["juntado_em"]}
            for d in pasta["documentos"][:max_documentos]
        ]

    # paralelo com teto: a integração MNI com o tribunal é lenta
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(_anexar_pasta, itens))

    hoje = date.today()
    resultado: dict[str, Any] = {
        "hoje": hoje.isoformat(),
        "janela_dias": dias,
        "desde": (hoje - timedelta(days=dias)).isoformat(),
        "caixas_varridas": [
            {"flow_id": f, "caixa": n} for f, n in sorted(fluxos.items())
        ],
        "erros_caixa": erros_caixa,
        "retornados": len(itens),
        "truncado": truncado,
        "observacao": (
            "Documentos resumidos (descrição/data). Para ler o teor use "
            "ler_documento_judicial; itens com pasta_erro exigem conferência "
            "manual no SPA."
        ),
        "processos": itens,
    }
    if not itens and ultimas:
        ultimas.sort(key=lambda u: u["recebido_dias"])
        resultado["entradas_mais_recentes"] = ultimas
        resultado["observacao"] = (
            f"Nenhuma entrada nas caixas nos últimos {dias} dias; a mais "
            f"recente foi há {ultimas[0]['recebido_dias']} dias "
            f"({ultimas[0]['caixa']}). Processos novos que saem logo da caixa "
            "(ex.: redistribuídos) não aparecem aqui — veja notificacoes."
        )
    return resultado


@mcp.tool
def notificacoes() -> dict:
    """Notificações do sino do SPA (avisos ao procurador logado)."""
    with FetcherSession(follow_redirects=True) as sessao:
        r = _get_autenticado(sessao, f"{BASE_URL}/notifications", headers=XHR_HEADERS)
    soup = BeautifulSoup(r.body.decode("utf-8", errors="replace"), "html.parser")
    badge = soup.find(class_="circle-notify")
    itens = []
    for a in soup.select(".listview a[href]"):
        texto = " ".join(a.get_text(" ").split())
        href = a.get("href") or ""
        if not texto or href in ("", "#"):
            continue
        if href.startswith("/"):
            href = BASE_URL + href
        itens.append({"texto": texto[:300], "url": href})
    return {
        "nao_lidas": int(badge.get_text(strip=True)) if badge and badge.get_text(strip=True).isdigit() else None,
        "notificacoes": itens,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
