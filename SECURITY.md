# SECURITY.md — memory-core-mcp

> Org genelinde geçerli güvenlik politikası için → [Agent-Z/SECURITY.md](https://github.com/Zekiog/Agent-Z/blob/main/SECURITY.md)

## Bu Repo İçin Kritik Notlar

- **Oracle ADB `ZMEM_PASSWORD`**: Vault'ta `zsecret://vault/zion/memory-core/zmem-password` — 90 günde bir rotate
- **`TARGET_PUB_IP`**: `gate_check.sh` ortam değişkeni zorunlu, hardcode yasak (bkz. commit `a799c1c`)
- **Branch protection**: main branch — PR + 1 review zorunlu olmalı

## Güvenlik Açığı Bildirimi

GitHub Private Security Advisory kullanın — public issue AÇMAYIN.

## Aktif Security Gates

| Gate | Dosya | Durum |
|---|---|---|
| IP Exposure | `security/gate/gate_check.sh` | ✅ Fixed (env-only) |
| Port binding | `security/gate/gate_check.sh step3` | ✅ Active |
| UFW default-deny | `security/gate/gate_check.sh step2` | ✅ Active |
| External probe | `security/gate/gate_check.sh step4` | ✅ Active |
