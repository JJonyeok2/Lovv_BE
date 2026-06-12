# @file src/shared/database.py
# @description Selects the database client for Lambda repositories.
# @lastModified 2026-06-12

import os

from shared.mysql_data import MySqlClient
from shared.rds_data import RdsDataClient


def create_database_client():
    access_mode = (os.environ.get("DB_ACCESS_MODE") or "aurora-data-api").strip().lower()
    # Existing Lovv Data Stack uses direct MySQL; legacy configs can still use Data API mode.
    if access_mode in ("mysql", "rds-mysql", "direct-mysql"):
        return MySqlClient()
    return RdsDataClient()


# EOF: src/shared/database.py
