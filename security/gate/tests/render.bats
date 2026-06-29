#!/usr/bin/env bats
load test_helper

setup() {
  source "$GATE_DIR/lib/render.sh"
  # deterministic shared state for a GREEN run with a latent P2.1 warning
  NOW_ISO="2026-06-29T12:00:00Z"; NOW_EPOCH="1751193600"
  HOSTLABEL="avm-02"; RUNNER="avm-01"; TARGET_PUB_IP="79.76.57.223"
  PROBE_TOOL="nc"; STRICT_P2_1="0"; EXEC_DURATION="2"
  VERDICT="GREEN"; EXIT_CODE="0"; FORMAT="text"
  ADIM_SS="1"; ADIM_UFW="1"; ADIM_DOCKER="1"; ADIM_EXT="1"
  FAILS=(); WARNS=("docker-user:empty(P2.1-latent)")
  EXT_PORT_RESULTS=("5678:0" "6379:0" "5432:0")
  CONFIG_ERROR="0"; CONFIG_REASON=""
}

@test "render_text matches golden" {
  run render_text
  [ "$status" -eq 0 ]
  [ "$output" = "$(cat "$FIXTURES/golden_text.txt")" ]
}

@test "render_json matches golden" {
  run render_json
  [ "$status" -eq 0 ]
  [ "$output" = "$(cat "$FIXTURES/golden_json.txt")" ]
}

@test "render_prom matches golden" {
  run render_prom
  [ "$status" -eq 0 ]
  [ "$output" = "$(cat "$FIXTURES/golden_prom.txt")" ]
}

@test "render_config_error prom matches golden" {
  FORMAT="prom"; CONFIG_ERROR="1"; EXEC_DURATION="0"
  run render_config_error
  [ "$status" -eq 0 ]
  [ "$output" = "$(cat "$FIXTURES/golden_config_prom.txt")" ]
}

@test "json_escape escapes quote and backslash" {
  run json_escape 'a"b\c'
  [ "$output" = 'a\"b\\c' ]
}
