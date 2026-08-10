"""Salva credenciais do SPA-PMT no Keychain do macOS.

Service name: mcp-spa
Itens armazenados:
  - email  (login do SPA, autenticação via email)
  - senha  (senha do SPA)

Rode uma vez: uv run setup_credenciais.py
"""

from __future__ import annotations

import getpass
import sys

import keyring

SERVICE = "mcp-spa"


def _set(key: str, value: str) -> None:
    keyring.set_password(SERVICE, key, value)
    print(f"  [{key}] salvo no Keychain (service={SERVICE})")


def main() -> int:
    print(f"Configuração de credenciais para o SPA-PMT (service={SERVICE})")
    print("Os dados ficam no Keychain do macOS, nunca em arquivo.\n")

    email = input("Email do SPA: ").strip()
    if not email:
        print("ERRO: email vazio.", file=sys.stderr)
        return 1

    senha = getpass.getpass("Senha do SPA: ")
    if not senha:
        print("ERRO: senha vazia.", file=sys.stderr)
        return 1

    _set("email", email)
    _set("senha", senha)

    print("\nOK. Para testar: uv run diagnostico_login.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
