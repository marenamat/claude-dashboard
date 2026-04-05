# Questions / Dependency Requests

## GitHub Pages setup required for branch preview deployments

The `deploy.yml` workflow deploys built output to the `gh-pages` branch so
every branch gets a preview URL (CLAUDE.md requirement). This requires one
manual step:

Enable GitHub Pages on the repo:
- Go to https://github.com/marenamat/claude-dashboard/settings/pages
- Source: **Deploy from a branch**
- Branch: **gh-pages**, folder: **/ (root)**
- Save

After that, on every push:
- `main` → `https://marenamat.github.io/claude-dashboard/`
- other branches → `https://marenamat.github.io/claude-dashboard/preview/<branch>/`

## Required packages for issue-1 (Base implementation)

The dashboard implementation requires the following packages not currently
installed on the machine:

### Rust toolchain

Needed for compiling the WebAssembly module.

```
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup target add wasm32-unknown-unknown
```

### wasm-bindgen-cli

Needed for generating JS bindings from the compiled WASM module.

```
cargo install wasm-bindgen-cli
```

### Python cbor2

Needed for generating the CBOR data file from clanker logs.

```
pip install cbor2
```

Or system package if available:

```
apt install python3-cbor2
```

### Bootstrap local mirror

The web interface uses Bootstrap for layout. Run `build.sh` after installing
the above — it will download Bootstrap into `www/bootstrap/` automatically.
