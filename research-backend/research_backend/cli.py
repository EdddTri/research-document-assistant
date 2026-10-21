import argparse
import getpass
from sqlalchemy import select
from research_core.db import session_factory
from research_core.models import User
from .auth import hasher


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create-admin"])
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    password = getpass.getpass("Admin password (at least 6 characters): ")
    if len(password) < 6:
        parser.error("Password must be at least 6 characters")
    with session_factory()() as db:
        if db.scalar(select(User).where(User.email == args.email.lower())):
            parser.error("Account already exists; use audited database administration to change its role")
        db.add(User(email=args.email.lower(), password_hash=hasher.hash(password), role="admin"))
        db.commit()
    print("Admin created.")


if __name__ == "__main__":
    main()
