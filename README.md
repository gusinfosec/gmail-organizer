# 📬 Gmail Organizer

**Your inbox, filed and archived — automatically. Rules first, AI only for
what's left, and it never reads your email bodies.**

Gmail Organizer tames a messy inbox with three passes:

1. **Labels** — rule-based by sender/domain (instant, free, offline)
2. **Archive** — moves old labeled mail out of the Inbox (kept in All Mail)
3. **Trash** — bins senders you never want to hear from again
4. **AI** — classifies whatever the rules missed, **reading only the sender
   and subject line — never the body**

The result: your Inbox only shows what needs *you* — everything else is filed
under its label, and nothing is ever deleted unless a rule says so.

## 🔒 Privacy

- **Reads sender + subject only.** Message bodies are never fetched or sent anywhere.
- **AI runs locally by default** (Ollama) — zero data leaves your machine. A
  Google Gemini option exists if you want it.
- Everything happens inside your own Gmail account via the official Gmail API.

## ⚙️ Setup (one-time, ~3 minutes)

```bash
# 1. Install dependencies
pip install google-api-python-client google-auth-oauthlib google-auth pyyaml
#    (Ollama users: install + run Ollama, then pull a model, e.g. llama3.2)

# 2. Enable the Gmail API for your Google account
#    https://console.cloud.google.com → New Project → APIs & Services
#    → Enable "Gmail API" → Credentials → OAuth 2.0 Client ID → Desktop app
#    → Download JSON → save to ~/.config/gmail-organizer/credentials.json

# 3. First run — opens a browser to authorize once
python3 gmail-organizer.py --dry-run
```

The first run creates a sample `rules.yaml` at
`~/.config/gmail-organizer/rules.yaml` — edit the senders and labels to match
your inbox, then re-run.

## 🚀 Usage

| Command | What it does |
|---|---|
| `python3 gmail-organizer.py` | Run all sections |
| `python3 gmail-organizer.py --dry-run` | Preview everything, change nothing |
| `python3 gmail-organizer.py --only labels` | Rule-based labeling only |
| `python3 gmail-organizer.py --only archive` | Archive pass only |
| `python3 gmail-organizer.py --only trash` | Trash pass only |
| `python3 gmail-organizer.py --only ai` | AI classification only |
| `python3 gmail-organizer.py --only interactive` | Review AI suggestions one-by-one |
| `python3 gmail-organizer.py --version` | Show version |

## ⏰ Run it on a schedule (systemd)

```ini
# ~/.config/systemd/user/gmail-organizer.service
[Unit]
Description=Gmail Organizer

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /home/you/gmail-organizer.py --dry-run   # ← remove --dry-run when happy

[Install]
WantedBy=default.target
```

```ini
# ~/.config/systemd/user/gmail-organizer.timer
[Timer]
OnCalendar=*-*-* 08:00,13:00,20:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user enable --now gmail-organizer.timer
```

## 🤖 AI providers

| Provider | Setup | Privacy |
|---|---|---|
| **ollama** (default) | `ollama pull llama3.2` — no key needed | 🔒 100% local |
| **gemini** | Get a free key at aistudio.google.com, `pip install google-generativeai`, set `GEMINI_API_KEY` | ☁️ Google |

## 📜 License

MIT — free, open source, do whatever you want with it. A star is appreciated ⭐

---

*Part of the [CyberGlobal](https://cyberglobal.ai) tiny-tools series — small,
useful, privacy-first software. (See also [sweep-lite](https://github.com/gusinfosec/sweep-lite).)*
