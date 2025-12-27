# AI Agent Router System 🤖

An intelligent AI agent routing system that uses Grok API to process user queries and automate tasks through browser automation and Google Drive integration.

## Features ✨

- **Intelligent Query Routing**: Uses Grok AI to analyze and route user requests
- **Browser Automation**: Automated web interactions using undetected-chromedriver
- **Google Drive Integration**: Seamless file management and storage
- **Structured Response Logging**: Clean, readable text-based output format
- **Multi-tool Support**: Integrates PyAutoGUI for GUI automation

## Prerequisites 📋

- Python 3.10 or higher
- Google Chrome browser installed
- Google Cloud Project with Drive API enabled
- Groq API account

## Installation 🚀

### 1. Clone the Repository

```bash
git clone https://github.com/HitarthTrivedi/prism.git
cd prism
```

### 2. Create Virtual Environment

```bash
python -m venv .venv

# On Windows:
.venv\Scripts\activate

# On macOS/Linux:
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```



**To get your Groq API key:**
1. Visit [https://console.groq.com/](https://console.groq.com/)
2. Sign up or log in
3. Navigate to API Keys section
4. Create a new API key
5. Copy and paste it into your `.env` file

### 5. Set Up Google Drive Authentication

**Step 1: Create Google Cloud Project**
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Enable **Google Drive API**:
   - Go to "APIs & Services" → "Library"
   - Search for "Google Drive API"
   - Click "Enable"

**Step 2: Create OAuth Credentials**
1. Go to "APIs & Services" → "Credentials"
2. Click "CREATE CREDENTIALS" → "OAuth client ID"
3. Configure consent screen if prompted:
   - Choose "External"
   - Fill in app name and your email
   - Add your email as test user
4. Choose "Desktop app" as application type
5. Name it (e.g., "AI Agent Router")
6. Click "Create"
7. **Download the JSON file**
8. Rename it to `client_secrets.json`
9. Place it in the project root directory

## Usage 💡

### Running the Program

```bash
python main.py
```

On first run, a browser window will open asking you to authenticate with Google. Grant the necessary permissions.

### Example Test Prompts

Try these prompts to test different functionalities:

#### 1. **Web Search Task**
```
Search for the latest developments in AI and summarize the top 3 findings
```

#### 2. **Data Analysis Task**
```
Analyze the current trends in cryptocurrency markets and provide a brief report
```

#### 3. **Automation Task**
```
Create a list of the top 5 programming languages in 2024 and their primary use cases
```

#### 4. **Information Gathering**
```
Find information about quantum computing breakthroughs in the last 6 months
```

## Project Structure 📁

```
prism/
├── main.py                 # Main application script
├── .env                    # Environment variables (not in git)
├── .env.example           # Environment template
├── client_secrets.json    # Google OAuth config (not in git)
├── requirements.txt       # Python dependencies
├── README.md             # This file
├── .gitignore            # Git ignore rules
├── ai_response.txt       # AI responses log (generated)
└── mycreds.txt           # Google Drive credentials (generated, not in git)
```

## Output Files 📄

- **`ai_response.txt`**: Contains formatted AI responses with timestamps
- **`mycreds.txt`**: Stores Google Drive authentication tokens (auto-generated)

## Configuration ⚙️

### Customizing the AI Model

In `main.py`, you can change the Grok model:

```python
"model": "llama-3.3-70b-versatile",  # Change this to other available models
```

### Adjusting Response Length

Modify the `max_tokens` parameter:

```python
"max_tokens": 1000,  # Increase for longer responses
```

## Troubleshooting 🔧

### Common Issues

**1. `ModuleNotFoundError: No module named 'distutils'`**
```bash
pip install setuptools
```

**2. `ModuleNotFoundError: No module named 'selenium.webdriver.chromium'`**
```bash
pip install --upgrade selenium
pip install undetected-chromedriver==3.5.4
```

**3. `invalid_grant: Bad Request` (Google Drive Auth)**
```bash
# Delete old credentials and re-authenticate
del mycreds.txt
del credentials.json
python main.py
```

**4. Chrome Version Mismatch**
- Update Chrome to the latest version
- Or specify Chrome version in code:
```python
driver = uc.Chrome(options=options, version_main=YOUR_CHROME_VERSION)
```

## Security Notes 🔒

- **Never commit API keys** to version control
- The `.gitignore` file excludes sensitive files
- Rotate API keys regularly
- Keep `client_secrets.json` private
- Use environment variables for all secrets



## Acknowledgments 🙏

- [Groq](https://groq.com/) for the AI API
- [PyDrive2](https://github.com/iterative/PyDrive2) for Google Drive integration
- [undetected-chromedriver](https://github.com/ultrafunkamsterdam/undetected-chromedriver) for browser automation

## Support 💬

If you encounter any issues or have questions:

1. Check the [Troubleshooting](#troubleshooting-) section
2. Open an issue on GitHub
3. Contact: hitartht318@gmail.com.com

## Roadmap 🗺️

- [ ] Add support for more AI models
- [ ] Implement task scheduling
- [ ] Add GUI interface
- [ ] Enhance error handling
- [ ] Add unit tests
- [ ] Docker containerization

---

**Made By Hitarth Trivedi
