-- Dev-only seed for local Docker MySQL (lovvdev).
-- Creates an R-ADMIN and an R-DATA-PROVIDER user so the admin proposal -> review
-- -> approve flow can be exercised end-to-end (admin cannot review own proposal,
-- so two distinct users are needed). Idempotent via fixed UUIDs.
USE lovvdev;

INSERT INTO users (id, email, email_verified, display_name, status, created_at, updated_at)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'admin@lovv.local',    TRUE, 'Local Admin',    'active', NOW(3), NOW(3)),
  ('00000000-0000-0000-0000-000000000002', 'provider@lovv.local', TRUE, 'Local Provider', 'active', NOW(3), NOW(3))
ON DUPLICATE KEY UPDATE updated_at = NOW(3);

INSERT INTO user_role_assignments (id, user_id, role_code, status, valid_from, created_at, updated_at)
VALUES
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-000000000001', 'R-ADMIN',         'active', NOW(3), NOW(3), NOW(3)),
  ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-000000000002', 'R-DATA-PROVIDER', 'active', NOW(3), NOW(3), NOW(3))
ON DUPLICATE KEY UPDATE updated_at = NOW(3);

SELECT u.id, u.display_name, a.role_code
FROM users u JOIN user_role_assignments a ON a.user_id = u.id
WHERE a.status = 'active'
ORDER BY a.role_code;
