# Shared bats helpers: build a stub bin dir on PATH so the gate never touches a real host.
GATE_DIR="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
FIXTURES="$BATS_TEST_DIRNAME/fixtures"

setup_stub_bin() {
  STUB_BIN="$(mktemp -d)"
  # --- stub ssh: route by the remote command string (last arg) ---
  cat >"$STUB_BIN/ssh" <<EOF
#!/usr/bin/env bash
cmd="\${@: -1}"
if [ "\${STUB_SSH_UNREACHABLE:-0}" = "1" ]; then exit 255; fi
case "\$cmd" in
  true) exit 0 ;;
  *"ss -tulnp"*)      cat "$FIXTURES/\${SS_FIXTURE:-ss_green.txt}" ;;
  *"ufw status"*)     cat "$FIXTURES/\${UFW_FIXTURE:-ufw_ok.txt}" ;;
  *"DOCKER-USER"*)    cat "$FIXTURES/\${DOCKERUSER_FIXTURE:-dockeruser_empty.txt}" ;;
  *"docker inspect"*) cat "$FIXTURES/\${DOCKERBIND_FIXTURE:-docker_bind_ok.txt}" ;;
  *) exit 0 ;;
esac
EOF
  # --- stub nc: control port 22 always open; gated ports open only if listed in STUB_OPEN_PORTS ---
  cat >"$STUB_BIN/nc" <<'EOF'
#!/usr/bin/env bash
port=""
for a in "$@"; do port="$a"; done   # last arg is the port
[ "$port" = "22" ] && exit 0
for p in ${STUB_OPEN_PORTS:-}; do [ "$p" = "$port" ] && exit 0; done
exit 1
EOF
  chmod +x "$STUB_BIN/ssh" "$STUB_BIN/nc"
  PATH="$STUB_BIN:$PATH"
}

teardown_stub_bin() { rm -rf "$STUB_BIN"; }
