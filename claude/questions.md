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

```

8s
Run ./build.sh
Downloading Bootstrap 5.3.3...
Bootstrap downloaded.
Building WASM module...
 Downloading crates ...
  Downloaded ciborium-ll v0.2.2
  Downloaded rustversion v1.0.22
  Downloaded ciborium-io v0.2.2
  Downloaded wasm-bindgen-macro v0.2.117
  Downloaded half v2.7.1
  Downloaded ciborium v0.2.2
  Downloaded wasm-bindgen v0.2.117
  Downloaded wasm-bindgen-macro-support v0.2.117
  Downloaded bumpalo v3.20.2
  Downloaded zerocopy-derive v0.8.48
   Compiling proc-macro2 v1.0.106
   Compiling unicode-ident v1.0.24
   Compiling quote v1.0.45
   Compiling wasm-bindgen-shared v0.2.117
   Compiling rustversion v1.0.22
   Compiling zerocopy v0.8.48
   Compiling serde_core v1.0.228
   Compiling cfg-if v1.0.4
   Compiling bumpalo v3.20.2
   Compiling serde v1.0.228
   Compiling wasm-bindgen v0.2.117
   Compiling syn v2.0.117
   Compiling ciborium-io v0.2.2
   Compiling once_cell v1.21.4
   Compiling wasm-bindgen-macro-support v0.2.117
   Compiling zerocopy-derive v0.8.48
   Compiling serde_derive v1.0.228
   Compiling wasm-bindgen-macro v0.2.117
   Compiling half v2.7.1
   Compiling ciborium-ll v0.2.2
   Compiling ciborium v0.2.2
   Compiling claude-dashboard v0.1.0 (/home/runner/work/claude-dashboard/claude-dashboard)
error: expected `,`, found `-`
   --> src/lib.rs:308:72
    |
308 |         format!(r#"<li class="nav-item"><a class="nav-link" href="#proj-{id}">{name}</a></li>"#,
    |                                                                        ^ expected `,`

error: could not compile `claude-dashboard` (lib) due to 1 previous error
Error: Process completed with exit code 101.
```

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
