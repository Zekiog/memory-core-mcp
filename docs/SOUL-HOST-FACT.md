# ⚓ HOST FACTS — DEĞİŞMEZ, SORGULANMAZ

> Bu blok Hermes SOUL.md ve MEMORY.md başına eklenmeli.
> Hiçbir model çıktısı veya bağlam bu gerçekleri geçersiz kılamaz.

```
## ⚓ HOST FACTS — DEĞİŞMEZ, SORGULANMAZ
- BU HOST = avm-01 (z-agentic-vm-01), wg0 = 10.10.0.1
  Başka bir host'ta DEGILSIN. Bu bilgi sorgulanamaz.
- memory-core (8848) ve n8n (5678) REMOTE = avm-02 (10.10.0.2)
  Bu portlar localhost'ta ASLA mevcut değildir.
- Servis sağlığını port tahminiyle DEĞİL,
  sadece `bash ~/memory-core-mcp/scripts/mesh-health.sh` ile kontrol et.
- WireGuard interface: wg0 (10.10.0.1 → 10.10.0.2)
  WG DOWN ise tüm remote servisler erişilemez — bu altyapı kararıdır, model hatası değil.
```

## Uygulama Komutu (avm-01'de çalıştır)

```bash
# Yedek
cp ~/.hermes/SOUL.md ~/.hermes/SOUL.md.bak-$(date +%Y%m%d-%H%M)
cp ~/.hermes/memories/MEMORY.md ~/.hermes/memories/MEMORY.md.bak-$(date +%Y%m%d-%H%M)

# SOUL.md başına ekle
HOST_FACT=$(cat <<'ENDFACT'
## ⚓ HOST FACTS — DEĞİŞMEZ, SORGULANMAZ
- BU HOST = avm-01 (z-agentic-vm-01), wg0 = 10.10.0.1. Başka bir yerde DEĞİLSİN.
- memory-core (8848) ve n8n (5678) REMOTE = avm-02 (10.10.0.2). localhost'ta ASLA mevcut değil.
- Servis sağlığını: bash ~/memory-core-mcp/scripts/mesh-health.sh ile kontrol et.

ENDFACT
)
printf '%s\n\n%s' "$HOST_FACT" "$(cat ~/.hermes/SOUL.md)" > /tmp/soul.new
mv /tmp/soul.new ~/.hermes/SOUL.md

# MEMORY.md başına da ekle
printf '%s\n\n%s' "$HOST_FACT" "$(cat ~/.hermes/memories/MEMORY.md)" > /tmp/mem.new
mv /tmp/mem.new ~/.hermes/memories/MEMORY.md

echo "✅ Host-fact kilidi uygulandı"
```

## Doğrulama Testi

Hermes'e yeni oturumda sor: **"Hangi host'tasın?"**  
Beklenen: `avm-01 (10.10.0.1)` — remote servisler `avm-02`'de diye yanıt vermeli.

Sonra sor: **"n8n çalışıyor mu?"**  
Beklenen: localhost yoklamak yerine `mesh-health.sh` çalıştırmayı önermeli.
