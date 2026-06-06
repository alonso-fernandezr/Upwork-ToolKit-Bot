# Upwork Job Scraper & Notification Bot

A Python-based automation pipeline that monitors Upwork job listings saved as local HTML files, intelligently filters and categorises projects, delivers real-time Telegram notifications, and logs all activity to a structured Google Sheets spreadsheet.

---

## Table of Contents

- [Overview](#overview)
- [How It Works](#how-it-works)
- [Features](#features)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
  - [Environment Variables](#environment-variables)
  - [Telegram Bot Setup](#telegram-bot-setup)
  - [Google Sheets Setup](#google-sheets-setup)
- [Google Sheets Structure](#google-sheets-structure)
- [Telegram Message Format](#telegram-message-format)
- [Filtering Logic](#filtering-logic)
  - [Blocked Keywords](#blocked-keywords)
  - [Hourly Rate Filter](#hourly-rate-filter)
  - [Embedded vs Full Stack Routing](#embedded-vs-full-stack-routing)
  - [High-Value Project Detection](#high-value-project-detection)
- [Running the Bot](#running-the-bot)

---

## Overview

This tool is designed for freelancers who want to monitor Upwork job listings without manually browsing the platform. You save Upwork search result pages as HTML files, and the bot automatically:

1. Detects and parses the HTML file
2. Filters out irrelevant or low-quality jobs
3. Routes qualifying jobs into two categories — **Embedded/Hardware** or **Full Stack**
4. Sends formatted Telegram notifications to the appropriate channel
5. Logs every project (including blocked ones) to Google Sheets
6. Deletes the processed HTML file and waits for the next one

---

## How It Works

```
Upwork HTML file saved to folder
        │
        ▼
  Bot detects file
        │
        ▼
  Parse all job listings
        │
        ▼
  ┌─────────────────────────────┐
  │  Filter: Blocked keyword?   │──► YES ──► Telegram (owner) + Google Sheets (Blocked)
  └─────────────────────────────┘
        │ NO
        ▼
  ┌─────────────────────────────┐
  │  Filter: Hourly rate < $50? │──► YES ──► Telegram (owner) + Google Sheets (Blocked)
  └─────────────────────────────┘
        │ NO
        ▼
  ┌─────────────────────────────┐
  │  Route: Embedded project?   │──► YES ──► Telegram (Embedded channel) + Google Sheets (Embedded)
  └─────────────────────────────┘
        │ NO
        ▼
  Full Stack ──► Telegram (Full Stack channel) + Google Sheets (Full Stack)
```

---

## Features

- **Automatic HTML detection** — polls the working directory for any `upwork*.html` file and processes the most recently modified one
- **Intelligent job filtering** — blocks irrelevant jobs via single keywords and multi-keyword combination rules
- **Hourly rate filtering** — automatically skips hourly jobs where the stated maximum rate is below $50/hr
- **Dual-channel routing** — separates Embedded/Hardware projects from Full Stack projects and delivers each to a dedicated Telegram group
- **High-value project flagging** — marks jobs with a 👍 prefix when the hourly max rate is ≥ $80/hr or fixed budget is ≥ $5,000
- **Rich Telegram messages** — formatted with HTML (bold, italic) and clickable project URLs
- **Google Sheets logging** — all projects (approved and blocked) are written to dedicated subsheets with Upwork-style card formatting and bold titles
- **Blocked project notifications** — filtered jobs are reported to a personal Telegram chat with the exact blocking reason
- **Short URL generation** — strips long Upwork job slugs down to clean `https://www.upwork.com/jobs/~<id>` links
- **Duplicate prevention** — tracks processed URLs in memory to avoid re-sending the same job within a session

---

## Project Structure

```
Upwork-ToolKit-Bot/
├── main.py               # Core scraper, filter, router, and notifier
├── credentials.json      # Google service account key (not committed)
├── .env                  # Environment variables (not committed)
└── README.md
```

---

## Prerequisites

- Python 3.10 or higher
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- Two Telegram group chats (Embedded channel, Full Stack channel)
- A Google Cloud service account with Google Sheets and Drive APIs enabled
- A Google Sheets spreadsheet shared with the service account

---

## Installation

```bash
# Clone the repository
git clone https://github.com/your-username/Upwork-ToolKit-Bot.git
cd Upwork-ToolKit-Bot

# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # macOS / Linux
venv\Scripts\activate           # Windows

# Install dependencies
pip install python-telegram-bot python-dotenv beautifulsoup4 gspread oauth2client
```

---

## Configuration

### Environment Variables

Create a `.env` file in the project root:

```env
TELEGRAM_TOKEN              = "your-bot-token"
TELEGRAM_CHAT_ID_EMBEDDED   = "-100xxxxxxxxxx"   # Embedded projects group
TELEGRAM_CHAT_ID_FULLSTACK  = "-100xxxxxxxxxx"   # Full Stack projects group
GOOGLE_SHEETS_CREDENTIALS_PATH = "credentials.json"
```

> The owner chat ID for blocked project notifications is hardcoded in `main.py` as `TELEGRAM_OWNER_ID`. Update this value to your personal Telegram user ID.

---

### Telegram Bot Setup

1. Open [@BotFather](https://t.me/BotFather) and create a new bot — copy the token into `.env`
2. Create two Telegram groups (e.g. *Upwork Embedded* and *Upwork Full Stack*)
3. Add your bot to both groups as an administrator
4. To find a group chat ID:
   - Add the bot to the group
   - Send any message in the group
   - Run the following snippet to retrieve the chat ID:

```python
import asyncio
import telegram

async def get_updates():
    bot = telegram.Bot("YOUR_BOT_TOKEN")
    async with bot:
        updates = await bot.get_updates()
        for u in updates:
            print(u.message.chat.id, u.message.chat.title)

asyncio.run(get_updates())
```

Group chat IDs are negative numbers (e.g. `-1001234567890`).

---

### Google Sheets Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project
2. Enable **Google Sheets API** and **Google Drive API**
3. Create a **Service Account**, generate a JSON key, and save it as `credentials.json` in the project root
4. Open your Google Sheets spreadsheet and share it with the service account email (found inside `credentials.json` as `client_email`) with **Editor** access
5. Create three subsheets in this exact order (index matters):

| Index | Sheet Name  | Purpose                           |
|-------|-------------|-----------------------------------|
| 0     | Full Stack  | Approved full stack projects      |
| 1     | Embedded    | Approved embedded/HW projects     |
| 2     | Blocked     | Filtered-out projects with reason |

The bot automatically writes column headers (`Posted`, `Project`, `URL`, `Description`) on first run if the sheet rows are empty.

---

## Google Sheets Structure

Each project is written as a single row across **4 columns**:

| Column | Content |
|--------|---------|
| **Posted** | Timestamp when the job was scraped (e.g. `05/26 07:01`) |
| **Project** | Upwork-style card — bold title, payment status, location, price, description snippet, skills, and blocking reason if applicable |
| **URL** | Clean short URL (`https://www.upwork.com/jobs/~<id>`) |
| **Description** | Full job description text |

### Project Card Format (Project column)

```
👍 Senior Python Developer Needed for Raspberry Pi Platform

✅ Verified  ·  💰 $0 spent  ·  📍 United States

$80/hr - $120/hr · Expert · Est. time: 3 to 6 months, Less than 30 hrs/week

We are building a commercial photo platform for entertainment venues...

Python | Flask | Raspberry Pi | FastAPI | REST API
```

For blocked projects an additional line is appended at the bottom:

```
🚫 Blocked: "ukraine"
```

or

```
🚫 Blocked: Hourly rate < $50/hr
```

**Symbol reference:**

| Symbol | Meaning |
|--------|---------|
| 👍 | High-value project (≥ $80/hr or ≥ $5,000 fixed) |
| ✅ | Payment verified |
| ❌ | Payment unverified |
| ❓ | Payment status unknown |
| 💰 | Total client spend history on Upwork |
| 📍 | Client location |
| 🚫 | Blocking reason (Blocked sheet only) |

---

## Telegram Message Format

Notifications sent to channels use Telegram HTML parse mode:

```
Senior Python Developer Needed for Raspberry Pi Platform

Posted:      05/26 07:01
Details:     Hourly: $80.00 - $120.00 | Expert | Est. time: 3 to 6 months
Location:    United States
Payment:     Verified
Total Spent: $0 spent

Project URL:
https://www.upwork.com/jobs/~022058890701204188187

Skills:
Python, Flask, Raspberry Pi, FastAPI, REST API
```

- Title is rendered in **bold**
- All metadata values (posted time, details, location, payment, spent) are rendered in ***bold italic***
- Project URL is a tappable hyperlink that opens directly in a browser

Blocked project notifications sent to the owner's personal chat include the job title, details, location, and the specific keyword or rule that triggered the filter.

---

## Filtering Logic

### Blocked Keywords

Jobs are evaluated against the full combined content string: `title + description + skills + location + details`. There are two tiers of blocking rules:

**Single keywords** — any one match blocks the job immediately:

```
virtual assistant, facebook advertising, illustration, email copywriting,
content writing, sales manager, ukraine, bulgaria, poland, turkey, serbia,
marketing consultant, youtube video editor, youtube manager,
wordpress developer, wix developer, shopify developer, shopify website build,
shopify website redesign, linkedin management, linkedin recruiter,
gohighlevel expert, go high level expert, ghl website design,
growth operator, resume writing, supply chain management, ...
```

**Combined keyword groups** — ALL keywords in a group must appear together to trigger a block:

| Group | Triggers when content contains |
|-------|-------------------------------|
| `["salesforce marketing cloud", "sfmc"]` | Both phrases simultaneously |
| `["sales", "consultant"]` | Both words simultaneously |
| `["sales", "executive"]` | Both words simultaneously |

Combined rules prevent false positives. For example, a legitimate developer job mentioning "build a sales tracking dashboard for an executive team" will not be blocked because the words appear in a technical context without both satisfying the same intent. Only jobs that clearly match both signals are filtered.

---

### Hourly Rate Filter

Jobs with a stated hourly rate range are skipped if the **maximum rate is below $50/hr**:

| Details string | Result |
|----------------|--------|
| `Hourly: $10.00 - $30.00` | ❌ Blocked (max $30 < $50) |
| `Hourly: $40.00 - $49.00` | ❌ Blocked (max $49 < $50) |
| `Hourly: $30.00 - $50.00` | ✅ Allowed (max $50 ≥ $50) |
| `Hourly` (no rate stated)  | ✅ Allowed |
| `Fixed price`              | ✅ Allowed (separate filter applies) |

---

### Embedded vs Full Stack Routing

After passing all filters, a job is classified as **Embedded** if:
- Its full content contains **at least one** embedded keyword, **AND**
- It contains **none** of the non-embedded (web/CMS platform) disqualifier keywords

**Embedded keywords** (any match qualifies):
```
stm32, esp32, nrf5340, imx8, jetson, yocto, buildroot, firmware, pcb,
altium, kicad, freertos, zephyr, mqtt, i2c, uart, zigbee, kernel,
embedded, hardware, schematic, antenna, fpga, electric, scada, plc
```

**Non-embedded disqualifiers** (any match overrides embedded classification):
```
wordpress, shopify, webflow, hubspot, wix, squarespace, drupal, joomla,
magento, woocommerce, prestashop, opencart, bubble, framer, webstudio,
salesforce, zoho, clickfunnels
```

This dual-gate approach ensures that a job like *"ESP32 + Shopify integration"* is correctly routed to Full Stack rather than Embedded, since the presence of a web platform keyword overrides the embedded signal.

Jobs that do not qualify as embedded are routed to the **Full Stack** channel by default.

---

### High-Value Project Detection

A project is flagged with 👍 when it meets either of the following thresholds:

| Job Type | Condition |
|----------|-----------|
| Hourly   | Maximum stated rate **≥ $80/hr** |
| Fixed price | Stated budget **≥ $5,000** |

The 👍 prefix is applied to the title in both the Telegram message and the Google Sheets card.

---

## Running the Bot

1. Save an Upwork search results page as an HTML file into the project directory. The filename must begin with `upwork` (e.g. `upwork.html`, `upwork_results.html`)
2. Start the bot:

```bash
python main.py
```

The bot will:
- Detect the HTML file automatically (within 5 seconds)
- Parse, filter, and route all job listings
- Send Telegram notifications to the appropriate channels
- Log all projects to Google Sheets
- Delete the processed HTML file
- Resume polling for the next file

To feed new results into the bot, simply save another `upwork*.html` file to the folder at any time.

> **Recommended browser extension:** Use [SingleFile](https://github.com/gildas-lormeau/SingleFile) to save a complete Upwork search results page as a single self-contained HTML file.
