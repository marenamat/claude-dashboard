# Questions / Dependency Requests

## www/pkg rebuild needed (issue #4 BigInt fix)

The committed `www/pkg/` files are stale — built from an old version of `src/lib.rs`
before `render_dashboard` grew to three parameters.  The current Rust source compiles
correctly, but `wasm-bindgen` needs to be run to regenerate the JS glue and WASM binary:

```
cargo build --target wasm32-unknown-unknown --release
wasm-bindgen target/wasm32-unknown-unknown/release/claude_dashboard.wasm \
  --out-dir www/pkg --target web --no-typescript
```

CI does this automatically on every push, so GitHub Pages is fine.
Running `build.sh` locally also fixes it.
The committed files just need one manual rebuild to become current.

## Required package for issue-9 (Autoreload WebSocket server)

`ws-server.py` uses the `websockets` Python library (async WebSocket server).

```
pip install websockets
```

Or via system package if available:

```
apt install python3-websockets
```

After installing, enable and start the new sysvinit service:

```
chmod +x /home/maria/claude/claude-dashboard/etc/init.d/claude-dashboard-ws
cp etc/init.d/claude-dashboard-ws /etc/init.d/
update-rc.d claude-dashboard-ws defaults
service claude-dashboard-ws start
```

Also reload nginx after updating `nginx/clanker.conf`:

```
nginx -s reload -c /path/to/nginx/clanker.conf
```

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
