"""Administrative commands, run inside the api container:

  docker compose exec api python -m app.cli create-admin
  docker compose exec api python -m app.cli create-user
  docker compose exec api python -m app.cli reset-password <username>
  docker compose exec api python -m app.cli disable-user <username>
  docker compose exec api python -m app.cli list-users
  docker compose exec api python -m app.cli delete-user <username> --yes

Passwords are prompted (never taken from arguments or environment). For
scripts, --password-stdin reads the password from standard input instead.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from . import auth, storage
from .config import load_settings
from .db import connect, init_db


def _prompt_password(username: str, from_stdin: bool = False) -> str:
    if from_stdin:
        p = sys.stdin.readline().rstrip("\r\n")
        try:
            auth.check_password_policy(p, username)
        except auth.PasswordPolicyError as exc:
            print(f"Contraseña no válida: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        return p
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
        p.add_argument("--password-stdin", action="store_true")
    for name in ("reset-password", "disable-user", "enable-user", "delete-user"):
        p = sub.add_parser(name)
        p.add_argument("username")
        if name == "reset-password":
            p.add_argument("--password-stdin", action="store_true")
        if name == "delete-user":
            p.add_argument("--yes", action="store_true", help="confirm deletion")
    sub.add_parser("list-users")
    args = parser.parse_args(argv)

    settings = load_settings()
    init_db(settings.db_path)
    conn = connect(settings.db_path)
    try:
        if args.cmd in ("create-admin", "create-user"):
            username = _username(args.username)
            password = _prompt_password(username, args.password_stdin)
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
                auth.set_password(conn, u["id"], _prompt_password(u["username"], args.password_stdin), u["username"])
                auth.revoke_user_sessions(conn, u["id"])
                print("Contraseña actualizada y sesiones cerradas.")
            elif args.cmd == "disable-user":
                conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (u["id"],))
                auth.revoke_user_sessions(conn, u["id"])
                print("Usuario deshabilitado.")
            elif args.cmd == "delete-user":
                if not args.yes:
                    print("Agrega --yes para confirmar.", file=sys.stderr)
                    return 1
                busy = conn.execute(
                    """SELECT COUNT(*) FROM jobs WHERE user_id = ?
                       AND (status IN ('queued', 'processing') OR files_deleted_at IS NULL AND status != 'draft')""",
                    (u["id"],)).fetchone()[0]
                drafts = conn.execute("SELECT storage_key FROM jobs WHERE user_id = ? AND status = 'draft'",
                                      (u["id"],)).fetchall()
                if busy:
                    print("El usuario tiene trabajos con archivos aún almacenados. Espera a que venzan o elimínalos.",
                          file=sys.stderr)
                    return 1
                for d in drafts:
                    storage.delete_path(storage.incoming_job_dir(settings, d["storage_key"]), settings, conn)
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (u["id"],))
                conn.execute("DELETE FROM jobs WHERE user_id = ?", (u["id"],))
                conn.execute("DELETE FROM users WHERE id = ?", (u["id"],))
                print("Usuario eliminado.")
            elif args.cmd == "enable-user":
                conn.execute("UPDATE users SET is_active = 1, failed_logins = 0, locked_until = 0 WHERE id = ?",
                             (u["id"],))
                print("Usuario habilitado.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
