## 2024-04-27 - [CRITICAL] Fix command injection vulnerability in backup passphrase

**Vulnerability:** The passphrase used to encrypt the tar.zst archive with GPG was passed via the command line argument `--passphrase`. This allowed any user on the same system to read the sensitive passphrase by inspecting running processes (e.g., using `ps aux`).
**Learning:** `run_checked` wrapper abstracted `subprocess.run` which hid the unsafe argument list. Command-line tools that accept passwords must consume them securely via standard input or dedicated file descriptors.
**Prevention:** Avoid passing secrets through command-line arguments. Use `--passphrase-fd 0` with `subprocess.run(..., input=secret)` to supply sensitive data securely to the standard input.
