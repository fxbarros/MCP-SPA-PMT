"""Diagnóstico de login no SPA-PMT (spa.pmt.pi.gov.br).

Abre o Chrome real (headed) com perfil persistente, faz login via email/senha
do Keychain (service mcp-spa) e imprime o que encontra depois do login:
URL final, título e itens de menu/navegação — pra mapearmos as telas do sistema.

Também salva:
  /tmp/spa_pos_login.html  — HTML da página pós-login
  /tmp/spa_pos_login.png   — screenshot

Rode: uv run diagnostico_login.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import keyring
from patchright.sync_api import sync_playwright

SERVICE = "mcp-spa"
BASE_URL = "https://spa.pmt.pi.gov.br"
PROFILE_DIR = Path.home() / ".mcp-spa-chrome-profile"


def main() -> int:
    email = keyring.get_password(SERVICE, "email")
    senha = keyring.get_password(SERVICE, "senha")
    if not email or not senha:
        print("[ERRO] Credenciais não encontradas no Keychain. "
              "Rode antes: uv run setup_credenciais.py", file=sys.stderr)
        return 1

    print(f"[LOGIN] Perfil persistente: {PROFILE_DIR}")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            no_viewport=True,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        print(f"[LOGIN] Abrindo {BASE_URL}/users/sign_in ...")
        page.goto(f"{BASE_URL}/users/sign_in", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        if "/users/sign_in" not in page.url:
            print(f"[LOGIN] Sessão já ativa — redirecionado para {page.url}")
        else:
            print("[LOGIN] Preenchendo formulário de email/senha...")
            page.fill("#email", email)
            page.fill("#password", senha)
            page.click("form#new_user input[type=submit]")
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(3000)

        print(f"[DIAG] URL final : {page.url}")
        print(f"[DIAG] Título    : {page.title()}")

        if "/users/sign_in" in page.url:
            alerta = page.locator(".alert").all_inner_texts()
            print(f"[DIAG] AINDA NA TELA DE LOGIN. Alertas: {alerta}")

        # Mapeia navegação: links visíveis com texto, pra entender os módulos
        print("[DIAG] Links de navegação encontrados:")
        vistos: set[tuple[str, str]] = set()
        for a in page.locator("nav a, .sidebar a, .navigation a, header a, a").all():
            try:
                texto = " ".join(a.inner_text().split())
                href = a.get_attribute("href") or ""
            except Exception:
                continue
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

        Path("/tmp/spa_pos_login.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path="/tmp/spa_pos_login.png", full_page=True)
        print("[DIAG] Salvos /tmp/spa_pos_login.html e /tmp/spa_pos_login.png")

        input("\n[DIAG] Navegue à vontade pra eu ver depois. ENTER pra fechar... ")
        ctx.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
