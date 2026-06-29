#!/usr/bin/env bats
load test_helper

@test "gate_check.sh passes bash -n" {
  run bash -n "$GATE_DIR/gate_check.sh"
  [ "$status" -eq 0 ]
}
