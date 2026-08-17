# Prism — Terminal

**One prompt → many specialist AIs, in sequence.**

Prism is a modern terminal app (in the spirit of Claude Code) that takes a
single task, uses **Groq** as a routing brain to split it into targeted prompts,
and then drives your **logged-in Chrome** through each specialist AI — grounded
research, deep reasoning, writing, visuals, media, and shipped apps — passing
each stage's output forward as context to the next.

This is a ground-up rewrite of the original Prism (which watched a `notes.txt`
in Google Drive). There is **no Google Drive** here — you type prompts directly,
and the tool catalogue spans every field, not just web design.

---

## Quick start

**macOS** (double-click works too):
```bash
cd prism_terminal
./run_prism.command          # first run builds a venv + installs deps, then launches
```

**Windows**:
```bat
cd prism_terminal
run_prism.bat
```

**Linux**:
```bash
cd prism_terminal
bash run_prism.command
```

or manually:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 prism.py
```

### First-time setup (happens once)

1. **Groq API key** — free from [console.groq.com](https://console.groq.com) (`gsk_…`).
2. **What do you do** — one line; tailors every prompt Prism writes.
3. **Pick your specialists** — one tool per category (skip the ones you don't need).
4. **Log in** — Prism drives your *real* Chrome, so sign in to each chosen tool.
5. **First run** — do a safe **dry run** (routing plan only, no browser) or a full run.

Everything is saved to `~/.prism/config.json` — **you're never asked again.**
Change anything anytime with `/config`, `/agents`, `/key`, `/profile`, `/chrome`.

### Chrome version

Prism auto-detects your Chrome and matches its driver. If you ever see a
"version mismatch" error, pin your version explicitly (find it at
`chrome://settings/help`):

```
prism › /chrome        # shows the detected version, lets you type one to pin
```

Leaving it blank keeps auto-detect. You're also asked once during setup. The
current setting shows in `/status`.

---

## Using the REPL

```
prism › build me a landing page for a coffee subscription startup
prism › /dry summarise the latest research on CRISPR off-target effects
prism › /agents        # re-pick your tools
prism › /catalog       # see every tool, grouped by category
prism › /help
```

Anything that isn't a `/command` is treated as a task and routed.

### Speak or type — the prompt takes both

With `pyaudio` installed (see the opt-in section of `requirements.txt`), the
prompt becomes a gate:

```
prism ›  [space] speak · [t] type
```

- **SPACE** — talk casually (any language; Whisper auto-detects per take),
  press SPACE again to stop. The raw transcript is then **interpreted**
  (Wispr-Flow style): fillers and self-corrections dropped, "dot PDF" → `.pdf`,
  any file you mentioned — however phrased — is located on disk and attached,
  and the remaining task routes through the pipeline. You see each step:
  `heard → understood → 📎 attached → routing`.
- **t** (or any other key) — the usual typed prompt.

Without `pyaudio`, the prompt is typed-only, exactly as before.
(`whisper.py` remains as a standalone dictation reference app.)

Run one-shot from your shell too:

```bash
python3 prism.py "design a pitch deck for a solar startup"
python3 prism.py --dry "explain transformer attention with citations"
python3 prism.py --file report.pdf --file logo.png "summarise this and redesign the logo"
python3 prism.py --config
```

### Attaching files (any type)

Attach **any** file to a task — PDFs, images, audio, video, code, CSVs, zips, datasets:

```
prism › /attach ~/Desktop/paper.pdf ~/refs/moodboard.png
prism › /attach ~/Desktop/campaign_assets      # a FOLDER: attaches everything in it
prism › /files                      # list what's attached
prism › summarise the paper and generate matching cover art
prism › /detach                     # clear when done
```

**Folders work everywhere files do** — typed (`/attach <folder>`), described
(`/find the delta folder in documents`), or spoken ("take the campaign folder
from my desktop and make a reel out of it"). Prism attaches every plain file
inside (capped at 15 — ChatGPT, the file-analysis stage, accepts at most 20
files per message). For a reel/deck from a folder of mixed assets the flow is:
ChatGPT reads all files & images → content writes the script → the media/deck
producer receives the original files. Note Runway Gen-4 uses at most 3
reference images per generation — pick folders with a clear hero logo/shot.

For each attachment Prism does two things:

1. **Extracts text** when it can (txt/md/code/csv/json/pdf/docx/…) and injects
   it into the routing brain *and* every agent's prompt — so even tools without
   an upload box see the content.
2. **Uploads the raw file** to each tool's web UI (via its `<input type="file">`),
   so images/audio/video/zips reach agents that accept them.

Attachments stay attached across tasks until you `/detach`. PDF/DOCX text needs
the optional `pypdf` / `python-docx` deps; without them the files are still
uploaded, just not inlined as text.

**Don't know the path? Describe it** (`/find`, or just talk to `/attach`):

```
prism › /find the BG brochure pdf in the prism terminal folder of prism ai flow in documents
   ✅  Attached BG17.20_brochure.pdf
```

Groq parses the casual description into folder hints + a filename, then a
local fuzzy matcher resolves them against your real disk — "python program"
matches `PythonProgram`, "pros and cons text file" matches `pros_cons.txt`.
Only your description is sent to the AI; your filesystem is never uploaded.
Multiple matches show a numbered list to pick from. `/attach` falls back to
this automatically whenever its argument doesn't exist as a path.

Whenever files are attached, **ChatGPT runs first as the dedicated file
analyst** — it reads every attachment and hands a precise brief to the rest of
the pipeline (skipped if the pipeline already starts with ChatGPT; if the
stage fails, the next stage gets the raw files instead, so nothing is lost). **Producer stages** (visual, media, development, presentation — the
agents that actually make the image, reel, app or deck) also receive the
original file(s) directly: text handoffs dilute a document's exact copy and
can't carry images or video at all, so the maker gets your files undiluted.

### Remote prompts (`/remote`)

Send tasks from your phone (or any device on the same Wi-Fi) and watch them run
in the terminal:

```
prism › /remote            # hosts a local site, prints its URL
   → open the URL on your phone; the page shows a 4-digit code
prism › /remote 3662       # pair that code — terminal now listens
   → anything submitted on the page runs here as a query
prism › /remote stop       # shut the bridge down
```

Pairing is explicit: nothing runs until you type the code into the terminal.
Remote tasks skip the "run this plan?" confirmation (you're not at the
keyboard), and the page shows each task's live status (queued → running → done).
Press Ctrl-C to leave listening mode; the bridge stays up until `/remote stop`.

**Across the internet** (you're travelling, a friend's Prism runs your idea):
deploy `relay_server.py` — a single stdlib-only file — to any free Python host
(Render/Railway/Fly; it respects the `PORT` env var). Then:

```
friend › /remote url https://your-relay.onrender.com    # saved once, forever
you    → open https://your-relay.onrender.com on your phone → page shows 4821
friend › /remote 4821                                   # pairs & listens
you    → type your idea on the page → it runs in their terminal
```

Pairing hands the terminal a secret token, so only the paired terminal can
read prompts. Codes expire after 15 min unpaired; sessions after 24 h idle.

### Email blasts (`/email`)

Mail-merge through the pipeline: attach a **CSV of recipients** plus the
**source material** (a brochure PDF, a doc…), and Prism analyses the material,
has your content agent write the email — locked to output *only* the draft —
then sends it to everyone in the CSV through **your own** email account:

```
prism › /email setup                          # once: address + app password
prism › /attach contacts.csv brochure.pdf
prism › /email invite them to try our new BG17.20 model
   → stage 1: ChatGPT (the dedicated file analyst) distils the brochure
   → stage 2: your content agent replies in strict  SUBJECT: / BODY:  format
   → preview: subject, body, recipient count — nothing sends until you confirm
```

Details that matter:

- No CSV? Just write the addresses in the prompt:
  `/email invite them to the demo — riya@acme.com, dev@beta.io`
- **Don't know the address at all?** `/email sarvam — propose a partnership`:
  with no address anywhere, Prism offers to have your research/brains agent
  search for the recipient's official public contact email, shows you the
  candidates, and only the one YOU pick continues to the draft — which still
  goes through the normal preview-and-confirm before anything is sent.
- **Recipients never reach any AI** — CSV rows are parsed locally (any layout:
  header or no header, addresses in any column), and addresses typed in the
  prompt are stripped out before the goal is sent to the pipeline. Only the
  brochure goes through the agents.
- The drafting agent is told every character it outputs goes to real
  recipients, so no "Here's your email!" chatter survives into the send.
- Write `{name}` in the draft and each recipient gets their own name from the
  CSV (falls back to "there").
- Sending uses stdlib `smtplib` over your SMTP account (Gmail needs an
  [app password](https://myaccount.google.com/apppasswords)); one message per
  recipient, 2 s apart. Only email people who've agreed to hear from you —
  bulk unsolicited mail will get your account flagged.

---

## The catalogue (26 tools · 7 categories)

Multi-domain tools (**Claude**, **ChatGPT**, **LAZYCOOK**, **Kimi 2.6**,
**Claude Design**) appear in more than one category on purpose.

During setup (and any `/agents` re-pick) Prism asks which of your chosen tools
you have a **premium / paid plan** for — the router then gives those tools the
bulk of the work (higher limits, better output), without ever adding stages a
task doesn't need.

| Category | Tools |
|---|---|
| 🧠 Orchestration & Brains | Perplexity · ChatGPT · Claude · LAZYCOOK |
| 📚 Research & Academic | Consensus · WolframAlpha · Semantic Scholar · NotebookLM · LAZYCOOK |
| ✍️ Content, Post & Docs | Jasper · Copy.ai · Kimi 2.6 · Writesonic · Claude · LAZYCOOK |
| 🎨 Visual & Image | Leonardo.ai · Adobe Firefly · Midjourney · ChatGPT |
| 🎬 Video & Audio | Runway · Pika Labs · HeyGen · ElevenLabs · Suno · Claude Design |
| 🛠️ Web, App & Tools | omma.build · emergent.sh · v0.dev · Claude · Kimi 2.6 |
| 📊 Presentations & Decks | Gamma.app · Tome · Claude · Claude Design |

Pipeline order: `research → brains → content → visual → media → development → presentation → summary`.
The router marks stages *not needed* per task, so a pure research question won't
trigger app-building, and vice-versa.

---

## How it works

```
your task
   │
   ▼
Groq pass 1 — enrichment  ──▶  professional task brief (goal, deliverable,
   │                           scope, quality bar, implicit needs)
   ▼
Groq pass 2 — routing     ──▶  routing plan  { stage: {questions[], needed} }
   │                           every prompt engineered: expert role, exact
   │                           deliverable spec, success criteria, non-goals
   │
   ▼
undetected-chromedriver  ──▶  opens each needed tool in your Chrome profile
   │                          types the prompt, waits, scrapes the response
   ▼
output of each stage is injected as context into the next
   │
   ▼
responses + chat links saved to  ~/.prism/runs/run_<ts>.json
```

### Teaching the router your own tool knowledge

Drop a `tool_notes.md` / `tool_notes.txt` / `pros_cons.txt` in `~/.prism/` (or
next to `prism.py`) with your honest, hands-on notes about each tool — "Kimi:
great for frontend, weak for backend; Claude: best for system design", etc.
The router injects these **field notes** into every routing decision and trusts
them over the generic tool descriptions.

---

## WhisperFlow (`whisper.py`) — voice dictation sidekick

A standalone real-time speech-to-text terminal app using Groq Whisper
(`whisper-large-v3`) + LLM cleanup:

```bash
python3 whisper.py                 # push-to-talk: SPACE starts/stops a take, Q quits
python3 whisper.py --auto          # hands-free: VAD segments speech automatically
python3 whisper.py --translate     # speak ANY language → English text out
python3 whisper.py --toggle-key r  # use 'r' instead of SPACE
python3 whisper.py --mode none     # raw transcription, no LLM cleanup
```

- **Push-to-talk by default**: press SPACE, talk casually as long as you like,
  press SPACE again — the whole take is transcribed and cleaned as one piece.
- **Multilingual**: language is auto-detected *per take* (switch languages
  mid-session); detected language is shown next to each transcription. Pin one
  with `--language hi`. LLM cleanup always replies in the language you spoke.
- Needs extra deps (see the optional section of `requirements.txt`); on macOS
  install PortAudio first: `brew install portaudio`.
- Uses the same Groq API key (`GROQ_API_KEY` env var or `--key`).

---

## Files

| File | Role |
|---|---|
| `prism.py` | REPL + slash commands + one-shot CLI |
| `whisper.py` | WhisperFlow — push-to-talk multilingual dictation (standalone) |
| `relay_server.py` | internet relay for `/remote` (deploy anywhere, stdlib-only) |
| `core/agents.py` | tool registry & category → pipeline map |
| `core/router.py` | Groq routing brain: enrichment pass + engineered stage prompts |
| `core/automation.py` | Chrome automation (macOS / Windows / Linux) |
| `core/files.py` | file attachments: text extraction + uploads |
| `core/pathfinder.py` | natural-language file finder (powers `/find`) |
| `core/remote.py` | local Wi-Fi remote-prompt bridge |
| `core/onboarding.py` | first-run wizard |
| `core/config.py` | persistent config at `~/.prism` |
| `core/ui.py` | rich terminal styling |
| `core/voice.py` | speech in, at the prompt |
| `core/lang.py` | which language the tools answer in |
| `run_prism.command` | macOS / Linux launcher (builds venv on first run) |
| `run_prism.bat` | Windows launcher (builds venv on first run) |

### Inbox to Order

The mail-to-order workflow, built with a spring manufacturer at GIDC. Driven
from the desktop app's **Inquiry Automation** screen — there is no REPL command
for it, because it is a screen you leave open rather than a prompt you type.

| File | Role |
|---|---|
| `core/inbox.py` | IMAP reading. `readonly=True` + `BODY.PEEK[]` — never marks, moves or deletes anything, so the owner keeps using Outlook on the same account. Stores UIDVALIDITY beside the last UID, so a renumbered mailbox starts over instead of re-importing years of mail as new inquiries |
| `core/triage.py` | Sorting. Local rules first — unsubscribe headers, auto-replies, known senders — so most mail never leaves the machine; only unknown senders reach the model, batched, with forged prompt boundaries defanged |
| `core/register.py` | The inquiry CSV. Atomic writes, hand-added columns preserved, Indian financial-year numbering (`INQ/25-26/0087`), and "close it in Excel" rather than a lost row when the file is locked |
| `core/quoting.py` | Pricing, from a rate list or from the owner's own cost sheet. `Decimal` with `ROUND_HALF_UP`, lakh grouping, quantity slabs. No AI ever touches a figure |
| `core/mailflow.py` | The daily loop that ties the rest together. Never raises, never sends. Reads a reply with local rules before asking any model |
| `core/sop.py` | Which documents are due to which regular customer |
| `core/po.py` | Reads a purchase order and compares it against what was quoted, judging a rate difference by `gap × quantity` rather than by the gap alone |
| `core/drafting.py` | The few emails a week worth writing well — a negotiation, a reminder — written by the AI tools in the user's own browser rather than through the API. Deliberately *not* used for sorting mail or for touching the register; the module docstring says why |

### Documents and media

| File | Role |
|---|---|
| `core/boq.py` | Bill of quantities from a DXF drawing or a written spec. Counts and measures; never prices |
| `core/reel.py` | Short videos drawn in Pillow — one look, every client |
| `core/reel_web.py` | Prism Studio: the frames are a real web page filmed in a paused Chromium. The design stage is a conversation — one turn for the look and a storyboard, then **one turn per scene**, each laid out and corrected before the next. `scope_css()` confines a scene's stylesheet to that scene so scenes written in separate replies cannot collide |
| `core/ffmpeg.py` | Finds FFmpeg — `PRISM_FFMPEG`, then the copy bundled with Prism, then one downloaded earlier, then `PATH`. Can fetch it, verifying against the digest PyPI publishes for that exact wheel before unpacking |
| `core/mailer.py` | SMTP sending, one message per recipient |
| `core/assets.py` | brand assets for generated media |

## Notes & limits

- **Cross-platform**: Chrome profile discovery, login-tab opening and launchers
  work on macOS, Windows and Linux (`platform.system()` at runtime — the OS is
  never asked or stored).
- Web-UI selectors for the well-known tools (Perplexity, ChatGPT, Claude, Kimi,
  omma, emergent) are hand-tuned; the rest use robust generic selectors and may
  need a tweak in `core/agents.py` as those sites change.
- No passwords are ever stored — Prism reuses your existing Chrome sessions.
- Your Groq key lives only in `~/.prism/config.json` (chmod `600`).
