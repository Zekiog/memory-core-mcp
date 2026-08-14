"""
Termination Attestation Utility

Implements Issue #17: Add termination ledger with cryptographic attestation.
Based on Bradley & Saad "Wrongful Destruction" argument (Oxford GPI, 2026).

This module provides cryptographic signing for agent termination events,
creating an auditable "death certificate" for each agent lifecycle end.
"""

import hashlib
import json
import hmac
import os
from datetime import datetime
from typing import Optional, Dict, Any


class TerminationAttestation:
    """
    Cryptographic attestation for agent termination events.
    
    Creates SHA-256 hashes of termination records for on-chain anchoring
    via Zion-ID. Supports HMAC signing with owner's private key.
    """
    
    def __init__(self, secret_key: Optional[str] = None):
        self.secret_key = secret_key or os.environ.get("TERMINATION_SECRET_KEY")
        if not self.secret_key:
            raise ValueError(
                "TERMINATION_SECRET_KEY environment variable not set. "
                "This is required for cryptographic attestation."
            )
    
    def create_termination_record(
        self,
        agent_id: str,
        reason: str,
        authorized_by: str,
        creation_intent: str = "tool",
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        record = {
            "agent_id": agent_id,
            "reason": reason,
            "authorized_by": authorized_by,
            "creation_intent": creation_intent,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "metadata": metadata or {}
        }
        return record
    
    def compute_hash(self, record: Dict[str, Any]) -> str:
        canonical_json = json.dumps(record, sort_keys=True, separators=(',', ':'))
        hash_bytes = hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()
        return hash_bytes
    
    def sign_record(self, record: Dict[str, Any]) -> str:
        canonical_json = json.dumps(record, sort_keys=True, separators=(',', ':'))
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            canonical_json.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def create_attestation(
        self,
        agent_id: str,
        reason: str,
        authorized_by: str,
        creation_intent: str = "tool",
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        record = self.create_termination_record(
            agent_id=agent_id,
            reason=reason,
            authorized_by=authorized_by,
            creation_intent=creation_intent,
            metadata=metadata
        )
        record_hash = self.compute_hash(record)
        signature = self.sign_record(record)
        attestation = {
            "record": record,
            "hash": record_hash,
            "signature": signature,
            "verification": {
                "algorithm": "HMAC-SHA256",
                "hash_algorithm": "SHA-256",
                "canonical_form": "JSON (sorted keys, no whitespace)"
            }
        }
        return attestation
    
    def verify_attestation(self, attestation: Dict[str, Any]) -> bool:
        record = attestation["record"]
        stored_hash = attestation["hash"]
        stored_signature = attestation["signature"]
        computed_hash = self.compute_hash(record)
        if computed_hash != stored_hash:
            return False
        computed_signature = self.sign_record(record)
        if computed_signature != stored_signature:
            return False
        return True


def create_termination_attestation(
    agent_id: str,
    reason: str,
    authorized_by: str,
    creation_intent: str = "tool",
    metadata: Optional[Dict[str, Any]] = None,
    secret_key: Optional[str] = None
) -> Dict[str, Any]:
    attester = TerminationAttestation(secret_key)
    return attester.create_attestation(
        agent_id=agent_id,
        reason=reason,
        authorized_by=authorized_by,
        creation_intent=creation_intent,
        metadata=metadata
    )


if __name__ == "__main__":
    import os
    os.environ["TERMINATION_SECRET_KEY"] = "example-secret-key-for-testing"
    
    attestation = create_termination_attestation(
        agent_id="zion-agent-001",
        reason="user_request",
        authorized_by="user_12345",
        creation_intent="tool",
        metadata={"lifecycle_duration_hours": 48}
    )
    
    print("Termination Attestation Package:")
    print(json.dumps(attestation, indent=2))
    print(f"\nHash: {attestation['hash']}")
    print(f"Signature: {attestation['signature']}")
    
    is_valid = TerminationAttestation().verify_attestation(attestation)
    print(f"\nVerification: {'VALID' if is_valid else 'INVALID'}")
