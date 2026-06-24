# Deploying memory-core to avm-02

## systemd drop-ins

Drop-ins live in `deploy/systemd/memory-core.service.d/*.conf` and mirror
`/etc/systemd/system/memory-core.service.d/*.conf` on avm-02. To deploy a
drop-in:

```bash
scp deploy/systemd/memory-core.service.d/10-fastembed-cache.conf \
    z-agentic-vm-02:/tmp/10-fastembed-cache.conf
ssh z-agentic-vm-02 "sudo install -d -m 755 /etc/systemd/system/memory-core.service.d && \
  sudo install -m 644 /tmp/10-fastembed-cache.conf /etc/systemd/system/memory-core.service.d/10-fastembed-cache.conf && \
  rm /tmp/10-fastembed-cache.conf && \
  sudo systemctl daemon-reload"
```

Restart only after the cache is pre-staged (see "Pre-staging the fastembed
model" below) — restarting before staging will leave the service offline
the same way it is today.

## Pre-staging the fastembed model

`HF_HUB_OFFLINE=1` means the service will never attempt a network download.
The model must be placed into the cache dirs *before* the env vars take
effect, using a context that still has internet + HOME access (the engineer's
own SSH session, not the sandboxed service):

```bash
ssh z-agentic-vm-02 "sudo install -d -o ubuntu -g ubuntu /opt/memory-core/.fastembed /opt/memory-core/.hf && \
  sudo -u ubuntu env FASTEMBED_CACHE_DIR=/opt/memory-core/.fastembed HF_HOME=/opt/memory-core/.hf \
    /opt/memory-core/.venv/bin/python -c \"from fastembed import TextEmbedding; TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2')\""
```

Expected: no exception; the command exits 0. This downloads the model once
into `/opt/memory-core/.fastembed` and `/opt/memory-core/.hf`, owned by the
service user (`ubuntu` — confirm with `systemctl cat memory-core.service |
grep User=` before running; adjust `-o`/`-g`/`-u` if different).
