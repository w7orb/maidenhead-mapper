# Security policy

Maidenhead Mapper is a hobby project maintained by one person in spare time. Security reports are welcome and will be taken seriously.

## Supported versions

Only the latest release gets fixes. Please check that a problem still happens on the newest version before reporting it.

| Version | Supported |
|---|---|
| Latest release | Yes |
| Older releases | No |

## Reporting a vulnerability

**Please don't open a public issue for a security problem.**

Report it privately through GitHub: go to the repository's **Security** tab and choose **Report a vulnerability**. Only the maintainer can see the report.

If you can't use GitHub, email w7orb.radio@gmail.com with "Maidenhead Mapper security" in the subject.

Please include:

- the plugin and QGIS versions, and your operating system;
- what an attacker can do, and what they need (for example, "anyone on the same network");
- steps or a sample file or packet that shows the problem.

## What to expect

- An acknowledgement within 7 days.
- An assessment, and a plan or a reason for declining, within 30 days.
- Credit in the changelog when a fix is released, unless you'd rather stay anonymous.

Please give a reasonable time for a fix, normally up to 90 days, before you disclose the problem publicly.

## Scope

Security problems include:

- **The live WSJT-X listener.** In multicast mode it listens on all network interfaces (`0.0.0.0`) on the chosen UDP port, so any device on the local network can send it packets. A packet that crashes QGIS, hangs it, uses unbounded memory, or runs code is in scope.
- **File imports.** ALL.TXT, PSK Reporter JSON and other files the plugin reads, where a crafted file causes more than a clear error message.
- **Network downloads.** Data fetched from PSK Reporter, KC2G and other services, if a malicious or intercepted response can do harm.
- **Cached and saved files.** Cache files in the QGIS settings folder, and saved sessions.

Not in scope:

- Problems in QGIS itself, Qt, Python or WSJT-X. Please report those to their own projects.
- Wrong propagation results, wrong positions or other incorrect output. Please open a normal issue for those.
- Attacks that need the attacker to already control your computer or your QGIS profile.

## Tips for users

- In multicast mode the live map accepts packets from your local network. Use it on networks you trust, or choose unicast on `127.0.0.1` if WSJT-X runs on the same computer and nothing else needs the feed.
- Install the plugin from the QGIS plugin repository or from this repository's releases, not from copies passed around elsewhere.
