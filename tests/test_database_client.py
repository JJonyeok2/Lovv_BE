import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from auth.user_repository import RdsDataUserRepository
from preferences.repository import RdsDataPreferenceRepository
from saved_plans.repository import RdsDataSavedPlanRepository
from shared.database import create_database_client


class DatabaseClientFactoryTest(unittest.TestCase):
    def test_defaults_to_aurora_data_api_client(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("shared.database.RdsDataClient", return_value="rds-client") as rds_client:
                self.assertEqual(create_database_client(), "rds-client")

        rds_client.assert_called_once_with()

    def test_uses_mysql_client_when_database_access_mode_is_mysql(self):
        with patch.dict(os.environ, {"DB_ACCESS_MODE": "mysql"}, clear=True):
            with patch("shared.database.MySqlClient", return_value="mysql-client") as mysql_client:
                self.assertEqual(create_database_client(), "mysql-client")

        mysql_client.assert_called_once_with()


class RepositoryClientSelectionTest(unittest.TestCase):
    def test_user_repository_from_env_uses_database_client_factory(self):
        with patch("auth.user_repository.create_database_client", return_value="db-client"):
            repository = RdsDataUserRepository.from_env()

        self.assertEqual(repository.rds, "db-client")

    def test_preference_repository_from_env_uses_database_client_factory(self):
        with patch("preferences.repository.create_database_client", return_value="db-client"):
            repository = RdsDataPreferenceRepository.from_env()

        self.assertEqual(repository.rds, "db-client")
        self.assertEqual(repository.table_name, "user_preferences")

    def test_saved_plan_repository_from_env_uses_database_client_factory(self):
        with patch("saved_plans.repository.create_database_client", return_value="db-client"):
            repository = RdsDataSavedPlanRepository.from_env()

        self.assertEqual(repository.rds, "db-client")


if __name__ == "__main__":
    unittest.main()
