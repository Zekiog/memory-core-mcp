#!/usr/bin/env bats
load test_helper

setup() { setup_stub_bin; }
teardown() { teardown_stub_bin; }

run_gate() {
  NOW_ISO="2026-06-29T12:00:00Z" NOW_EPOCH="1751193600" EXEC_DURATION_OVERRIDE="2" \
  RUNNER="avm-01" HOSTLABEL="avm-02" PROBE_TOOL="nc" \
  TARGET_HOST="z-agentic-vm-02" TARGET_PUB_IP="79.76.57.223" \
  FORMAT="$1" bash "$GATE_DIR/gate_check.sh"
}

@test "all-clean run is GREEN, exit 0, prom verdict=1" {
  run run_gate prom
  [ "$status" -eq 0 ]
  echo "$output" | grep -q 'mesh_gate_verdict{host="avm-02"} 1'
}

@test "public bind on ss makes verdict RED, exit 1, adim ss=0" {
  export SS_FIXTURE="ss_red.txt"   # export so the stubbed ssh subprocess sees it
  run run_gate prom
  [ "$status" -eq 1 ]
  echo "$output" | grep -q 'mesh_gate_verdict{host="avm-02"} 0'
  echo "$output" | grep -q 'mesh_gate_adim{host="avm-02",adim="ss"} 0'
}

@test "externally open gated port makes verdict RED and ext_port_open=1" {
  export STUB_OPEN_PORTS="5678"   # export so the stubbed nc subprocess sees it
  run run_gate prom
  [ "$status" -eq 1 ]
  echo "$output" | grep -q 'mesh_gate_ext_port_open{host="avm-02",port="5678"} 1'
  echo "$output" | grep -q 'mesh_gate_adim{host="avm-02",adim="ext_probe"} 0'
}

@test "json format emits a single parseable verdict line" {
  run run_gate json
  [ "$status" -eq 0 ]
  echo "$output" | grep -q '"verdict":"GREEN"'
  echo "$output" | grep -q '"ext_ports":{"5678":"closed","6379":"closed","5432":"closed"}'
}

@test "text format default is backward-compatible single line" {
  run run_gate text
  [ "$status" -eq 0 ]
  echo "$output" | grep -q 'gate=GREEN target=79.76.57.223 probe=nc'
}
