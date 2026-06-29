SET @itinerary_json_exists := (
  SELECT COUNT(*)
  FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'itineraries'
    AND COLUMN_NAME = 'itinerary_json'
);

SET @add_itinerary_json_sql := IF(
  @itinerary_json_exists = 0,
  'ALTER TABLE itineraries ADD COLUMN itinerary_json JSON NULL AFTER conditions_snapshot_json',
  'SELECT 1'
);

PREPARE add_itinerary_json_stmt FROM @add_itinerary_json_sql;
EXECUTE add_itinerary_json_stmt;
DEALLOCATE PREPARE add_itinerary_json_stmt;
