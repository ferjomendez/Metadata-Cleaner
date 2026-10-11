"""Administrative commands, run inside the api container:

  docker compose exec api python -m app.cli create-admin
  docker compose exec api python -m app.cli create-user
  docker compose exec api python -m app.cli reset-password <username>
  docker compose exec api python -m app.cli disable-user <username>
  docker compose exec api python -m app.cli list-users

Passwords are always prompted (never taken from arguments or environment).
"""

from __future__ import annotations

import argparse
import getpass
import sys

from . import auth
from .config import load_settings
from .db import connect, init_db


def _prompt_password(username: str) -> str:
    for _ in range(3):
        p1 = getpass.getpass(f"Contraseña para {username} (mínimo {auth.MIN_PASSWORD} caracteres): ")
        p2 = getpass.getpass("Repite la contraseña: ")
        if p1 != p2:
            print("Las contraseñas no coinciden.", file=sys.stderr)
            continue
        try:
            auth.check_password_policy(p1, username)
            return p1
        except auth.PasswordPolicyError as exc:
            print(f"Contraseña no válida: {exc}", file=sys.stderr)
    raise SystemExit(1)


def _username(value: str | None) -> str:
    return value or input("Nombre de usuario: ").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("create-admin", "create-user"):
        p = sub.add_parser(name)
        p.add_argument("--username")
    for name in ("reset-password", "disable-user", "enable-user"):
        p = sub.add_parser(name)
        p.add_argument("username")
    sub.add_parser("list-users")
    args = parser.parse_args(argv)

    settings = load_settings()
    init_db(settings.db_path)
    conn = connect(settings.db_path)
    try:
        if args.cmd in ("create-admin", "create-user"):
            username = _username(args.username)
            password = _prompt_password(username)
            try:
                auth.create_user(conn, username, password, is_admin=args.cmd == "create-admin")
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            print(f"Usuario '{username}' creado{' como administrador' if args.cmd == 'create-admin' else ''}.")
        elif args.cmd == "list-users":
            for u in conn.execute("SELECT username, is_admin, is_active FROM users ORDER BY username"):
                print(f"{u['username']}\t{'admin' if u['is_admin'] else 'user'}\t"
                      f"{'activo' if u['is_active'] else 'deshabilitado'}")
        else:
            u = conn.execute("SELECT * FROM users WHERE username = ?", (args.username,)).fetchone()
            if u is None:
                print("Usuario no encontrado.", file=sys.stderr)
                return 1
            if args.cmd == "reset-password":
                auth.set_password(conn, u["id"], _prompt_password(u["username"]), u["username"])
                auth.revoke_user_sessions(conn, u["id"])
                print("Contraseña actualizada y sesiones cerradas.")
            elif args.cmd == "disable-user":
                conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (u["id"],))
                auth.revoke_user_sessions(conn, u["id"])
                print("Usuario deshabilitado.")
            elif args.cmd == "enable-user":
                conn.execute("UPDATE users SET is_active = 1, failed_logins = 0, locked_until = 0 WHERE id = ?",
                             (u["id"],))
                print("Usuario habilitado.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
