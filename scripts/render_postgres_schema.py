from __future__ import annotations

from app.database import postgres_schema_statements


def main() -> None:
    print("-- Generated from app/schema.py; review before applying.\n")
    for statement in postgres_schema_statements():
        print(statement.rstrip() + ";\n")


if __name__ == "__main__":
    main()
