#!/usr/bin/env python3
"""Dev-only: diagnose the admin migration FK failure against the live DB.

Run with the SSM tunnel open (RDS_LOCAL_PORT) and RDS_SECRET_ARN set, same as
check_admin_migration.py.
"""
import json
import os
import sys

try:
    import pymysql
except ImportError:
    sys.exit("pymysql is not installed. Run: pip install pymysql")


def resolve_credentials():
    user = os.environ.get("RDS_USER")
    password = os.environ.get("RDS_PW")
    if password:
        return (user or "lovvadmin"), password
    secret_arn = os.environ.get("RDS_SECRET_ARN")
    if not secret_arn:
        sys.exit("Set RDS_PW, or set RDS_SECRET_ARN to load credentials via boto3.")
    import boto3
    raw = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)["SecretString"]
    secret = json.loads(raw)
    return (user or secret.get("username")), secret.get("password")


host = os.environ.get("RDS_LOCAL_HOST", "127.0.0.1")
port = int(os.environ.get("RDS_LOCAL_PORT", "3306"))
database = os.environ.get("RDS_DATABASE", "lovvdev")
user, password = resolve_credentials()

conn = pymysql.connect(host=host, port=port, user=user, password=password, database=database)
try:
    with conn.cursor() as cur:
        print("=== users.id / granted_by reference column ===")
        cur.execute(
            "SELECT column_name, column_type, character_set_name, collation_name, column_key "
            "FROM information_schema.columns WHERE table_schema=%s AND table_name='users' AND column_name='id'",
            [database],
        )
        for row in cur.fetchall():
            print(row)

        print("\n=== SHOW CREATE TABLE users ===")
        cur.execute("SHOW CREATE TABLE users")
        print(cur.fetchone()[1])

        print("\n=== SHOW CREATE TABLE admin_organizations ===")
        try:
            cur.execute("SHOW CREATE TABLE admin_organizations")
            print(cur.fetchone()[1])
        except Exception as e:
            print("(not present:", e, ")")

        print("\n=== LATEST FOREIGN KEY ERROR (InnoDB) ===")
        cur.execute("SHOW ENGINE INNODB STATUS")
        status = cur.fetchone()[2]
        marker = "LATEST FOREIGN KEY ERROR"
        if marker in status:
            section = status.split(marker, 1)[1]
            section = section.split("------------\n", 1)[0]
            print(marker + section[:2000])
        else:
            print("(no recent foreign key error recorded)")
finally:
    conn.close()
