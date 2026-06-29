#!/usr/bin/env bash
# render.sh — output formatters for gate_check.sh (sourced, not executed).
#
# Consumes these globals (set by gate_check.sh, or by a test before calling):
#   VERDICT GREEN|RED            EXIT_CODE 0|1
#   ADIM_SS ADIM_UFW ADIM_DOCKER ADIM_EXT   1=pass 0=fail
#   EXT_PORT_RESULTS[]  "port:open"  (open: 1=reachable=BAD, 0=closed=good)
#   STRICT_P2_1 0|1     PROBE_TOOL nmap|nc     CONFIG_ERROR 0|1   CONFIG_REASON str
#   EXEC_DURATION int   NOW_ISO iso8601   NOW_EPOCH unix   TARGET_PUB_IP str
#   HOSTLABEL str (metric host=)   RUNNER str   FORMAT text|json|prom
#   FAILS[] WARNS[]  token arrays
#   FORMAT is read only by render_config_error (to pick its own encoding).

json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "$s"
}

# join args into a JSON array; empty -> []
json_array() {
  local first=1 out="[" a
  for a in "$@"; do
    if [ "$first" = 1 ]; then first=0; else out="$out,"; fi
    out="$out\"$(json_escape "$a")\""
  done
  printf '%s]' "$out"
}

render_text() {
  local fails_s warns_s
  fails_s="${FAILS[*]:-none}"
  warns_s="${WARNS[*]:-none}"
  printf '%s gate=%s target=%s probe=%s strict_p2_1=%s fails=%s warns=%s\n' \
    "$NOW_ISO" "$VERDICT" "$TARGET_PUB_IP" "$PROBE_TOOL" "$STRICT_P2_1" \
    "$fails_s" "$warns_s"
}

render_json() {
  local fails_json warns_json ext_json="{" first=1 pair port open state
  fails_json="$(json_array ${FAILS[@]+"${FAILS[@]}"})"
  warns_json="$(json_array ${WARNS[@]+"${WARNS[@]}"})"
  for pair in ${EXT_PORT_RESULTS[@]+"${EXT_PORT_RESULTS[@]}"}; do
    port="${pair%%:*}"; open="${pair##*:}"
    if [ "$open" = 1 ]; then state="open"; else state="closed"; fi
    if [ "$first" = 1 ]; then first=0; else ext_json="$ext_json,"; fi
    ext_json="$ext_json\"$port\":\"$state\""
  done
  ext_json="$ext_json}"
  printf '{"ts":"%s","host":"%s","verdict":"%s","exit":%s,"fails":%s,"warns":%s,"ext_ports":%s,"probe":"%s","strict_p2_1":%s,"runner":"%s","duration_s":%s}\n' \
    "$NOW_ISO" "$HOSTLABEL" "$VERDICT" "$EXIT_CODE" "$fails_json" "$warns_json" \
    "$ext_json" "$PROBE_TOOL" "$STRICT_P2_1" "$RUNNER" "$EXEC_DURATION"
}

render_prom() {
  local pair port open verdict_n
  verdict_n=0; [ "$VERDICT" = GREEN ] && verdict_n=1
  printf '# HELP mesh_gate_verdict Overall gate verdict (1=GREEN no exposure, 0=RED exposure found)\n'
  printf '# TYPE mesh_gate_verdict gauge\n'
  printf 'mesh_gate_verdict{host="%s"} %s\n' "$HOSTLABEL" "$verdict_n"
  printf '# HELP mesh_gate_adim Per-step result (1=pass, 0=fail)\n'
  printf '# TYPE mesh_gate_adim gauge\n'
  printf 'mesh_gate_adim{host="%s",adim="ss"} %s\n' "$HOSTLABEL" "$ADIM_SS"
  printf 'mesh_gate_adim{host="%s",adim="ufw"} %s\n' "$HOSTLABEL" "$ADIM_UFW"
  printf 'mesh_gate_adim{host="%s",adim="docker_bind"} %s\n' "$HOSTLABEL" "$ADIM_DOCKER"
  printf 'mesh_gate_adim{host="%s",adim="ext_probe"} %s\n' "$HOSTLABEL" "$ADIM_EXT"
  printf '# HELP mesh_gate_ext_port_open External reachability (1=OPEN=bad, 0=closed/filtered=good)\n'
  printf '# TYPE mesh_gate_ext_port_open gauge\n'
  for pair in ${EXT_PORT_RESULTS[@]+"${EXT_PORT_RESULTS[@]}"}; do
    port="${pair%%:*}"; open="${pair##*:}"
    printf 'mesh_gate_ext_port_open{host="%s",port="%s"} %s\n' "$HOSTLABEL" "$port" "$open"
  done
  printf '# HELP mesh_gate_strict_p2_1 STRICT_P2_1 in effect (1=empty DOCKER-USER counts as RED)\n'
  printf '# TYPE mesh_gate_strict_p2_1 gauge\n'
  printf 'mesh_gate_strict_p2_1{host="%s"} %s\n' "$HOSTLABEL" "$STRICT_P2_1"
  printf '# HELP mesh_gate_config_error Gate hit CONFIG error (exit 2)\n'
  printf '# TYPE mesh_gate_config_error gauge\n'
  printf 'mesh_gate_config_error{host="%s"} 0\n' "$HOSTLABEL"
  printf '# HELP mesh_gate_exec_duration_seconds Wall time of the gate run\n'
  printf '# TYPE mesh_gate_exec_duration_seconds gauge\n'
  printf 'mesh_gate_exec_duration_seconds{host="%s"} %s\n' "$HOSTLABEL" "$EXEC_DURATION"
  printf '# HELP mesh_gate_last_run_timestamp_seconds Unix time the gate completed\n'
  printf '# TYPE mesh_gate_last_run_timestamp_seconds gauge\n'
  printf 'mesh_gate_last_run_timestamp_seconds{host="%s"} %s\n' "$HOSTLABEL" "$NOW_EPOCH"
}

# exit-2 path: emit ONLY config_error=1 + duration + timestamp. verdict/adim/ext omitted (unknown).
render_config_error() {
  case "$FORMAT" in
    prom)
      printf '# HELP mesh_gate_config_error Gate hit CONFIG error (exit 2)\n'
      printf '# TYPE mesh_gate_config_error gauge\n'
      printf 'mesh_gate_config_error{host="%s"} 1\n' "$HOSTLABEL"
      printf '# HELP mesh_gate_exec_duration_seconds Wall time of the gate run\n'
      printf '# TYPE mesh_gate_exec_duration_seconds gauge\n'
      printf 'mesh_gate_exec_duration_seconds{host="%s"} %s\n' "$HOSTLABEL" "$EXEC_DURATION"
      printf '# HELP mesh_gate_last_run_timestamp_seconds Unix time the gate completed\n'
      printf '# TYPE mesh_gate_last_run_timestamp_seconds gauge\n'
      printf 'mesh_gate_last_run_timestamp_seconds{host="%s"} %s\n' "$HOSTLABEL" "$NOW_EPOCH"
      ;;
    json)
      printf '{"ts":"%s","host":"%s","verdict":"CONFIG","exit":2,"config_error":1,"reason":"%s","runner":"%s","duration_s":%s}\n' \
        "$NOW_ISO" "$HOSTLABEL" "$(json_escape "$CONFIG_REASON")" "$RUNNER" "$EXEC_DURATION"
      ;;
    *)
      printf '%s gate=CONFIG target=%s reason=%s\n' "$NOW_ISO" "$TARGET_PUB_IP" "$CONFIG_REASON" >&2
      ;;
  esac
}
