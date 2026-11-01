# Use a second laptop as a desktop companion

The companion connects outward to ARIA's existing authenticated `/node/ws`
bridge. ARIA chooses it by an explicit node name. An unnamed phone request
continues to select a phone and never silently switches to a desktop.

1. On the second Windows laptop, clone this repository, install Python 3.12,
   and install the locked dependencies:

   ```powershell
   py -3.12 -m pip install --require-hashes -r requirements-lock.txt
   ```

2. On ARIA's host, obtain `BRIDGE_TOKEN` from your local `aria_keys.json`.
   Save only its value in a private local text file on the second computer,
   such as `C:\Private\aria-node.key`. Never commit or paste the key into chat.
   Copy the host's public `workspace/aria_bridge_machine.pem` certificate to the
   second computer. Keep the host's private certificate key on the host.
   Use the host address covered by that per-machine certificate and its
   configured bridge port (normally 8777).

3. Create a directory to share, then launch:

   ```powershell
   py -3.12 -m aria.desktop_node --url wss://HOST:8777/node/ws --name second-laptop --root C:\Shared --key-file C:\Private\aria-node.key --ca-file C:\Private\aria_bridge_machine.pem
   ```

   For a bundled installation use `ARIA.exe --desktop-node` with the same
   arguments. If the certificate is already trusted by Windows, `--ca-file`
   is optional. Non-loopback connections require WSS and normal certificate
   validation; no insecure TLS switch is provided.

The default exposes system information, directory listing and text-file reads
inside the selected root. Add `--allow-writes` for text writes and
`--allow-controls` for screenshots, mouse, keyboard and opening web URLs.
There is no remote shell/code-execution action. Text transfers are capped at
2 MiB. Relative paths and resolved symlinks must stay in the shared root.

The client reconnects after transport failure and journals command receipts
in `.aria-companion/receipts.sqlite3` inside the root. Replaying the same
execution ID returns its saved result; a command interrupted before recording
its result stays uncertain. The client never blindly repeats that effect.

Open Work Center → Computers to see the announced capabilities. The model tool
`desktop_control` takes `node`, `action` and an `arguments` object, uses the
normal approval/safe-mode dispatcher and reports disconnects/timeouts as
uncertain. Example arguments:

```json
{"node":"second-laptop","action":"write_file","arguments":{"path":"notes.txt","content":"Hello"}}
```

Stopping the companion process disconnects that computer without stopping ARIA
or the phone node.
