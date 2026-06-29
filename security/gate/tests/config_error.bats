#!/usr/bin/env bats
load test_helper

setup() { setup_stub_bin; }
teardown() { teardown_stub_bin; }

@test "unreachable target yields exit 2 and config_error=1, no verdict line (prom)" {
  export STUB_SSH_UNREACHABLE=1 NOW_ISO="2026-06-29T12:00:00Z" NOW_EPOCH="1751193600"
  export EXEC_DURATION_OVERRIDE="0" HOSTLABEL="avm-02" PROBE_TOOL="nc" FORMAT="prom"
  run bash "$GATE_DIR/gate_check.sh"
  [ "$status" -eq 2 ]
  echo "$output" | grep -q 'mesh_gate_config_error{host="avm-02"} 1'
  ! echo "$output" | grep -q 'mesh_gate_verdict'
}

@test "unreachable target yields CONFIG json with reason" {
  export STUB_SSH_UNREACHABLE=1 NOW_ISO="2026-06-29T12:00:00Z" NOW_EPOCH="1751193600"
  export EXEC_DURATION_OVERRIDE="0" HOSTLABEL="avm-02" RUNNER="avm-01" PROBE_TOOL="nc" FORMAT="json"
  run bash "$GATE_DIR/gate_check.sh"
  [ "$status" -eq 2 ]
  echo "$output" | grep -q '"verdict":"CONFIG"'
  echo "$output" | grep -q '"reason":"ssh:unreachable:z-agentic-vm-02"'
}
