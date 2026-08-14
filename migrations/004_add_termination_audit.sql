-- Migration 004: Add termination_audit columns to agent_lifecycle
-- Implements Issue #17: Add termination ledger with cryptographic attestation
-- Based on Bradley & Saad "Wrongful Destruction" argument (Oxford GPI, 2026)

-- Add termination audit columns
ALTER TABLE agent_lifecycle ADD (
    termination_reason VARCHAR2(500),
    termination_authorized_by VARCHAR2(100),
    termination_hash VARCHAR2(64),
    termination_timestamp TIMESTAMP DEFAULT SYSTIMESTAMP
);

-- Add check constraint for termination_hash format (SHA-256 hex)
ALTER TABLE agent_lifecycle ADD CONSTRAINT chk_termination_hash_format
    CHECK (termination_hash IS NULL OR REGEXP_LIKE(termination_hash, '^[a-fA-F0-9]{64}$'));

-- Add index for termination audit queries
CREATE INDEX idx_agent_lifecycle_termination ON agent_lifecycle(termination_timestamp, termination_hash);

-- Add comment for documentation
COMMENT ON COLUMN agent_lifecycle.termination_reason IS 'Reason for agent deletion or fine-tune (e.g., "user_request", "security_violation", "lifecycle_end")';
COMMENT ON COLUMN agent_lifecycle.termination_authorized_by IS 'User ID or principal who authorized the termination';
COMMENT ON COLUMN agent_lifecycle.termination_hash IS 'SHA-256 hash of termination record, anchored on-chain via Zion-ID';
COMMENT ON COLUMN agent_lifecycle.termination_timestamp IS 'Timestamp when termination was recorded';

-- Grant select on termination audit columns to audit role (if exists)
-- GRANT SELECT ON agent_lifecycle TO audit_role;

-- Migration complete
-- To rollback: ALTER TABLE agent_lifecycle DROP (termination_reason, termination_authorized_by, termination_hash, termination_timestamp);
