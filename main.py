"""
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
AI Agent Router System with Grok API (using requests - no base URL)
"""
"""
AI Agent Router System - Playwright Version
"""
import undetected_chromedriver as uc
import requests
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
import os
import time
import json
import pyautogui

# Configuration
GROK_API_KEY = "YOUR_GROK_API"

# AI Agent Specializations
AI_AGENTS = {
    "claude": {
        "url": "https://claude.ai",
        "specialty": "coding, debugging, technical implementation, code review, algorithms",
        "textarea_selector": "div[contenteditable='true']",
    },
    "perplexity": {
    "url": "https://www.perplexity.ai",
    "specialty": "research, fact-checking, current events, academic queries, citations",
    "textarea_selector": "div[contenteditable='true']#ask-input",
    },
    "chatgpt": {
        "url": "https://chatgpt.com",
        "specialty": "general conversation, brainstorming, creative writing, everyday tasks",
        "textarea_selector": "#prompt-textarea",
    },
    "gemini": {
        "url": "https://gemini.google.com",
        "specialty": "multimodal tasks, Google integration, data analysis, summaries",
        "textarea_selector": "div[contenteditable='true'][role='textbox']",
    }
}


def analyze_and_route_with_grok(notes):
    """Use your working Grok API implementation"""

    routing_prompt = f"""You are an intelligent AI routing system. 

    TASK:
    1. Read each question/topic in the user's notes
    2. For EACH question, generate 3-4 related follow-up questions that:
       - Cover different aspects of the topic
       - Anticipate what the user might want to know next
       - Provide comprehensive coverage so user doesn't need to research further
    3. Route ALL generated questions to the BEST specialized AI agent

    Available AI Agents:
    - claude: {AI_AGENTS['claude']['specialty']}
    - perplexity: {AI_AGENTS['perplexity']['specialty']}
    - chatgpt: {AI_AGENTS['chatgpt']['specialty']}
    - gemini: {AI_AGENTS['gemini']['specialty']}

    User's notes/questions:
    {notes}

    EXAMPLE:
    If user asks: "How to learn Python?"
    Generate questions like:
    - "What are the best Python learning resources for beginners?"
    - "What projects should I build to practice Python?"
    - "How long does it take to become proficient in Python?"
    - "What are the most important Python libraries to learn?"

    ROUTING RULES:
    1. For coding/debugging → claude
    2. For research/facts/current events → perplexity
    3. For general chat/creative/learning advice → chatgpt
    4. For data analysis/Google integration → gemini

    Return ONLY valid JSON with this structure:
    {{
        "agent_name": {{
            "questions": ["question 1", "question 2", "question 3", ...],
            "reasoning": "why this agent is best for these questions"
        }}
    }}

    Only include agents that are needed. Return ONLY the JSON, no extra text."""

    try:
        # YOUR WORKING GROK API CALL HERE
        headers = {
            "Authorization": f"Bearer {GROK_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": routing_prompt}],
            "temperature": 0.3
        }

        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload
        )

        response_text = response.json()['choices'][0]['message']['content']
        json_start = response_text.find('{')
        json_end = response_text.rfind('}') + 1

        if json_start == -1:
            return {}

        routing_data = json.loads(response_text[json_start:json_end])
        return routing_data

    except Exception as e:
        print(f"❌ Grok API Error: {e}")
        return {}


def automate_ai_agents(routing_data):
    """Selenium + undetected-chromedriver version"""

    if not routing_data:
        print("❌ No agents to route to")
        return {}

    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    # Setup undetected Chrome
    options = uc.ChromeOptions()
    options.add_argument(r"--user-data-dir=C:\Users\hitar\chrome_automation")
    options.add_argument("--disable-blink-features=AutomationControlled")

    driver = uc.Chrome(options=options, version_main=142)

    agent_tabs = {}

    # Open required agents in separate tabs
    print("\n" + "=" * 70)
    print("📋 GENERATED QUESTIONS PER AGENT:")
    print("=" * 70)
    for agent_name, data in routing_data.items():
        if agent_name not in AI_AGENTS:
            continue

        agent_config = AI_AGENTS[agent_name]  # Define BEFORE try block
        print(f"\n🚀 Opening {agent_name.upper()}...")
        print(f"   Reason: {data.get('reasoning', 'N/A')}")

        try:
            # Open new tab
            if agent_tabs:
                pyautogui.hotkey('ctrl', 't')
                time.sleep(2)
                driver.switch_to.window(driver.window_handles[-1])

            driver.get(agent_config["url"])  # Now agent_config is defined
            time.sleep(4)

            # Send prompts
            for idx, prompt in enumerate(data["questions"], 1):
                try:
                    print(f"   → Question {idx}/{len(data['questions'])}: {prompt[:80]}...")

                    textarea = WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, agent_config["textarea_selector"]))
                    )
                    textarea.clear()
                    textarea.send_keys(prompt)
                    time.sleep(1)
                    textarea.send_keys(Keys.ENTER)

                    time.sleep(45)  # Delay between prompts

                except Exception as e:
                    print(f"   ❌ Prompt error: {e}")

            agent_tabs[agent_name] = {
                "window_handle": driver.current_window_handle,
                "question_count": len(data["questions"])
            }
            time.sleep(100)
        except Exception as e:
            print(f"   ❌ Failed to open {agent_name}: {e}")

    # Wait for responses
    print(f"\n⏳ Waiting 5 minutes for responses...")
    time.sleep(200)

    # Fetch responses
    print(f"\n\n{'=' * 70}")
    print("📥 COLLECTING RESPONSES")
    print(f"{'=' * 70}\n")

    all_responses = {}

    for agent_name, tab_data in agent_tabs.items():
        try:
            driver.switch_to.window(tab_data["window_handle"])

            possible_selectors = [
                "[data-testid*='message']",
                ".message-content",
                ".response",
                "[class*='response']",
                "[class*='message']"
            ]

            responses = []
            for selector in possible_selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        for elem in elements[-tab_data["question_count"]:]:
                            text = elem.text.strip()
                            if text and len(text) > 50:
                                responses.append(text)
                        break
                except:
                    continue

            if responses:
                all_responses[agent_name] = responses
                print(f"\n{'─' * 70}")
                print(f"🤖 AI AGENT: {agent_name.upper()}")
                print(f"{'─' * 70}")

                for idx, resp in enumerate(responses, 1):
                    print(f"\n[Response {idx}]")
                    print(resp[:800] + ("..." if len(resp) > 800 else ""))
            else:
                print(f"\n⚠️ {agent_name}: No responses captured")

        except Exception as e:
            print(f"\n❌ Error fetching from {agent_name}: {e}")

    print(f"\n{'=' * 70}\n")

    # Keep windows open


    return all_responses


def main():
    # Google Drive Authentication with settings
    gauth = GoogleAuth(settings_file='settings.yaml')

    # Try to load saved credentials
    gauth.LoadCredentialsFile("credentials.json")

    if gauth.credentials is None:
        # Authenticate if no credentials
        gauth.LocalWebserverAuth()
    elif gauth.access_token_expired:
        # Refresh if expired
        gauth.Refresh()
    else:
        # Initialize with valid credentials
        gauth.Authorize()

    # Save credentials for next time
    gauth.SaveCredentialsFile("credentials.json")

    drive = GoogleDrive(gauth)

    # Read notes.txt from Drive
    file_list = drive.ListFile({'q': "title='notes.txt'"}).GetList()

    if not file_list:
        print("❌ notes.txt not found in Google Drive!")
        return

    file = file_list[0]
    user_notes = file.GetContentString()

# Rest of your existing code...
    print("\n" + "=" * 70)
    print("🤖 AI AGENT ROUTER SYSTEM")
    print("=" * 70)
    print("\n📝 Your Notes:")
    print(user_notes)

    print("\n" + "=" * 70)
    print("🤖 AI AGENT ROUTER SYSTEM (Playwright)")
    print("=" * 70)
    print("\n📝 Your Notes:")
    print(user_notes)

    print("\n🧠 Grok analyzing and routing...\n")
    routing_data = analyze_and_route_with_grok(user_notes)

    if routing_data:
        print("📊 ROUTING DECISIONS:")
        print(json.dumps(routing_data, indent=2))

        responses = automate_ai_agents(routing_data)

        if responses:
            with open("ai_responses.json", "w", encoding="utf-8") as f:
                json.dump(responses, f, indent=2, ensure_ascii=False)
            print("\n💾 Responses saved to ai_responses.json")
    else:
        print("\n❌ No routing data from Grok")


if __name__ == "__main__":
    main()
