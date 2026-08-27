# Remote access (iPad / travelling)

Reach the Paper Review app running on your Mac from an iPad, without exposing
it to the public internet.

> **Why not a public URL?** The app runs Claude Code with `bypassPermissions`,
> so anyone who reaches it can execute arbitrary code on your Mac. We use
> Tailscale, a private network only your own devices can join. Never use
> `tailscale funnel` (that *is* public) — use `tailscale serve`.

---

## 1. Vacation mode (already automated)

```bash
./remote/setup-remote.sh            # enable
./remote/setup-remote.sh --status   # check state + print the access token
./remote/setup-remote.sh --off      # back to the normal ./start.sh workflow
```

This does three things that matter when nobody is at the keyboard:

| Problem | Fix |
| --- | --- |
| Jupyter mints a **new token each launch**, breaking bookmarks | Pins a stable token (`remote/.remote-token`, gitignored) |
| The server has **crashed/stopped** on its own before | A launchd agent restarts it automatically, and after a reboot |
| The Mac **idle-sleeps** and becomes unreachable | The server runs wrapped in `caffeinate -is` |

No sudo needed — it's a per-user LaunchAgent. Logs land in `remote/logs/`.

## 2. Tailscale (needs you — sudo + an account)

On the **Mac**:

```bash
brew install --cask tailscale
```

Then open Tailscale, sign in, and connect. Once connected, publish the app to
your private network only:

```bash
tailscale serve --bg 8888
tailscale serve status     # shows your https://<machine>.<tailnet>.ts.net URL
```

On the **iPad**: install Tailscale from the App Store, sign in with the same
account, toggle it on, then open:

```
https://<machine>.<tailnet>.ts.net/lab?token=<your-token>
```

Get `<your-token>` from `./remote/setup-remote.sh --status`. Bookmark that full
URL — it stays valid across restarts now. Add it to your iPad Home Screen for
an app-like window.

## 3. Before you walk out the door

- **Plug the Mac in.** `caffeinate` stops idle-sleep, not a dead battery.
- **Leave the lid open.** Closing it sleeps the Mac regardless of `caffeinate`
  (unless you're in clamshell mode with an external display).
- **Confirm it's healthy:** `./remote/setup-remote.sh --status`
- **Re-login to Claude:** run `claude`, then `/login`. Sessions expire; if that
  happens while you're away you cannot fix it remotely, because the login is an
  interactive browser flow. GPT/Codex models use separate auth and keep working
  either way — a useful fallback.
- **Test from your phone/iPad on cellular** (not home Wi-Fi) before leaving, so
  you know the tailnet path actually works.

## Troubleshooting from the iPad

- **Page won't load** — check Tailscale is toggled on, on *both* devices.
- **"Invalid credentials"** — the token is missing from the URL; use the full
  bookmarked URL.
- **Claude models error, GPT works** — the Claude OAuth session lapsed. Switch
  the model dropdown to a GPT model until you're home.
- **Everything is down** — the Mac slept, lost power, or dropped off the
  network. Nothing fixes that remotely; a smart plug to power-cycle is the only
  real mitigation.
