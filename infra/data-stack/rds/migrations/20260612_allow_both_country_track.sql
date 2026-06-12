-- Allow the API fallback countryTrack value used when the frontend does not send a country filter.
ALTER TABLE user_preferences
  DROP CHECK chk_user_preferences_country;

ALTER TABLE user_preferences
  ADD CONSTRAINT chk_user_preferences_country
  CHECK (country_track IN ('KR', 'JP', 'BOTH'));
