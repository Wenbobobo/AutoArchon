# Migration Recovery

Use this runbook to move AutoArchon onto a new host with minimal ambiguity.

The intended recovery order is:

1. GitHub for the main `AutoArchon` repo
2. HF dataset backups for local config, curated campaign state, and session archives
3. `repo-lock.json` to recreate upstream benchmark clones at the recorded commits

This keeps the code path clean while still preserving the irreplaceable local state that does not belong in Git.

## Recovery Inputs

Code repo:

- `https://github.com/Wenbobobo/AutoArchon.git`

HF datasets:

- public: `Garydesu/AutoArchon_Public`
- private: `Garydesu/AutoArchon_Private`

The public dataset contains:

- tracked `AutoArchon` source export
- `git bundle` fallback
- small public problem packs
- restore metadata

The private dataset contains:

- `examples/helper.env`
- curated campaign metadata and workspace snapshots
- Codex session backup archive

Large rebuildable caches such as `.lake`, `node_modules`, and `.venv` are intentionally excluded from the migration bundles.

## Prerequisites

On the new host, install:

```bash
sudo apt-get update
sudo apt-get install -y git gpg zstd curl
curl -LsSf https://astral.sh/uv/install.sh | sh
curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh -s -- -y
curl -LsSf https://hf.co/cli/install.sh | bash
```

Then restart the shell and confirm:

```bash
uv --version
elan --version
hf version
gpg --version
```

If you need the private dataset, authenticate first:

```bash
hf auth login
hf auth whoami
```

## 1. Create the Workspace Skeleton

```bash
export RESTORE_ROOT="$HOME/Wenbo"
export MATH_ROOT="$RESTORE_ROOT/math"
export HF_PUBLIC_DIR="$RESTORE_ROOT/restore/hf-public"
export HF_PRIVATE_DIR="$RESTORE_ROOT/restore/hf-private"
mkdir -p "$MATH_ROOT" "$HF_PUBLIC_DIR" "$HF_PRIVATE_DIR"
```

## 2. Clone the Main Repo from GitHub

Use GitHub as the primary code source:

```bash
git clone https://github.com/Wenbobobo/AutoArchon.git "$MATH_ROOT/Archon"
cd "$MATH_ROOT/Archon"
git remote add upstream https://github.com/frenzymath/Archon.git || true
git fetch --all --tags
git checkout main
git pull --ff-only origin main
```

If GitHub is unavailable, the public HF bundle also contains `git-bundles/AutoArchon.bundle`. See the fallback section below.

## 3. Download the HF Datasets

```bash
hf download Garydesu/AutoArchon_Public \
  --repo-type dataset \
  --local-dir "$HF_PUBLIC_DIR"

hf download Garydesu/AutoArchon_Private \
  --repo-type dataset \
  --local-dir "$HF_PRIVATE_DIR"
```

You should end up with:

- `$HF_PUBLIC_DIR/archives/`
- `$HF_PUBLIC_DIR/manifests/`
- `$HF_PRIVATE_DIR/archives/`
- `$HF_PRIVATE_DIR/manifests/`

## 4. Verify Checksums

```bash
(cd "$HF_PUBLIC_DIR" && sha256sum -c SHA256SUMS)
(cd "$HF_PRIVATE_DIR" && sha256sum -c SHA256SUMS)
```

## 5. Reassemble Split Parts If Present

Some archives may already be single `.gpg` files. Others may be split into `.part0000`, `.part0001`, ... pieces.

```bash
for root in "$HF_PUBLIC_DIR" "$HF_PRIVATE_DIR"; do
  find "$root/archives" -maxdepth 1 -type f -name '*.part0000' | while read -r part0; do
    base="${part0%.part0000}"
    cat "${base}".part* > "$base"
  done
done
```

## 6. Decrypt and Extract the Bundles

Set the agreed migration passphrase for this backup batch in your shell, then decrypt and extract.

```bash
export BACKUP_PASSPHRASE='<agreed-migration-passphrase>'
export EXTRACT_PUBLIC="$RESTORE_ROOT/restore/extracted-public"
export EXTRACT_PRIVATE="$RESTORE_ROOT/restore/extracted-private"
mkdir -p "$EXTRACT_PUBLIC" "$EXTRACT_PRIVATE"
```

Decrypt and extract the public archives:

```bash
for enc in "$HF_PUBLIC_DIR"/archives/*.gpg; do
  out="${enc%.gpg}"
  gpg --batch --yes --pinentry-mode loopback \
    --passphrase "$BACKUP_PASSPHRASE" \
    -o "$out" -d "$enc"
  tar --use-compress-program=unzstd -xf "$out" -C "$EXTRACT_PUBLIC"
done
```

Decrypt and extract the private archives:

```bash
for enc in "$HF_PRIVATE_DIR"/archives/*.tar.zst.gpg; do
  out="${enc%.gpg}"
  gpg --batch --yes --pinentry-mode loopback \
    --passphrase "$BACKUP_PASSPHRASE" \
    -o "$out" -d "$enc"
  tar --use-compress-program=unzstd -xf "$out" -C "$EXTRACT_PRIVATE"
done
```

The Codex session archive in the private dataset is a raw `*.tar.gpg` file rather than a `tar.zst.gpg` bundle. Decrypt it separately if present:

```bash
if ls "$HF_PRIVATE_DIR"/archives/*codex*sessions*.tar.gpg >/dev/null 2>&1; then
  for enc in "$HF_PRIVATE_DIR"/archives/*codex*sessions*.tar.gpg; do
    out="${enc%.gpg}"
    gpg --batch --yes --pinentry-mode loopback \
      --passphrase "$BACKUP_PASSPHRASE" \
      -o "$out" -d "$enc"
  done
fi
```

## 7. Restore Local Config and Curated Campaign State

Restore the ignored helper env:

```bash
install -D -m 600 \
  "$EXTRACT_PRIVATE/autoarchon-private-config/Archon/examples/helper.env" \
  "$MATH_ROOT/Archon/examples/helper.env"
```

Restore curated campaign metadata and run snapshots:

```bash
mkdir -p "$MATH_ROOT/runs/campaigns"
rsync -a \
  "$EXTRACT_PRIVATE/autoarchon-private-campaigns-metadata/campaigns/" \
  "$MATH_ROOT/runs/campaigns/"

rsync -a \
  "$EXTRACT_PRIVATE/autoarchon-private-campaigns-workspaces/campaigns/" \
  "$MATH_ROOT/runs/campaigns/"
```

Those snapshots intentionally exclude `.lake`, so the new host must rehydrate builds instead of trusting stale caches.

## 8. Restore Codex Session Archives

If the private dataset contains the session tar, extract it back under `$RESTORE_ROOT`:

```bash
if ls "$HF_PRIVATE_DIR"/archives/*codex*sessions*.tar >/dev/null 2>&1; then
  for tarball in "$HF_PRIVATE_DIR"/archives/*codex*sessions*.tar; do
    tar -xf "$tarball" -C "$RESTORE_ROOT"
  done
fi
```

Adjust the extraction target if you want those sessions somewhere else.

## 9. Recreate Upstream Clones from `repo-lock.json`

The public dataset manifest is the source of truth for upstream clone remotes and commits.

Run this from any shell with `python3` and `git`:

```bash
python3 - <<'PY'
import json
import subprocess
from pathlib import Path

restore_root = Path.home() / "Wenbo"
math_root = restore_root / "math"
repo_lock = restore_root / "restore" / "hf-public" / "manifests" / "repo-lock.json"
payload = json.loads(repo_lock.read_text(encoding="utf-8"))

for entry in payload:
    name = entry["name"]
    if name == "Archon":
        continue
    rel_path = Path(entry["path"]).relative_to("/home/daism/Wenbo/math")
    dest = math_root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    fetch_url = None
    for remote in entry.get("remotes", []):
        if remote.get("kind") == "fetch":
            fetch_url = remote["url"]
            break
    if fetch_url is None:
        raise SystemExit(f"no fetch remote recorded for {name}")
    if not dest.exists():
        subprocess.run(["git", "clone", fetch_url, str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "fetch", "--all", "--tags"], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", entry["head"]], check=True)
PY
```

This recreates:

- `Archon-upstream`
- `benchmarks/FATE-upstream`
- `benchmarks/FATE-M-upstream`
- `benchmarks/FATE-H-upstream`
- `benchmarks/FATE-X-upstream`

at the recorded commits.

## 10. Bootstrap the Restored Repo

```bash
cd "$MATH_ROOT/Archon"
./setup.sh
uv sync --all-groups
bash scripts/install_repo_skill.sh
```

Then validate the local toolchain:

```bash
uv run --directory "$MATH_ROOT/Archon" autoarchon-helper-healthcheck \
  --env-file "$MATH_ROOT/Archon/examples/helper.env"
```

If you want to sanity check the control plane before a long run:

```bash
uv run --directory "$MATH_ROOT/Archon" autoarchon-campaign-overview \
  --campaign-root "$MATH_ROOT/runs/campaigns/<campaign-id>" \
  --markdown
```

And for a launch preflight:

```bash
uv run --directory "$MATH_ROOT/Archon" autoarchon-validate-launch-contract \
  --campaign-root "$MATH_ROOT/runs/campaigns/<campaign-id>" \
  --probe-helper
```

## 11. Restart Interactive Operation

```bash
cd "$MATH_ROOT/Archon"
source examples/helper.env
codex -C "$MATH_ROOT/Archon" --model gpt-5.4 --config "model_reasoning_effort=xhigh"
```

Then use the normal operator flow from [campaign-operator.md](campaign-operator.md).

## GitHub-Unavailable Fallback

If the GitHub repo is unavailable, recover the main repo from the public backup:

```bash
export PUBLIC_EXPORT="$EXTRACT_PUBLIC/autoarchon-public-repo"
git clone "$PUBLIC_EXPORT/git-bundles/AutoArchon.bundle" "$MATH_ROOT/Archon"
cd "$MATH_ROOT/Archon"
git remote add origin https://github.com/Wenbobobo/AutoArchon.git || true
git remote add upstream https://github.com/frenzymath/Archon.git || true
```

The extracted tracked source export at:

- `$PUBLIC_EXPORT/AutoArchon-export/`

is the human-readable fallback mirror if you need to inspect files before Git remotes come back.

## Acceptance Checklist

The migration is complete when all of the following succeed:

- `git -C "$MATH_ROOT/Archon" status`
- `uv --version`
- `lean --version`
- `lake --version`
- `codex --version`
- `uv run --directory "$MATH_ROOT/Archon" autoarchon-helper-healthcheck --env-file "$MATH_ROOT/Archon/examples/helper.env"`
- `uv run --directory "$MATH_ROOT/Archon" autoarchon-campaign-overview --campaign-root "$MATH_ROOT/runs/campaigns/<campaign-id>" --markdown`

At that point the new host has the code, local secrets, curated campaign state, and operator session archive needed to continue work without returning to the old machine.
