#!/usr/bin/env python3
__version__ = "1.0.0"
"""
Gmail Organizer — label, archive, trash, AI classify, and interactive review.

SETUP (one-time):
  1. Install deps:
       sudo pacman -S python-google-api-python-client python-google-auth python-google-auth-oauthlib

  2. Create a Google Cloud project and enable the Gmail API:
       https://console.cloud.google.com/
       → New project → APIs & Services → Enable APIs → search "Gmail API" → Enable
       → Credentials → Create Credentials → OAuth 2.0 Client ID → Desktop app
       → Download JSON → save to ~/.config/gmail-organizer/credentials.json

  3. OAuth consent screen (same page):
       → OAuth consent screen → External → Add your Gmail as a test user

  4. Edit ~/.config/gmail-organizer/rules.yaml with your rules

  5. First run (opens browser for auth):
       python3 ~/scripts/gmail-organizer.py --dry-run

USAGE:
  python3 gmail-organizer.py                  # run all sections
  python3 gmail-organizer.py --dry-run        # preview only, no changes
  python3 gmail-organizer.py --only labels
  python3 gmail-organizer.py --only archive
  python3 gmail-organizer.py --only trash
  python3 gmail-organizer.py --only ai
  python3 gmail-organizer.py --only interactive

AI PROVIDERS (set in rules.yaml under ai.provider):
  ollama  — local, free, no API key needed (default). Needs ollama running.
  gemini  — Google Gemini free tier. Get a key at https://aistudio.google.com/app/apikey
             then: pip install google-generativeai --break-system-packages
"""

import argparse
import sys
import re
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Load ~/.env so API keys are available without re-sourcing the shell
_env_file = Path.home() / '.env'
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _, _v = _line.partition('=')
            os.environ.setdefault(_k.strip(), _v.strip().strip('"'))

import yaml
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES     = ['https://www.googleapis.com/auth/gmail.modify']
CONFIG_DIR = Path.home() / '.config' / 'gmail-organizer'
CREDS_FILE = CONFIG_DIR / 'credentials.json'
TOKEN_FILE = CONFIG_DIR / 'token.json'
RULES_FILE = CONFIG_DIR / 'rules.yaml'

# ── ntfy notification (posts run summary to the gmail-organizer topic) ─────────
NTFY_TOKEN_FILE = Path.home() / '.config' / 'ntfy' / 'token'
NTFY_DEFAULT_URL = None
# Optional: set to an ntfy topic to get push summaries, e.g.
#   NTFY_DEFAULT_URL = 'https://ntfy.sh/your-secret-topic'

# Collects (section, detail) pairs during a run for the ntfy summary
RUN_SUMMARY = []


def send_ntfy(body, url=NTFY_DEFAULT_URL, title='Gmail Organizer'):
    """POST a message to the ntfy topic. No-op on dry-run or when
    no URL is configured."""
    if not url:
        return
    import urllib.request, urllib.error
    token = ''
    try:
        token = NTFY_TOKEN_FILE.read_text().strip()
    except FileNotFoundError:
        pass
    headers = {'Title': title, 'Tags': 'email,robot', 'Priority': '3', 'X-Markdown': 'yes'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(
        url, data=body.encode('utf-8'), headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except Exception as e:  # noqa: BLE001
        print(f"  [ntfy] post failed: {e}")
        return None


def build_summary(notify_cfg, results):
    """Build the ntfy message body from collected run results."""
    stamp = datetime.now().strftime('%A, %b %d · %I:%M %p')
    lines = [f"📬 **Gmail Organizer** · {stamp}", ""]
    icons = {'Labels': '🏷️', 'Archive': '📦', 'Trash': '🗑️', 'AI': '🤖'}
    for section, detail in results:
        icon = icons.get(section, '•')
        lines.append(f"{icon} **{section}:** {detail}")
    return "\n".join(lines)

SYSTEM_LABELS = {
    'INBOX', 'UNREAD', 'IMPORTANT', 'SENT', 'DRAFT', 'SPAM', 'TRASH', 'STARRED',
    'CATEGORY_PROMOTIONS', 'CATEGORY_SOCIAL', 'CATEGORY_UPDATES',
    'CATEGORY_FORUMS', 'CATEGORY_PERSONAL',
}

SAMPLE_RULES = """\
# Gmail Organizer Rules
# Sender values: full address (user@example.com) or domain (example.com)

rules:

  # ── Auto-label by sender/domain ───────────────────────────────────────────
  labels:
    - name: "To Read"
      senders:
        - "substack.com"
        - "medium.com"
        - "mailchimp.com"
        - "beehiiv.com"
        - "convertkit.com"
        - "newsletter@"
        - "digest@"
    - name: "Finance"
      senders:
        - "paypal.com"
        - "stripe.com"
        - "bank@example.com"   # ← replace with your bank
    - name: "Dev"
      senders:
        - "github.com"
        - "gitlab.com"

  # ── Archive (remove from Inbox, keep in All Mail) ─────────────────────────
  archive:
    - older_than_days: 14
      has_label: "To Read"
    - older_than_days: 30
      has_label: "Follow Up"
    - older_than_days: 60
      has_label: "Awaiting Response"
    - older_than_days: 14
      has_label: "Dev"
    - older_than_days: 90
      has_label: "Finance"
    - older_than_days: 180

  # ── Trash (move to bin) ───────────────────────────────────────────────────
  trash:
    - senders:
        - "deals@example.com"   # ← replace with real unwanted senders

  # ── AI classification (handles emails not matched by rules above) ─────────
  ai:
    enabled: true
    provider: ollama          # ollama (local/free) | gemini (free API key)
    model: llama3             # ollama model. gemini uses: gemini-1.5-flash
    # gemini_api_key: ""      # paste your Gemini key here if using gemini
    max_emails: 100           # max emails to classify per run
    # Labels the AI is allowed to assign. Add/remove to match your workflow.
    labels:
      - "To Read"
      - "Finance"
      - "Dev"
      - "Follow Up"
      - "Awaiting Response"
    # AI can also suggest these actions (no label applied, just the action)
    actions:
      - archive
      - trash
      - skip                  # skip = leave for interactive review

  # ── Interactive review (anything AI skipped or AI is disabled) ────────────
  interactive:
    enabled: true
    max_threads: 50
"""


# ── Auth ──────────────────────────────────────────────────────────────────────

def authenticate():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDS_FILE.exists():
                print(f"\nERROR: credentials.json not found at {CREDS_FILE}")
                print("See the setup instructions at the top of this script.")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return build('gmail', 'v1', credentials=creds)


# ── Gmail helpers ─────────────────────────────────────────────────────────────

def get_all_labels(service):
    result = service.users().labels().list(userId='me').execute()
    return {l['name']: l['id'] for l in result.get('labels', [])}


def get_or_create_label(service, name, cache):
    if name in cache:
        return cache[name]
    label = service.users().labels().create(userId='me', body={
        'name': name,
        'labelListVisibility': 'labelShow',
        'messageListVisibility': 'show',
    }).execute()
    cache[name] = label['id']
    print(f"    Created new label: {name}")
    return label['id']


def search_messages(service, query, max_results=1000):
    messages, page_token = [], None
    while len(messages) < max_results:
        kwargs = dict(userId='me', q=query, maxResults=min(500, max_results - len(messages)))
        if page_token:
            kwargs['pageToken'] = page_token
        result = service.users().messages().list(**kwargs).execute()
        messages.extend(result.get('messages', []))
        page_token = result.get('nextPageToken')
        if not page_token:
            break
    return messages


def batch_modify(service, msg_ids, add_labels=None, remove_labels=None, dry_run=False, desc=''):
    if not msg_ids:
        return 0
    if dry_run:
        print(f"    [dry-run] would {desc}: {len(msg_ids)} message(s)")
        return len(msg_ids)
    for i in range(0, len(msg_ids), 1000):
        chunk = msg_ids[i:i + 1000]
        body  = {'ids': chunk}
        if add_labels:
            body['addLabelIds'] = add_labels
        if remove_labels:
            body['removeLabelIds'] = remove_labels
        service.users().messages().batchModify(userId='me', body=body).execute()
    print(f"    {desc}: {len(msg_ids)} message(s)")
    return len(msg_ids)


def senders_to_query(senders):
    return '(' + ' OR '.join(f'from:{s}' for s in senders) + ')'


def thread_msg_ids(service, thread_ids):
    ids = []
    for tid in thread_ids:
        t = service.users().threads().get(userId='me', id=tid, format='minimal').execute()
        ids.extend(m['id'] for m in t.get('messages', []))
    return ids


# ── AI backend ────────────────────────────────────────────────────────────────

BATCH_SIZE = 25  # emails per API call


def _build_batch_prompt(emails, labels):
    all_choices = labels + ['archive', 'trash', 'skip']
    lines = '\n'.join(
        f'{i+1}. From: {e["sender"]} | Subject: {e["subject"]}'
        for i, e in enumerate(emails)
    )
    return f"""Classify each email below into exactly one category.

Categories: {', '.join(all_choices)}

Guidance:
- To Read: newsletters, blogs, articles, digests, announcements
- Finance: invoices, receipts, payments, bank/card/loan alerts, billing
- Dev: GitHub, code, developer tools, CI/CD, technical services
- Follow Up: needs your personal action, reply, or decision
- Awaiting Response: you sent something and are waiting on a reply
- archive: notifications, account alerts, order confirmations, social, low priority
- trash: spam, unwanted ads, promotions, marketing you never asked for
- skip: personal email or genuinely unclear — leave for human review

Emails:
{lines}

Reply with ONLY a JSON array of {len(emails)} category strings, in the same order.
Example: ["Finance", "archive", "To Read"]"""


def _normalize(raw, valid_choices):
    raw = raw.strip().lower()
    for choice in valid_choices:
        if choice.lower() in raw:
            return choice
    return 'skip'


def _parse_batch_response(text, count, valid_choices):
    import json
    import re
    try:
        match = re.search(r'\[.*?\]', text, re.DOTALL)
        if match:
            results = json.loads(match.group())
            normalized = [_normalize(r, valid_choices) for r in results]
            if len(normalized) == count:
                return normalized
    except Exception:
        pass
    return ['skip'] * count


def _call_api(payload_dict, url, headers, timeout=60):
    import urllib.request
    import urllib.error
    import json
    import time

    payload = json.dumps(payload_dict).encode()
    for attempt in range(4):
        req = urllib.request.Request(url, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                wait = 15 * (attempt + 1)   # 15s, 30s, 45s
                print(f"    [AI] Rate limited — waiting {wait}s before retry {attempt + 1}/3...")
                time.sleep(wait)
            else:
                raise


def ai_classify_batch(emails, cfg):
    import os
    provider = cfg.get('provider', 'ollama')
    model    = cfg.get('model', '')
    labels   = cfg.get('labels', [])
    valid    = labels + ['archive', 'trash', 'skip']
    results  = []

    for i in range(0, len(emails), BATCH_SIZE):
        batch  = emails[i:i + BATCH_SIZE]
        prompt = _build_batch_prompt(batch, labels)

        try:
            if provider == 'ollama':
                data = _call_api(
                    {'model': model or 'gemma2:2b',
                     'messages': [{'role': 'user', 'content': prompt}],
                     'stream': False, 'options': {'temperature': 0}},
                    'http://localhost:11434/api/chat',
                    {'Content-Type': 'application/json'},
                    timeout=120,
                )
                text = data['message']['content']

            elif provider in ('openrouter', 'groq'):
                if provider == 'openrouter':
                    api_key  = cfg.get('openrouter_api_key') or os.environ.get('OPENROUTER_API_KEY', '')
                    base_url = 'https://openrouter.ai/api/v1'
                    default_model = 'meta-llama/llama-3.3-70b-instruct:free'
                else:
                    api_key  = cfg.get('groq_api_key') or os.environ.get('GROQ_API_KEY', '')
                    base_url = 'https://api.groq.com/openai/v1'
                    default_model = 'llama-3.3-70b-versatile'
                if not api_key:
                    print(f"    [AI] {provider.upper()}_API_KEY not set")
                    results.extend(['skip'] * len(batch))
                    continue
                data = _call_api(
                    {'model': model or default_model,
                     'messages': [{'role': 'user', 'content': prompt}],
                     'max_tokens': len(batch) * 15, 'temperature': 0},
                    f'{base_url}/chat/completions',
                    {'Content-Type': 'application/json',
                     'Authorization': f'Bearer {api_key}'},
                )
                text = data['choices'][0]['message']['content']

            elif provider == 'gemini':
                try:
                    from google import genai as google_genai
                except ImportError:
                    print("    [AI] Run: pip install google-genai --break-system-packages")
                    results.extend(['skip'] * len(batch))
                    continue
                api_key = cfg.get('gemini_api_key', '') or os.environ.get('GEMINI_API_KEY', '')
                if not api_key:
                    print("    [AI] GEMINI_API_KEY not set in ~/.env or rules.yaml")
                    results.extend(['skip'] * len(batch))
                    continue
                client = google_genai.Client(api_key=api_key)
                resp   = client.models.generate_content(
                    model=model or 'gemini-1.5-flash',
                    contents=prompt,
                )
                text = resp.text

            else:
                print(f"    [AI] Unknown provider: {provider}")
                results.extend(['skip'] * len(batch))
                continue

        except Exception as e:
            print(f"    [AI] {provider} error: {e}")
            results.extend(['skip'] * len(batch))
            continue

        batch_results = _parse_batch_response(text, len(batch), valid)
        results.extend(batch_results)
        print(f"    Classified batch {i // BATCH_SIZE + 1} ({len(batch)} emails)")

    return results


# ── Sections ──────────────────────────────────────────────────────────────────

def run_labels(service, rules, label_cache, dry_run):
    label_rules = rules.get('labels', [])
    if not label_rules:
        return
    print("\n── Auto-label ──")
    labeled = 0
    for rule in label_rules:
        name    = rule.get('name', '').strip()
        senders = rule.get('senders', [])
        if not name or not senders:
            continue
        label_id     = get_or_create_label(service, name, label_cache)
        query        = f"in:inbox {senders_to_query(senders)} -label:{name}"
        msgs         = search_messages(service, query)
        if not msgs:
            print(f"  [{name}] nothing new")
            continue
        keep_in_inbox = {'Follow Up', 'Awaiting Response'}
        remove_inbox  = [] if name in keep_in_inbox else ['INBOX']
        batch_modify(service, [m['id'] for m in msgs],
                     add_labels=[label_id], remove_labels=remove_inbox,
                     dry_run=dry_run, desc=f"label → {name}")
        labeled += len(msgs)
    if labeled:
        RUN_SUMMARY.append(("Labels", f"{labeled} message(s) labeled"))


def run_archive(service, rules, dry_run):
    archive_rules = rules.get('archive', [])
    if not archive_rules:
        return
    print("\n── Archive ──")
    archived = 0
    for rule in archive_rules:
        days      = rule.get('older_than_days')
        has_label = rule.get('has_label', '')
        froms     = rule.get('from', [])
        if days is None:
            continue
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y/%m/%d')
        parts  = [f'in:inbox before:{cutoff}']
        if has_label:
            parts.append(f'label:{has_label}')
        if froms:
            parts.append(senders_to_query(froms))
        query = ' '.join(parts)
        msgs  = search_messages(service, query)
        if not msgs:
            note = f' [{has_label}]' if has_label else ''
            print(f"  [archive{note} >{days}d] nothing matched")
            continue
        note = f' {has_label}' if has_label else ''
        batch_modify(service, [m['id'] for m in msgs],
                     remove_labels=['INBOX'], dry_run=dry_run,
                     desc=f"archive{note} (>{days} days)")
        archived += len(msgs)
    if archived:
        RUN_SUMMARY.append(("Archive", f"{archived} message(s) archived"))


def run_trash(service, rules, dry_run):
    trash_rules = rules.get('trash', [])
    if not trash_rules:
        return
    print("\n── Trash ──")
    for rule in trash_rules:
        senders = rule.get('senders', [])
        if not senders:
            continue
        query = f"{senders_to_query(senders)} -in:trash"
        msgs  = search_messages(service, query)
        if not msgs:
            print(f"  [trash] nothing found for: {', '.join(senders)}")
            continue
        batch_modify(service, [m['id'] for m in msgs],
                     add_labels=['TRASH'], remove_labels=['INBOX'],
                     dry_run=dry_run,
                     desc=f"trash ({', '.join(senders)})")
        RUN_SUMMARY.append(("Trash", f"{len(msgs)} message(s) trashed"))


def run_ai_labels(service, rules, label_cache, dry_run):
    cfg = rules.get('ai', {})
    if not cfg.get('enabled', False):
        return

    max_emails = cfg.get('max_emails', 100)
    provider   = cfg.get('provider', 'ollama')
    model      = cfg.get('model', 'llama3')
    ai_labels  = cfg.get('labels', [])

    print(f"\n── AI Classification ({provider}/{model}) ──")
    print(f"  Fetching up to {max_emails} unlabeled inbox messages...")

    msgs = search_messages(service, 'in:inbox', max_results=max_emails)
    if not msgs:
        print("  Inbox is empty.")
        return

    # Only process messages with no user-defined labels
    to_classify = []
    for m in msgs:
        data = service.users().messages().get(
            userId='me', id=m['id'], format='metadata',
            metadataHeaders=['From', 'Subject'],
        ).execute()
        label_ids  = set(data.get('labelIds', []))
        user_labels = label_ids - SYSTEM_LABELS
        if user_labels:
            continue
        hdrs    = {h['name']: h['value'] for h in data.get('payload', {}).get('headers', [])}
        sender  = hdrs.get('From', '')
        subject = hdrs.get('Subject', '(no subject)')
        to_classify.append({'id': m['id'], 'sender': sender, 'subject': subject})

    if not to_classify:
        print("  All inbox messages already have labels — nothing to classify.")
        return

    print(f"  Classifying {len(to_classify)} unlabeled message(s) in batches of {BATCH_SIZE}...\n")

    decisions = ai_classify_batch(to_classify, cfg)

    # Print results and bucket by decision — skip falls back to Awaiting Response
    buckets = defaultdict(list)
    for item, decision in zip(to_classify, decisions):
        if decision == 'skip':
            decision = 'Awaiting Response'
        print(f"  {decision:22s}  {item['sender'][:32]:32s}  {item['subject'][:48]}")
        buckets[decision].append(item['id'])

    # Summary
    print(f"\n  Results:")
    for decision, ids in sorted(buckets.items()):
        print(f"    {decision}: {len(ids)}")
    total_ai = sum(len(ids) for ids in buckets.values())
    if total_ai:
        parts = sorted(buckets.items(), key=lambda kv: -len(kv[1]))
        breakdown = " · ".join(f"{d.title()}: {len(ids)}" for d, ids in parts)
        RUN_SUMMARY.append(("AI", f"{total_ai} message(s) classified — {breakdown}"))

    # Labels that stay in Inbox (need active attention)
    keep_in_inbox = {'Follow Up', 'Awaiting Response'}

    # Apply labels
    print()
    for decision, ids in buckets.items():
        if decision in ('skip', 'none', ''):
            continue
        elif decision == 'archive':
            batch_modify(service, ids, remove_labels=['INBOX'],
                         dry_run=dry_run, desc="AI → archive")
        elif decision == 'trash':
            batch_modify(service, ids, add_labels=['TRASH'],
                         remove_labels=['INBOX'], dry_run=dry_run,
                         desc="AI → trash")
        elif decision in ai_labels:
            label_id     = get_or_create_label(service, decision, label_cache)
            remove_inbox = [] if decision in keep_in_inbox else ['INBOX']
            batch_modify(service, ids, add_labels=[label_id],
                         remove_labels=remove_inbox,
                         dry_run=dry_run, desc=f"AI → label: {decision}")


def run_interactive(service, rules, label_cache, dry_run):
    cfg = rules.get('interactive', {})
    if not cfg.get('enabled', False):
        return
    max_threads = cfg.get('max_threads', 50)
    print("\n── Interactive Review ──")
    print(f"Loading up to {max_threads} unlabeled inbox threads...")

    result  = service.users().threads().list(userId='me', q='in:inbox', maxResults=max_threads).execute()
    threads = result.get('threads', [])

    sender_map = {}
    for t in threads:
        data = service.users().threads().get(
            userId='me', id=t['id'], format='metadata',
            metadataHeaders=['From', 'Subject'],
        ).execute()
        msgs = data.get('messages', [])
        if not msgs:
            continue
        all_label_ids = {lid for m in msgs for lid in m.get('labelIds', [])}
        if all_label_ids - SYSTEM_LABELS:
            continue
        hdrs    = {h['name']: h['value'] for h in msgs[0].get('payload', {}).get('headers', [])}
        sender  = hdrs.get('From', 'Unknown')
        subject = hdrs.get('Subject', '(no subject)')
        sender_map.setdefault(sender, []).append({'id': t['id'], 'subject': subject})

    if not sender_map:
        print("  No unlabeled threads — inbox is clean!")
        return

    print(f"Found {len(sender_map)} sender(s) with unlabeled threads.\n")

    for sender, tlist in sender_map.items():
        print(f"  From : {sender}")
        print(f"  Count: {len(tlist)} thread(s)")
        for item in tlist[:3]:
            print(f"    • {item['subject'][:70]}")
        if len(tlist) > 3:
            print(f"    … and {len(tlist) - 3} more")
        print("  [l] label  [a] archive  [t] trash  [s] skip  [q] quit")
        try:
            choice = input("  > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if choice == 'q':
            break
        elif choice in ('s', ''):
            print()
            continue
        elif choice == 'a':
            msg_ids = thread_msg_ids(service, [i['id'] for i in tlist])
            batch_modify(service, msg_ids, remove_labels=['INBOX'],
                         dry_run=dry_run, desc="archive")
        elif choice == 't':
            msg_ids = thread_msg_ids(service, [i['id'] for i in tlist])
            batch_modify(service, msg_ids, add_labels=['TRASH'],
                         remove_labels=['INBOX'], dry_run=dry_run, desc="trash")
        elif choice == 'l':
            try:
                label_name = input("  Label name: ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                break
            if label_name:
                label_id = get_or_create_label(service, label_name, label_cache)
                msg_ids  = thread_msg_ids(service, [i['id'] for i in tlist])
                batch_modify(service, msg_ids, add_labels=[label_id],
                             dry_run=dry_run, desc=f"label → {label_name}")
        print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Gmail inbox organizer with AI')
    parser.add_argument('--version', action='version',
                        version=f'gmail-organizer {__version__}')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview changes without touching anything')
    parser.add_argument('--only',
                        choices=['labels', 'archive', 'trash', 'ai', 'interactive'],
                        help='Run only one section instead of all')
    args = parser.parse_args()

    if not RULES_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        RULES_FILE.write_text(SAMPLE_RULES)
        print(f"Sample rules created at:\n  {RULES_FILE}\n")
        print("Edit it to match your inbox, then re-run.")
        sys.exit(0)

    with open(RULES_FILE) as f:
        config = yaml.safe_load(f)
    rules = config.get('rules', {})

    if args.dry_run:
        print("DRY-RUN MODE — no changes will be made\n")

    print("Authenticating with Gmail...")
    service     = authenticate()
    label_cache = get_all_labels(service)
    print("Ready.\n")

    only = args.only
    try:
        if not only or only == 'labels':
            run_labels(service, rules, label_cache, args.dry_run)
        if not only or only == 'archive':
            run_archive(service, rules, args.dry_run)
        if not only or only == 'trash':
            run_trash(service, rules, args.dry_run)
        if not only or only == 'ai':
            run_ai_labels(service, rules, label_cache, args.dry_run)
        if not only or only == 'interactive':
            run_interactive(service, rules, label_cache, args.dry_run)
    except HttpError as e:
        print(f"\nGmail API error: {e}")
        sys.exit(1)

    # ── ntfy notification ──────────────────────────────────────────────────────
    notify_cfg = rules.get('notify', {})
    if not args.dry_run and notify_cfg.get('enabled', True):
        if RUN_SUMMARY:
            url = notify_cfg.get('topic', NTFY_DEFAULT_URL)
            body = build_summary(notify_cfg, RUN_SUMMARY)
            status = send_ntfy(body, url=url)
            if status:
                print(f"\nntfy → {url} ({status})")
        else:
            print("\nNothing to report — ntfy skipped.")

    print("\nDone.")


if __name__ == '__main__':
    main()
