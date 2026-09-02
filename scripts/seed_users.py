"""Create the first accounts.

    python -m scripts.seed_users                       # one operator, one candidate
    python -m scripts.seed_users --operator asha       # named, password generated
    python -m scripts.seed_users --list                # who exists already

Every account needs an operator to have created it, which leaves nobody to
create the first operator. This is that bootstrap, run once from a terminal
that already has the database — no self-service route to an operator account
exists over HTTP, deliberately: an operator reads assessments and records
hiring decisions.

Passwords are generated rather than chosen. They are printed ONCE, here, and
never again — only the scrypt hash is stored, so a lost password means a new
account rather than a lookup.
"""

import argparse
import sys

from src.gateway import users


def show_all() -> int:
    from src.state import db

    rows = db.query("SELECT username, role, full_name, created_at FROM users "
                    "ORDER BY role, username")
    if not rows:
        print("\n  No accounts yet. Run without --list to create the first pair.\n")
        return 0

    print(f"\n  {len(rows)} account(s)\n")
    for r in rows:
        print(f"    {r['role']:<10} {r['username']:<24} {r['full_name']}")
    print()
    return 0


def create(username: str, role: str, full_name: str) -> bool:
    if users.by_username(username):
        print(f"    \033[33m{username}\033[0m already exists — skipped")
        return False

    password = users.suggest_password()
    try:
        users.create(username, password, role, full_name)
    except users.UserError as exc:
        print(f"    \033[31m{username}\033[0m — {exc}")
        return False

    print(f"    \033[32m{role:<10}\033[0m {username}")
    print(f"    {'':<10} password: \033[1m{password}\033[0m")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator", default="operator")
    parser.add_argument("--candidate", default="candidate")
    parser.add_argument("--operator-name", default="Interview Operator")
    parser.add_argument("--candidate-name", default="Test Candidate")
    parser.add_argument("--list", action="store_true", help="show existing accounts")
    args = parser.parse_args(argv)

    if args.list:
        return show_all()

    print("\n\033[1mSEEDING ACCOUNTS\033[0m")
    print("-" * 62)
    made = 0
    made += create(args.operator, users.OPERATOR, args.operator_name)
    made += create(args.candidate, users.CANDIDATE, args.candidate_name)
    print("-" * 62)

    if made:
        print("\n  \033[33mWrite these down now.\033[0m Only the hash is stored, so a")
        print("  forgotten password means creating another account.\n")
    else:
        print("\n  Nothing to do — both accounts already existed.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
