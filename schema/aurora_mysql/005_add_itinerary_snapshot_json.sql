ALTER TABLE itineraries
  ADD COLUMN itinerary_json JSON NULL AFTER conditions_snapshot_json;
