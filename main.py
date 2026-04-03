"""
Prism — AI Agent Router System
Reads notes.txt from Google Drive, routes via Groq (LLaMA-3.3-70b),
then sequentially dispatches to Perplexity → ChatGPT → Claude → Gemini
with full context chaining. Results saved locally; links uploaded to Drive.
"""

import os
import json
import time
import shutil
import requests
import undetected_chromedriver as uc
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import InvalidSessionIdException, WebDriverException
from dotenv import load_dotenv

load_dotenv()

# ─── Configuration ────────────────────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
PREV_NOTES_FILE    = "prev_notes.txt"
RESPONSES_FILE     = "ai_responses.json"
LINKS_FILE         = "ai_links.txt"
# Persistent Chrome automation profile (lives inside the project folder)
CHROME_PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_automation")

# Fixed sequential pipeline order
PIPELINE_ORDER = ["perplexity", "chatgpt", "claude", "gemini"]

# Per-agent config: URL, specialty (for routing prompt), input + response selectors
AI_AGENTS = {
    "perplexity": {
        "url": "https://www.perplexity.ai",
        "domain": "perplexity.ai",
        "search_via_url": True,   # bypass textarea — use URL-encoded query instead
        "specialty": "research, fact-checking, current events, academic queries, citations",
        "input_selector": "textarea",
        "response_selectors": [
            # Search-page answer containers (try multiple since selectors shift with deploys)
            ".prose",
            "[class*='prose']",
            "[class*='answer']",
            "[class*='Answer']",
            "[class*='result']",
            "[class*='markdown']",
            "[data-testid='answer']",
            ".col-span-8 p",
            "section p",          # fallback: any paragraph inside a section
        ],
    },
    "chatgpt": {
        "url": "https://chatgpt.com",
        "domain": "chatgpt.com",
        "specialty": "general conversation, brainstorming, creative writing, concepts, learning advice",
        "input_selector": "#prompt-textarea",
        # Prefer the actual send button over Enter — React needs the click event
        "send_button_selector": "button[data-testid='send-button'], button[aria-label*='Send']",
        # Logged-in indicator: sidebar new-chat button or nav profile avatar
        "login_check_selector": "[data-testid='profile-button'], nav [href='/'], button[aria-label='New chat']",
        "response_selectors": [
            "[data-message-author-role='assistant'] .markdown",
            "[data-message-author-role='assistant'] .prose",
            "[data-message-author-role='assistant']",
            ".agent-turn .markdown",
            "article[data-testid*='conversation-turn'] .markdown",
        ],
    },
    "claude": {
        "url": "https://claude.ai",
        "domain": "claude.ai",
        "specialty": "coding, debugging, technical implementation, code review, algorithms",
        "input_selector": "div[contenteditable='true'][data-placeholder]",
        # Logged-in indicator: the compose area or top-nav new conversation button
        "login_check_selector": "[data-testid='new-chat-button'], button[aria-label='New chat'], .flex-shrink-0.truncate",
        "response_selectors": [
            ".font-claude-message",
            "[data-testid='claude-response']",
            ".assistant-message",
        ],
    },
    "gemini": {
        "url": "https://gemini.google.com",
        "domain": "gemini.google.com",
        "specialty": "multimodal tasks, Google integration, data analysis, synthesis, summaries",
        "input_selector": "div[contenteditable='true'][role='textbox'], rich-textarea div[contenteditable='true']",
        # Logged-in indicator: Google account avatar/menu in top-right
        "login_check_selector": "a[aria-label*='Google Account'], .gb_A, [data-ogsr-up]",
        "response_selectors": [
            "model-response .markdown",
            "model-response",
            "[class*='response-container'] p",
            "message-content",
            ".response-container p",
            "[data-chunk-index] p",
            ".model-response-text p",
            ".model-response-text",
        ],
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# Change Detection
# ═══════════════════════════════════════════════════════════════════════════════

def _normalize(text: str) -> str:
    """Normalize line endings and strip so comparisons are platform-independent."""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def notes_changed(current_notes: str) -> bool:
    """Return True if notes.txt content differs from last run."""
    if not os.path.exists(PREV_NOTES_FILE):
        return True
    with open(PREV_NOTES_FILE, "r", encoding="utf-8") as f:
        return f.read().strip() != _normalize(current_notes)


def get_prev_notes() -> str:
    """Return the previously saved notes, or empty string if none."""
    if not os.path.exists(PREV_NOTES_FILE):
        return ""
    with open(PREV_NOTES_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def get_new_notes_only(current_notes: str) -> str:
    """
    Return ONLY the lines/paragraphs that are new in current_notes
    compared to what was saved from the last run.

    Strategy:
    - Split both into non-empty lines.
    - Return lines in current that are NOT present in prev (order preserved).
    - If nothing is prev (first run), return everything.
    """
    prev = get_prev_notes()
    if not prev:
        return current_notes

    prev_lines  = set(_normalize(prev).splitlines())
    curr_lines  = _normalize(current_notes).splitlines()
    new_lines   = [l for l in curr_lines if l.strip() and l not in prev_lines]
    return "\n".join(new_lines).strip() or current_notes  # fallback: all if diff empty


def save_prev_notes(notes: str):
    with open(PREV_NOTES_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(_normalize(notes))


# ═══════════════════════════════════════════════════════════════════════════════
# Google Drive
# ═══════════════════════════════════════════════════════════════════════════════

def auth_drive() -> GoogleDrive:
    """Authenticate via OAuth (Chrome) and return a GoogleDrive instance."""
    gauth = GoogleAuth(settings_file="settings.yaml")
    gauth.LoadCredentialsFile("credentials.json")

    if gauth.credentials is None:
        gauth.LocalWebserverAuth()
    elif gauth.access_token_expired:
        try:
            gauth.Refresh()
        except Exception:
            print("  ⚠️  Token refresh failed — re-authenticating…")
            gauth.LocalWebserverAuth()
    else:
        gauth.Authorize()

    gauth.SaveCredentialsFile("credentials.json")
    return GoogleDrive(gauth)


def read_notes_from_drive(drive: GoogleDrive) -> str:
    """Fetch notes.txt content from Google Drive root."""
    file_list = drive.ListFile({"q": "title='notes.txt' and trashed=false"}).GetList()
    if not file_list:
        raise FileNotFoundError("❌  notes.txt not found in Google Drive!")
    return file_list[0].GetContentString()


def upload_links_to_drive(drive: GoogleDrive, links_content: str):
    """Create or overwrite ai_links.txt on Google Drive."""
    with open(LINKS_FILE, "w", encoding="utf-8") as f:
        f.write(links_content)

    file_list = drive.ListFile({"q": "title='ai_links.txt' and trashed=false"}).GetList()
    gfile = file_list[0] if file_list else drive.CreateFile({"title": "ai_links.txt"})
    gfile.SetContentString(links_content)
    gfile.Upload()
    print("☁️   ai_links.txt uploaded to Google Drive.")


# ═══════════════════════════════════════════════════════════════════════════════
# Groq Routing
# ═══════════════════════════════════════════════════════════════════════════════

def route_with_groq(notes: str) -> dict:
    """
    Send notes to Groq (LLaMA-3.3-70b-versatile).
    Returns a routing dict: {agent_name: {questions: [...], reasoning: ...}}
    """
    agent_desc = "\n".join(
        f"  - {name}: {cfg['specialty']}" for name, cfg in AI_AGENTS.items()
    )

    prompt = f"""You are an intelligent AI routing system.

TASK:
1. Read the user's notes/questions.
2. For EACH question generate 2-3 focused follow-up questions covering different angles.
3. Assign ALL questions to the single best-fit AI agent per topic cluster.

Available agents:
{agent_desc}

ROUTING RULES (strict):
- perplexity → research, facts, current events, citations
- chatgpt    → concepts, brainstorming, learning advice, creative tasks
- claude     → code, debugging, technical implementation
- gemini     → data analysis, synthesis, Google-ecosystem tasks

User's notes:
{notes}

Return ONLY valid JSON — absolutely no extra text:
{{
    "agent_name": {{
        "questions": ["question 1", "question 2", ...],
        "reasoning": "why this agent suits these questions"
    }}
}}
Only include agents that are actually needed (1–4 agents total)."""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }

    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()

    text = resp.json()["choices"][0]["message"]["content"]
    j_start = text.find("{")
    j_end   = text.rfind("}") + 1
    if j_start == -1:
        return {}
    return json.loads(text[j_start:j_end])


# ═══════════════════════════════════════════════════════════════════════════════
# Chrome — Persistent Profile + Driver Launch
# ═══════════════════════════════════════════════════════════════════════════════

def get_chrome_profile() -> str:
    """
    Return (and prepare) a persistent Chrome profile directory.
    - Lives in chrome_automation/ inside the project folder.
    - Preserved between runs so logins survive — log in manually on first run.
    - Deletes SingletonLock before launch to avoid lock conflicts.
    """
    os.makedirs(CHROME_PROFILE_DIR, exist_ok=True)

    # Remove stale lock files that prevent Chrome from starting
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_path = os.path.join(CHROME_PROFILE_DIR, lock)
        if os.path.exists(lock_path):
            try:
                os.remove(lock_path)
                print(f"  🔓  Removed stale {lock}")
            except OSError:
                pass

    print(f"📁  Chrome profile → {CHROME_PROFILE_DIR}")
    print("    (Log in to each AI site on first run — sessions are saved for future runs.)")
    return CHROME_PROFILE_DIR


def launch_driver(profile_dir: str) -> uc.Chrome:
    """Launch undetected Chrome with the persistent profile."""
    options = uc.ChromeOptions()
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    driver = uc.Chrome(options=options, version_main=146)
    driver.set_window_size(1400, 900)
    return driver


# ═══════════════════════════════════════════════════════════════════════════════
# Agent Interaction
# ═══════════════════════════════════════════════════════════════════════════════

def scrape_response(driver, selectors: list) -> str:
    """Try each CSS selector in order; return the longest non-trivial text found."""
    best = ""
    for sel in selectors:
        try:
            elems = driver.find_elements(By.CSS_SELECTOR, sel)
            if elems:
                text = elems[-1].text.strip()
                if len(text) > len(best):
                    best = text
        except Exception:
            pass
    return best


def js_type_text(driver, element, text: str):
    """
    Type text into a form element in a way that both works visually AND triggers
    React's synthetic event system (onChange / onInput).

    Strategy:
    1. Use execCommand('insertText') to insert text atomically (preserves \n).
    2. Dispatch a native InputEvent so React's onChange fires and recognizes
       the new value — without this, React SPAs (ChatGPT, Gemini…) see an
       empty field even though the text is visible in the box.
    3. Fall back to chunked send_keys if JS path fails.
    """
    js = """
        var el = arguments[0];
        var text = arguments[1];
        el.focus();

        // Clear existing content
        if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
            var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype, 'value'
            ) || Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
            if (nativeInputValueSetter) {
                nativeInputValueSetter.set.call(el, text);
            } else {
                el.value = text;
            }
        } else {
            // contenteditable
            document.execCommand('selectAll', false, null);
            document.execCommand('delete', false, null);
            document.execCommand('insertText', false, text);
        }

        // Fire React-compatible events so the framework registers the change
        el.dispatchEvent(new Event('input',  { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    """
    try:
        driver.execute_script(js, element, text)
        time.sleep(0.5)
        # Verify something landed
        val = driver.execute_script(
            "return arguments[0].value || arguments[0].innerText || '';", element
        )
        if val.strip():
            return  # success
    except Exception:
        pass

    # Fallback: chunked send_keys
    try:
        element.click()
        time.sleep(0.3)
        chunk_size = 200
        for i in range(0, len(text), chunk_size):
            element.send_keys(text[i: i + chunk_size])
            time.sleep(0.05)
    except Exception:
        pass


def safe_current_url(driver) -> str:
    """Return driver.current_url without crashing if the session is dead."""
    try:
        return driver.current_url
    except (InvalidSessionIdException, WebDriverException):
        return "(session unavailable)"


# ═══════════════════════════════════════════════════════════════════════════════
# Pre-flight Login Check
# ═══════════════════════════════════════════════════════════════════════════════

LOGIN_BLOCK_KEYWORDS = ("login", "signin", "sign-in", "/auth", "accounts.google", "/account")


def _page_seems_logged_in(driver, agent_name: str) -> bool:
    """
    Non-navigating check: look at the current page in the already-open tab.

    Strategy (in order):
      1. If the URL contains login/auth keywords → definitely NOT logged in.
      2. If agent has a login_check_selector and any element matches → logged in.
      3. If the chat input is present → assume accessible (handles Perplexity
         which works both signed-in and signed-out).
    """
    cfg = AI_AGENTS[agent_name]
    try:
        url = safe_current_url(driver).lower()
        if any(kw in url for kw in LOGIN_BLOCK_KEYWORDS):
            return False

        login_sel = cfg.get("login_check_selector", "")
        if login_sel:
            if driver.find_elements(By.CSS_SELECTOR, login_sel):
                return True

        # Fallback: input present → usable
        return bool(driver.find_elements(By.CSS_SELECTOR, cfg["input_selector"]))

    except (InvalidSessionIdException, WebDriverException):
        return False
    except Exception:
        return False


def check_and_ensure_logins(driver, agents_to_use: list) -> dict:
    """
    Pre-flight: opens every assigned agent site in its own dedicated tab,
    checks login status, and pauses for manual login if needed.

    Returns
    -------
    dict  {agent_name: window_handle}  — one tab per agent, left open.
    The pipeline will switch to these handles directly instead of opening
    fresh tabs, so every agent interaction stays in its own persistent tab.
    """
    print(f"\n{'=' * 70}")
    print("🔍  PRE-FLIGHT: Opening assigned agent sites…")
    print(f"{'=' * 70}")

    agent_tabs: dict = {}   # agent_name → window_handle
    first = True

    # ── Open every agent in its own tab ───────────────────────────────────────
    for agent_name in agents_to_use:
        cfg = AI_AGENTS[agent_name]
        try:
            if not first:
                driver.execute_script("window.open('');")
                time.sleep(0.8)
                driver.switch_to.window(driver.window_handles[-1])
            first = False

            print(f"  📲  {agent_name.upper():<12} → {cfg['url']}")
            driver.get(cfg["url"])
            agent_tabs[agent_name] = driver.current_window_handle
        except (InvalidSessionIdException, WebDriverException) as e:
            print(f"  ❌  Could not open {agent_name}: {e}")

    # Let all pages fully render before checking
    print(f"\n  ⏳  Waiting for pages to load…")
    time.sleep(6)

    # ── Check login status in each tab ────────────────────────────────────────
    not_logged_in = []
    for agent_name, handle in agent_tabs.items():
        try:
            driver.switch_to.window(handle)
            time.sleep(1)
            if _page_seems_logged_in(driver, agent_name):
                print(f"  ✅  {agent_name.upper():<12} — signed in")
            else:
                print(f"  ⚠️  {agent_name.upper():<12} — NOT signed in")
                not_logged_in.append(agent_name)
        except Exception:
            not_logged_in.append(agent_name)

    # ── If any need login, pause and wait ───────────────────────────────────────
    if not_logged_in:
        print(f"\n{'=' * 70}")
        print("🔐  MANUAL LOGIN REQUIRED")
        print(f"{'=' * 70}")
        print("The following sites are open in Chrome tabs — sign in to each:\n")
        for a in not_logged_in:
            print(f"    →  {a.upper():<12}  (tab already open)")
        print()
        try:
            input("   ►  Press Enter here once you've signed in to all of them… ")
        except EOFError:
            pass   # non-interactive environment — just continue
        print()
    else:
        print(f"\n✅  All agents signed in — proceeding…")

    return agent_tabs


def _send_to_perplexity_via_url(driver, full_prompt: str, cfg: dict) -> tuple:
    """
    Submit to Perplexity by navigating directly to:
        https://www.perplexity.ai/search?q=<encoded_prompt>

    This completely avoids DOM interaction (no textarea find, no WebDriverWait,
    no send_keys) which is what was triggering Cloudflare bot-detection and
    killing the ChromeDriver session.
    """
    import urllib.parse

    encoded = urllib.parse.quote_plus(full_prompt)
    search_url = f"https://www.perplexity.ai/search?q={encoded}"

    print(f"\n  🔍  Submitting to PERPLEXITY via URL (no DOM interaction)")
    print(f"  🌐  {search_url[:120]}{'…' if len(search_url) > 120 else ''}")
    try:
        driver.get(search_url)
    except (InvalidSessionIdException, WebDriverException) as e:
        print(f"  ❌  Session lost navigating Perplexity search URL: {e}")
        return "", "(session lost)"

    print(f"  ⏳  Waiting for Perplexity to generate response…")
    time.sleep(15)   # initial wait for answer to start streaming

    response_text = ""
    for attempt in range(12):   # up to 12 × 10 s = 120 s total
        try:
            response_text = scrape_response(driver, cfg["response_selectors"])
            # JS fallback: grab all visible paragraph text from the page body
            if len(response_text) < 100:
                response_text = driver.execute_script(
                    "return Array.from(document.querySelectorAll('p, li'))"
                    ".map(e => e.innerText.trim())"
                    ".filter(t => t.length > 40)"
                    ".join('\\n');"
                ) or ""
        except (InvalidSessionIdException, WebDriverException):
            print(f"  ❌  Session lost while polling Perplexity")
            break
        if len(response_text) > 100:
            print(f"  ✅  Response captured on poll {attempt + 1} ({len(response_text):,} chars)")
            break
        print(f"  ⏳  Polling… attempt {attempt + 1}/12")
        time.sleep(10)

    return response_text, safe_current_url(driver)


def send_to_agent(
    driver, agent_name: str, full_prompt: str, skip_navigation: bool = False
) -> tuple:
    """
    Navigate to agent URL (unless skip_navigation=True, i.e. we're already there),
    type the full prompt, submit, wait for a response, scrape and return
    (response_text, current_url).

    skip_navigation=True is set when the tab was pre-opened by the pre-flight
    check and we're already on the correct domain — avoids the second driver.get()
    call that triggers Perplexity's bot-detection and kills the session.
    """
    cfg  = AI_AGENTS[agent_name]
    wait = WebDriverWait(driver, 30)

    # ── Perplexity: URL-based submission (no DOM interaction needed) ──────────
    if cfg.get("search_via_url"):
        return _send_to_perplexity_via_url(driver, full_prompt, cfg)

    if skip_navigation:
        print(f"\n  ⏭️  Already on {agent_name.upper()} — skipping navigation")
        time.sleep(2)   # small settle delay
    else:
        print(f"\n  🌐  Loading {agent_name.upper()} → {cfg['url']}")
        try:
            driver.get(cfg["url"])
        except (InvalidSessionIdException, WebDriverException) as e:
            print(f"  ❌  Browser session lost before loading {agent_name}: {e}")
            return "", "(session lost)"

        # Perplexity needs more time — it redirects and hydrates the React app
        load_wait = 8 if agent_name == "perplexity" else 5
        time.sleep(load_wait)

    # ── Find input (gentle polling — avoids WebDriverWait's 500ms DOM storm) ──
    textarea = None
    for attempt in range(15):   # up to 15 × 2 s = 30 s
        try:
            elems = driver.find_elements(By.CSS_SELECTOR, cfg["input_selector"])
            if elems and elems[0].is_displayed():
                textarea = elems[0]
                break
        except (InvalidSessionIdException, WebDriverException) as e:
            print(f"  ❌  Browser session lost while waiting for input ({agent_name}): {e}")
            return "", safe_current_url(driver)
        except Exception:
            pass
        print(f"  ⏳  Input not ready yet — attempt {attempt + 1}/15…")
        time.sleep(2)

    if textarea is None:
        print(f"  ⚠️  Input not found for {agent_name} after retries")
        return "", safe_current_url(driver)

    # ── Type prompt via JS (triggers React onChange properly) ──────────────────
    try:
        js_type_text(driver, textarea, full_prompt)
        time.sleep(1.5)
    except (InvalidSessionIdException, WebDriverException) as e:
        print(f"  ❌  Browser session lost while typing ({agent_name}): {e}")
        return "", safe_current_url(driver)

    # ── Submit ────────────────────────────────────────────────────────────────
    try:
        submitted = False
        # Prefer a dedicated send button (React apps need the click, not just Enter)
        send_sel = cfg.get("send_button_selector", "")
        if send_sel:
            btns = driver.find_elements(By.CSS_SELECTOR, send_sel)
            if btns and btns[0].is_displayed():
                btns[0].click()
                submitted = True
                print(f"  ✅  Prompt submitted via send button — waiting for response…")

        if not submitted:
            # Fallback: re-fetch the input and press Enter
            textarea = driver.find_element(By.CSS_SELECTOR, cfg["input_selector"])
            textarea.send_keys(Keys.ENTER)
            print(f"  ✅  Prompt submitted via Enter — waiting for response…")

    except (InvalidSessionIdException, WebDriverException) as e:
        print(f"  ❌  Browser session lost on submit ({agent_name}): {e}")
        return "", safe_current_url(driver)
    except Exception as e:
        print(f"  ⚠️  Submit failed for {agent_name}: {e}")
        return "", safe_current_url(driver)

    # ── Poll for response ─────────────────────────────────────────────────────
    initial_wait = 45   # give the agent time to start generating
    time.sleep(initial_wait)

    response_text = ""
    for attempt in range(8):       # Up to 8 × 20 s = ~2.5 more minutes
        try:
            response_text = scrape_response(driver, cfg["response_selectors"])
        except (InvalidSessionIdException, WebDriverException):
            print(f"  ❌  Browser session lost while polling ({agent_name})")
            break
        if len(response_text) > 100:
            break
        print(f"  ⏳  Polling… attempt {attempt + 1}/8")
        time.sleep(20)

    return response_text, safe_current_url(driver)


# ═══════════════════════════════════════════════════════════════════════════════
# Sequential Pipeline with Context Chaining
# ═══════════════════════════════════════════════════════════════════════════════

def run_pipeline(driver, routing_data: dict, agent_tabs: dict = None) -> tuple:
    """
    Execute assigned agents in fixed order: Perplexity → ChatGPT → Claude → Gemini.
    Each agent receives all previous agents' responses as context (context chaining).

    agent_tabs  — {agent_name: window_handle} returned by check_and_ensure_logins.
                  When provided, the pipeline switches to those existing dedicated tabs
                  instead of opening fresh ones, keeping one tab per agent throughout.

    Returns (all_responses dict, agent_links dict).
    """
    all_responses: dict = {}
    agent_links:   dict = {}
    context_chain: list = []
    agent_tabs = agent_tabs or {}

    for agent_name in PIPELINE_ORDER:
        if agent_name not in routing_data:
            continue

        agent_data = routing_data[agent_name]
        questions  = agent_data.get("questions", [])
        if not questions:
            continue

        print(f"\n{'=' * 70}")
        print(f"🤖  Agent: {agent_name.upper()}")
        print(f"   Reason: {agent_data.get('reasoning', 'N/A')}")
        print(f"   Questions ({len(questions)}):")
        for q in questions:
            print(f"     • {q}")

        # ── Build full prompt (questions + context chain) ─────────────────────
        context_block = ""
        if context_chain:
            context_block = (
                "\n\n========== CONTEXT FROM PREVIOUS AI AGENTS ==========\n"
                + "\n\n".join(context_chain)
                + "\n========== END OF CONTEXT ==========\n\n"
                "Use the above context to inform your answers.\n\n"
            )

        full_prompt = context_block + "\n\n".join(
            f"{i + 1}. {q}" for i, q in enumerate(questions)
        )

        # ── Switch to pre-opened tab, or open a fresh one ─────────────────────
        already_on_site = False
        if agent_name in agent_tabs:
            handle = agent_tabs[agent_name]
            try:
                # Verify the session is still alive before switching
                _ = driver.window_handles  # raises if session is dead
                driver.switch_to.window(handle)
                print(f"  🔄  Reusing tab for {agent_name.upper()}")
                # Check if we're already on the right domain (avoids re-navigation)
                try:
                    current = driver.current_url.lower()
                    if AI_AGENTS[agent_name].get("domain", "") in current:
                        already_on_site = True
                except Exception:
                    pass
            except (InvalidSessionIdException, WebDriverException):
                # Session is completely dead — cannot recover without relaunch
                print(f"  ❌  Session dead when switching to {agent_name} tab — skipping")
                all_responses[agent_name] = ""
                agent_links[agent_name] = "(session lost)"
                continue
            except Exception:
                # Handle is stale — open a fresh tab instead
                print(f"  ⚠️  Stale tab for {agent_name} — opening fresh tab")
                try:
                    driver.execute_script("window.open('');")
                    time.sleep(1)
                    driver.switch_to.window(driver.window_handles[-1])
                except (InvalidSessionIdException, WebDriverException):
                    print(f"  ❌  Session dead — skipping {agent_name}")
                    all_responses[agent_name] = ""
                    agent_links[agent_name] = "(session lost)"
                    continue
        elif all_responses:   # 2nd+ agent with no pre-opened tab
            driver.execute_script("window.open('');")
            time.sleep(1.5)
            driver.switch_to.window(driver.window_handles[-1])
        # (1st agent, no pre-opened tab: use whatever tab is current)

        # ── Interact with the agent ───────────────────────────────────────────
        response_text, final_url = send_to_agent(
            driver, agent_name, full_prompt, skip_navigation=already_on_site
        )

        all_responses[agent_name] = response_text
        agent_links[agent_name]   = final_url

        # ── Add to context chain for downstream agents ────────────────────────
        if response_text:
            preview = response_text[:2000] + ("…" if len(response_text) > 2000 else "")
            context_chain.append(f"[{agent_name.upper()}]\n{preview}")
            print(f"\n  📥  Response captured ({len(response_text):,} chars)")
            print(f"  🔗  URL: {final_url}")
        else:
            print(f"\n  ⚠️  No response captured from {agent_name}")

    return all_responses, agent_links


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "=" * 70)
    print("🌈  PRISM — AI Agent Router System")
    print("=" * 70)

    # ── 1. Authenticate Google Drive ──────────────────────────────────────────
    print("\n🔐  Authenticating with Google Drive…")
    drive = auth_drive()

    # ── 2. Read notes.txt from Drive ──────────────────────────────────────────
    print("📂  Reading notes.txt from Google Drive…")
    try:
        user_notes = read_notes_from_drive(drive)
    except FileNotFoundError as e:
        print(e)
        return

    print(f"\n📝  Notes:\n{'-' * 40}\n{user_notes}\n{'-' * 40}")

    # ── 3. Change detection ───────────────────────────────────────────────────
    if not notes_changed(user_notes):
        print("\n✅  Notes unchanged since last run — skipping. (it's the same)")
        return

    # Extract only the NEW lines BEFORE saving (prev_notes.txt still has old content here)
    notes_to_route = get_new_notes_only(user_notes)

    # NOW overwrite prev_notes so next run compares against the full current notes
    save_prev_notes(user_notes)
    print("\n🔄  Notes have changed — proceeding.\n")

    if notes_to_route != _normalize(user_notes):
        print(f"📌  Routing only NEW content:\n{'-' * 40}\n{notes_to_route}\n{'-' * 40}\n")
    else:
        print("📌  First run or full re-route — routing all notes.\n")

    # ── 4. Groq routing ───────────────────────────────────────────────────────
    print("🧠  Groq (LLaMA-3.3-70b) analyzing and routing…\n")
    try:
        routing_data = route_with_groq(notes_to_route)
    except Exception as e:
        print(f"❌  Groq error: {e}")
        return

    if not routing_data:
        print("❌  No routing data returned from Groq.")
        return

    print("📊  Routing decisions:\n" + json.dumps(routing_data, indent=2))

    # ── 5. Prepare persistent Chrome profile + launch browser ────────────────
    profile_dir = get_chrome_profile()
    print("\n🚀  Launching Chrome…")
    driver = launch_driver(profile_dir)

    try:
        # ── 6. Pre-flight login check (opens one tab per agent) ────────────────
        agents_in_use = [a for a in PIPELINE_ORDER if a in routing_data]
        agent_tabs = check_and_ensure_logins(driver, agents_in_use)

        # ── 7. Sequential pipeline (reuses those same tabs) ─────────────────────
        all_responses, agent_links = run_pipeline(driver, routing_data, agent_tabs)

        # ── 8. Save responses locally ─────────────────────────────────────────
        with open(RESPONSES_FILE, "w", encoding="utf-8") as f:
            json.dump(all_responses, f, indent=2, ensure_ascii=False)
        print(f"\n💾  Responses saved → {RESPONSES_FILE}")

        # ── 9. Build links file and upload to Drive ───────────────────────────
        links_content = "\n".join(
            f"{name}: {url}" for name, url in agent_links.items()
        )
        if links_content:
            upload_links_to_drive(drive, links_content)
            print(f"\n🔗  Agent links:\n{links_content}")

    finally:
        print("\n🏁  Pipeline complete. Tabs left open for review.")
        # Browser intentionally NOT closed — user reviews conversations


if __name__ == "__main__":
    main()
