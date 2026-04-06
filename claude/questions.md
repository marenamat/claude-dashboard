# Questions / Dependency Requests

## CI Build failures — need log access

The Build and Deploy workflows are still failing after all CI fixes (wasm-bindgen-cli
approach, exact version pinning). The CI step "Build" (`./build.sh`) fails but we
cannot see the actual error because the GitHub API returns 403 on log downloads.

CI was split into discrete sub-steps (Bootstrap download, Cargo build WASM,
wasm-bindgen, Generate dashboard data) so the next CI run will show exactly which
sub-step fails. Please check the failing step in the next CI run and report back.

Alternatively, please grant the workflow logs read access by checking if the GitHub
token has the `actions: read` permission, or share the relevant step's output.

## GitHub Pages setup — RESOLVED

Updated `deploy.yml` to use GitHub Actions Pages deployment (`actions/deploy-pages`).
No gh-pages branch required. Runs on `main` only.

**M:** Can't set to gh-pages, that branch existn't. Set to "deploy by actions".
**Resolution:** deploy.yml updated to use GitHub Actions workflow deployment.

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
