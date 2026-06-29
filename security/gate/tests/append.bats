#!/usr/bin/env bats
load test_helper

@test "append_jsonl appends each stdin line, preserving order" {
  tmp="$(mktemp -d)"; ledger="$tmp/security-audit.jsonl"
  printf '{"n":1}\n' | bash "$GATE_DIR/audit/append_jsonl.sh" "$ledger"
  printf '{"n":2}\n' | bash "$GATE_DIR/audit/append_jsonl.sh" "$ledger"
  [ "$(wc -l < "$ledger" | tr -d ' ')" = "2" ]
  [ "$(sed -n 1p "$ledger")" = '{"n":1}' ]
  [ "$(sed -n 2p "$ledger")" = '{"n":2}' ]
  rm -rf "$tmp"
}

@test "append_jsonl creates the ledger and parent dir if missing" {
  tmp="$(mktemp -d)"; ledger="$tmp/sub/dir/security-audit.jsonl"
  printf '{"n":1}\n' | bash "$GATE_DIR/audit/append_jsonl.sh" "$ledger"
  [ -f "$ledger" ]
  rm -rf "$tmp"
}
