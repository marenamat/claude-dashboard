# Questions / Dependency Requests

## Required packages for issue-1 (Base implementation)

The dashboard implementation requires the following packages not currently
installed on the machine:

### Rust toolchain

Needed for compiling the WebAssembly module.

```
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup target add wasm32-unknown-unknown
```

### wasm-pack

Needed for building and packaging the Rust WASM module.

```
curl https://rustwasm.github.io/wasm-pack/installer/init.sh -sSf | sh
```

Or via cargo once Rust is installed:

```
cargo install wasm-pack
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
