"""One-shot DB bootstrap: create ZMEM app user + schema on zmemory-adb.

Reads credentials from ~/.secrets.d/zmemory-adb.env. Idempotent-ish:
drops/creates ZMEM cleanly and (re)creates tables if absent.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import oracledb

SECRETS = Path.home() / ".secrets.d" / "zmemory-adb.env"
WALLET = Path.home() / ".secrets.d" / "zmemory-wallet"


def load_env() -> dict:
    env = {}
    for line in SECRETS.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def admin_conn(env):
    return oracledb.connect(
        user="ADMIN",
        password=env["ADB_ADMIN_PASSWORD"],
        dsn="zmemory_high",
        config_dir=str(WALLET),
        wallet_location=str(WALLET),
        wallet_password=env["ADB_WALLET_PASSWORD"],
    )


def zmem_conn(env):
    return oracledb.connect(
        user="ZMEM",
        password=env["ZMEM_SCHEMA_PASSWORD"],
        dsn="zmemory_high",
        config_dir=str(WALLET),
        wallet_location=str(WALLET),
        wallet_password=env["ADB_WALLET_PASSWORD"],
    )


def run(cur, stmt: str):
    try:
        cur.execute(stmt)
        print("  ok:", stmt.split("\n")[0][:70])
    except oracledb.DatabaseError as e:
        (err,) = e.args
        # tolerate "already exists" / "does not exist" idempotency errors
        if err.code in (955, 1920, 942, 1418, 2289, 1408):
            print(f"  skip ({err.code}):", stmt.split("\n")[0][:60])
        else:
            raise


def main():
    env = load_env()
    pw = env["ZMEM_SCHEMA_PASSWORD"]

    print("[1/3] ADMIN: create ZMEM user + grants")
    with admin_conn(env) as c, c.cursor() as cur:
        run(cur, f'CREATE USER ZMEM IDENTIFIED BY "{pw}" '
                 f'DEFAULT TABLESPACE DATA QUOTA UNLIMITED ON DATA')
        for g in (
            "CREATE SESSION", "CREATE TABLE", "CREATE VIEW", "CREATE SEQUENCE",
            "CREATE PROCEDURE", "CREATE MINING MODEL",
        ):
            run(cur, f"GRANT {g} TO ZMEM")
        for obj in ("DBMS_VECTOR", "DBMS_VECTOR_CHAIN", "DBMS_CLOUD"):
            run(cur, f"GRANT EXECUTE ON {obj} TO ZMEM")
        c.commit()

    print("[2/3] ZMEM: create schema")
    schema = (Path(__file__).parent / "02_schema.sql").read_text()
    stmts = [s.strip() for s in schema.split(";\n") if s.strip()
             and not s.strip().startswith("--")]
    with zmem_conn(env) as c, c.cursor() as cur:
        for s in stmts:
            # strip trailing comment-only lines
            body = "\n".join(l for l in s.splitlines()
                             if not l.strip().startswith("--")).strip()
            if body:
                run(cur, body)
        c.commit()

    print("[3/3] verify")
    with zmem_conn(env) as c, c.cursor() as cur:
        cur.execute("SELECT table_name FROM user_tables ORDER BY table_name")
        print("  tables:", [r[0] for r in cur.fetchall()])
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
