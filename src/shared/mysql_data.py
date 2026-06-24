# @file src/shared/mysql_data.py
# @description Direct MySQL adapter for existing Lovv Data Stack RDS.
# @lastModified 2026-06-12

import json
import os
import re
from datetime import date, datetime, timezone
from decimal import Decimal


class MySqlConfigurationError(Exception):
    pass


_NAMED_PARAMETER_PATTERN = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
_UTC_ISO_STRING_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class MySqlClient:
    def __init__(
        self,
        host=None,
        database=None,
        secret_arn=None,
        username=None,
        password=None,
        port=None,
        secret_loader=None,
        connection_factory=None,
    ):
        self.host = host or os.environ.get("MYSQL_HOST") or os.environ.get("RDS_HOST")
        self.database = database or os.environ.get("MYSQL_DATABASE") or os.environ.get("RDS_DATABASE_NAME")
        self.secret_arn = secret_arn or os.environ.get("MYSQL_SECRET_ARN") or os.environ.get("RDS_SECRET_ARN")
        self.port = int(port or os.environ.get("MYSQL_PORT") or "3306")
        self.secret_loader = secret_loader or _load_secret
        self.connection_factory = connection_factory or _pymysql_connection_factory

        # Credential precedence: explicit args -> env (local Docker / dev) -> Secrets
        # Manager. The env path lets DB_ACCESS_MODE=mysql connect to a local MySQL
        # without a secret; the secret is only loaded when creds are still missing.
        username = username or os.environ.get("MYSQL_USER") or os.environ.get("RDS_USER")
        password = password or os.environ.get("MYSQL_PASSWORD") or os.environ.get("RDS_PW")

        secret = {}
        if self.secret_arn and (not username or not password):
            secret = _parse_secret(self.secret_loader(self.secret_arn))

        self.username = username or secret.get("username")
        self.password = password or secret.get("password")
        self.host = self.host or secret.get("host")
        self.database = self.database or secret.get("dbname") or secret.get("database")
        self.port = int(secret.get("port") or self.port)

        if not self.host or not self.database or not self.username or not self.password:
            raise MySqlConfigurationError("MySQL connection configuration is missing")

    def execute(self, sql, parameters=None, include_result_metadata=True):
        # Repositories use RDS Data API-style named parameters; PyMySQL needs positional values.
        translated_sql, values = translate_named_parameters(sql, parameters or {})
        values = [_mysql_parameter_value(value) for value in values]
        connection = self.connection_factory(
            host=self.host,
            port=self.port,
            user=self.username,
            password=self.password,
            database=self.database,
            charset="utf8mb4",
            autocommit=False,
            cursorclass=_dict_cursor_class(),
        )
        try:
            with connection.cursor() as cursor:
                row_count = cursor.execute(translated_sql, values)
                if include_result_metadata:
                    return [_api_row(row) for row in cursor.fetchall()]
                connection.commit()
                return {"numberOfRecordsUpdated": row_count}
        finally:
            connection.close()

    def fetch_one(self, sql, parameters=None):
        rows = self.fetch_all(sql, parameters)
        return rows[0] if rows else None

    def fetch_all(self, sql, parameters=None):
        return self.execute(sql, parameters, include_result_metadata=True)


def translate_named_parameters(sql, parameters):
    values = []

    def replace(match):
        name = match.group(1)
        if name not in parameters:
            raise MySqlConfigurationError(f"MySQL SQL parameter is missing: {name}")
        values.append(parameters[name])
        return "%s"

    return _NAMED_PARAMETER_PATTERN.sub(replace, sql), values


def _mysql_parameter_value(value):
    if isinstance(value, str) and _UTC_ISO_STRING_PATTERN.match(value):
        return value.replace("T", " ").removesuffix("Z")
    return value


def _api_row(row):
    if not isinstance(row, dict):
        return row
    return {key: _api_value(value) for key, value in row.items()}


def _api_value(value):
    # Normalize MySQL-native values to the JSON-friendly shape expected by existing API adapters.
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return f"{value.replace(microsecond=0).isoformat()}Z"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _parse_secret(secret):
    if isinstance(secret, dict):
        return secret
    try:
        return json.loads(secret or "{}")
    except json.JSONDecodeError as error:
        raise MySqlConfigurationError("MySQL secret must be valid JSON") from error


_secret_cache = {}


def _load_secret(secret_arn):
    global _secret_cache
    if secret_arn in _secret_cache:
        return _secret_cache[secret_arn]

    try:
        import boto3
    except ImportError as error:
        raise MySqlConfigurationError("boto3 is required to load MySQL credentials from Secrets Manager") from error

    response = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
    if "SecretString" not in response:
        raise MySqlConfigurationError("MySQL secret must contain SecretString")

    _secret_cache[secret_arn] = response["SecretString"]
    return _secret_cache[secret_arn]


def _pymysql_connection_factory(**kwargs):
    try:
        import pymysql
    except ImportError as error:
        raise MySqlConfigurationError("pymysql is required for direct MySQL access") from error
    return pymysql.connect(**kwargs)


def _dict_cursor_class():
    try:
        import pymysql
    except ImportError:
        return None
    return pymysql.cursors.DictCursor


# EOF: src/shared/mysql_data.py
