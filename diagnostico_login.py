"""Diagnóstico de login no SPA-PMT (spa.pmt.pi.gov.br) — HTTP puro via Scrapling.

O SPA não tem Cloudflare, então o login Devise (Rails) funciona sem browser:
  1. GET  /users/sign_in  -> extrai authenticity_token (CSRF) do form#new_user
  2. POST /users/sign_in  -> user[email] + user[password] do Keychain (service mcp-spa)
  3. GET  /procedures     -> prova real de que a sessão autenticada funciona

Imprime URL final, título e itens de menu/navegação — pra mapearmos as telas.
Também salva:
  /tmp/spa_pos_login.html   — HTML da página pós-login
  /tmp/spa_procedures.html  — HTML do inbox de procedimentos
  ~/.mcp-spa-session.json   — cookies da sessão (reuso futuro pelo servidor MCP)

Fallback com Chrome real (headed): uv run diagnostico_login_browser.py

Rode: uv run diagnostico_login.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import keyring
from scrapling.fetchers import FetcherSession

SERVICE = "mcp-spa"
BASE_URL = "https://spa.pmt.pi.gov.br"
SESSION_FILE = Path.home() / ".mcp-spa-session.json"


def _listar_navegacao(pagina) -> None:
    """Imprime links visíveis com texto, pra entender os módulos do sistema."""
    vistos: set[tuple[str, str]] = set()
    for a in pagina.css("a"):
        texto = " ".join((a.text or "").split())
        href = a.attrib.get("href") or ""
        if not texto or not href or href.startswith(("#", "javascript")):
            continue
        chave = (texto, href)
        if chave in vistos:
            continue
        vistos.add(chave)
        print(f"  - {texto!r:50s} -> {href}")
        if len(vistos) >= 60:
            print("  ... (limitado a 60)")
            break


def main() -> int:
    email = keyring.get_password(SERVICE, "email")
    senha = keyring.get_password(SERVICE, "senha")
    if not email or not senha:
        print("[ERRO] Credenciais não encontradas no Keychain. "
              "Rode antes: uv run setup_credenciais.py", file=sys.stderr)
        return 1

    cookies: dict[str, str] = {}
    with FetcherSession(follow_redirects=True) as sessao:
        print(f"[LOGIN] GET {BASE_URL}/users/sign_in ...")
        r = sessao.get(f"{BASE_URL}/users/sign_in")
        cookies.update(r.cookies)
        if r.status != 200:
            print(f"[ERRO] Tela de login retornou HTTP {r.status}", file=sys.stderr)
            return 1

        form = None
        for f in r.css("form"):
            if f.attrib.get("id") == "new_user":
                form = f
                break
        if form is None:
            print("[ERRO] form#new_user não encontrado — layout mudou? "
                  "Use o fallback: uv run diagnostico_login_browser.py",
                  file=sys.stderr)
            return 1

        # Campos hidden vêm do próprio form (utf8, authenticity_token,
        # user[remember_me]...) — não hardcodar valores de CSRF.
        dados = {
            i.attrib.get("name"): i.attrib.get("value") or ""
            for i in form.css("input[type=hidden]")
            if i.attrib.get("name")
        }
        dados["user[email]"] = email
        dados["user[password]"] = senha
        dados["user[remember_me]"] = "1"
        acao = form.attrib.get("action") or "/users/sign_in"

        print(f"[LOGIN] POST {acao} (token CSRF de {len(dados.get('authenticity_token', ''))} chars)...")
        r = sessao.post(f"{BASE_URL}{acao}", data=dados)
        cookies.update(r.cookies)

        titulo = r.css("title::text")
        titulo = str(titulo[0]).strip() if titulo else "(sem título)"
        print(f"[DIAG] HTTP {r.status} | URL final: {r.url}")
        print(f"[DIAG] Título: {titulo}")

        if "/users/sign_in" in r.url:
            alertas = [" ".join(a.text.split()) for a in r.css(".alert, .flash, .error")]
            print(f"[DIAG] AINDA NA TELA DE LOGIN. Alertas: {alertas}", file=sys.stderr)
            return 1

        Path("/tmp/spa_pos_login.html").write_bytes(r.body)
        print("[DIAG] Links de navegação pós-login:")
        _listar_navegacao(r)

        # Prova real: rota interna conhecida do recon
        print(f"\n[DIAG] GET {BASE_URL}/procedures (rota autenticada)...")
        r2 = sessao.get(f"{BASE_URL}/procedures")
        cookies.update(r2.cookies)
        print(f"[DIAG] HTTP {r2.status} | URL final: {r2.url}")
        if "/users/sign_in" in r2.url:
            print("[DIAG] /procedures redirecionou pro login — sessão NÃO segurou.",
                  file=sys.stderr)
            return 1
        Path("/tmp/spa_procedures.html").write_bytes(r2.body)

    SESSION_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
    SESSION_FILE.chmod(0o600)
    print(f"\n[OK] Login HTTP puro funcionou. Cookies salvos em {SESSION_FILE}")
    print("[OK] Salvos /tmp/spa_pos_login.html e /tmp/spa_procedures.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
