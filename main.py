import os
import re
import html
import dotenv
import asyncio
import gspread
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from telegram import Bot
from oauth2client.service_account import ServiceAccountCredentials

# Load environment variables
dotenv.load_dotenv()

# Telegram Bot
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID_EMBEDDED  = os.getenv("TELEGRAM_CHAT_ID_EMBEDDED")
TELEGRAM_CHAT_ID_CAD3D    = "-4991404775"
TELEGRAM_CHAT_ID_FULLSTACK = os.getenv("TELEGRAM_CHAT_ID_FULLSTACK")
TELEGRAM_OWNER_ID = "8170918959"  # personal chat — receives blocked project notices
bot = Bot(token=TELEGRAM_TOKEN)

# ------------ GOOGLE SHEETS ------------

SHEET_URL = "https://docs.google.com/spreadsheets/d/1F28lBEdy4rknnMO70b8SQbrYJTeXcainIFB_IfRvlpQ/edit?usp=sharing"
SHEET_HEADERS = ["Posted", "Project", "URL", "Description"]


def init_google_sheets():
    try:
        creds_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS_PATH")
        if not creds_path:
            print("Warning: GOOGLE_SHEETS_CREDENTIALS_PATH not set — Google Sheets disabled.")
            return None, None

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_url(SHEET_URL)

        ws_fullstack   = spreadsheet.get_worksheet(0)  # Sheet 1 — Full Stack
        ws_embedded    = spreadsheet.get_worksheet(1)  # Sheet 2 — Embedded
        ws_cad3d       = spreadsheet.get_worksheet(2)  # Sheet 3 — CAD & 3D
        ws_blocked     = spreadsheet.get_worksheet(3)  # Sheet 4 — Blocked
        ws_not_matched = spreadsheet.get_worksheet(4)  # Sheet 5 — Not Matched

        # Add headers to each sheet if they are empty
        for ws in [ws_fullstack, ws_embedded, ws_blocked, ws_not_matched, ws_cad3d]:
            if not ws.row_values(1):
                ws.insert_row(SHEET_HEADERS, index=1)

        print("Google Sheets connected.")
        return ws_fullstack, ws_embedded, ws_blocked, ws_not_matched, ws_cad3d

    except Exception as e:
        print(f"Google Sheets init error: {e}")
        return None, None, None, None, None


ws_fullstack, ws_embedded, ws_blocked, ws_not_matched, ws_cad3d = init_google_sheets()


def parse_details_for_sheet(details: str):
    """
    Splits the details string into (price, rest).

    Hourly with range  → ("$30/hr - $60/hr", "Intermediate | Est. time: ...")
    Hourly single rate → ("$60/hr",           "Intermediate | Est. time: ...")
    Hourly no rate     → ("Hourly",            "Intermediate | Est. time: ...")
    Fixed with budget  → ("$3000",             "Intermediate")
    Fixed no budget    → ("Fixed Price",        "Intermediate")
    """
    def fmt(amount_str: str) -> str:
        val = float(amount_str.replace(',', ''))
        return str(int(val)) if val == int(val) else str(val)

    # Hourly with rate range: "Hourly: $30.00 - $60.00"
    m = re.search(r'Hourly:\s*\$([\d,.]+)\s*-\s*\$([\d,.]+)', details, re.IGNORECASE)
    if m:
        price = f"${fmt(m.group(1))}/hr - ${fmt(m.group(2))}/hr"
        rest = re.sub(r'Hourly\b\s*\|\s*', '', details, count=1, flags=re.IGNORECASE)
        rest = re.sub(r'Hourly:\s*\$[\d,.]+\s*-\s*\$[\d,.]+\s*(\|\s*)?', '', rest, flags=re.IGNORECASE)
        return price, rest.strip(' |').strip()

    # Hourly with single rate: "Hourly: $60.00"
    m = re.search(r'Hourly:\s*\$([\d,.]+)', details, re.IGNORECASE)
    if m:
        price = f"${fmt(m.group(1))}/hr"
        rest = re.sub(r'Hourly\b\s*\|\s*', '', details, count=1, flags=re.IGNORECASE)
        rest = re.sub(r'Hourly:\s*\$[\d,.]+\s*(\|\s*)?', '', rest, flags=re.IGNORECASE)
        return price, rest.strip(' |').strip()

    # Fixed price with stated budget: "Est. budget: $3,000.00"
    m = re.search(r'Est\.?\s*budget:\s*\$([\d,.]+)', details, re.IGNORECASE)
    if m:
        price = f"${fmt(m.group(1))}"
        rest = re.sub(r'Fixed[- ]?price\s*\|\s*', '', details, count=1, flags=re.IGNORECASE)
        rest = re.sub(r'Est\.?\s*budget:\s*\$[\d,.]+\s*(\|\s*)?', '', rest, flags=re.IGNORECASE)
        return price, rest.strip(' |').strip()

    # Hourly without rate
    if re.search(r'\bhourly\b', details, re.IGNORECASE):
        rest = re.sub(r'Hourly\s*\|\s*', '', details, count=1, flags=re.IGNORECASE)
        return "Hourly", rest.strip(' |').strip()

    # Fixed price without stated budget
    if re.search(r'fixed', details, re.IGNORECASE):
        rest = re.sub(r'Fixed[- ]?price\s*\|\s*', '', details, count=1, flags=re.IGNORECASE)
        return "Fixed Price", rest.strip(' |').strip()

    return "", details


def format_sheet_card(d, blocked_reason: str = "") -> str:
    """
    Formats a project as a single-cell Upwork-style card:

    🕐 05/26 07:01
    👍 Project Title

    ✅ Verified  ·  💰 $35 spent  ·  📍 United States

    $30/hr - $60/hr · Expert · Est. time: 1 to 3 months, Less than 30 hrs/week

    Description text here...

    Flask, Python, Raspberry Pi, API Integration
    """
    title    = d[2] or ""
    details  = d[6] or ""
    desc     = d[7] or ""
    skills   = d[8] or ""
    payment  = (d[9] or "").strip()
    spent    = d[4] or ""
    location = d[5] or ""
    posted   = d[1] or ""

    if is_high_value_project(details):
        title = f"👍 {title}"

    # Payment symbol
    p_lower = payment.lower()
    if p_lower == "verified":
        payment_str = "✅ Verified"
    elif p_lower == "unverified":
        payment_str = "❌ Unverified"
    else:
        payment_str = f"❓ {payment}" if payment else ""

    # Info line: payment · spent · location
    info_line = "  ·  ".join(filter(None, [
        payment_str,
        f"💰 {spent}" if spent else "",
        f"📍 {location}" if location else "",
    ]))

    # Details line: price · experience · duration
    price, rest = parse_details_for_sheet(details)
    rest_clean = rest.replace(" | ", " · ")
    details_line = " · ".join(filter(None, [price, rest_clean]))

    parts = [
        title,
        "",
        info_line,
        "",
        details_line,
    ]

    if desc:
        short_desc = desc[:250] + "..." if len(desc) > 250 else desc
        parts += ["", short_desc]

    if skills and skills != "No skills":
        parts += ["", skills.replace(", ", " | ")]

    if blocked_reason:
        parts += ["", f"🚫 Blocked: {blocked_reason}"]

    return "\n".join(parts)


def write_to_sheet(worksheet, d, blocked_reason: str = ""):
    """Append one project as a card row (Posted | Project | URL) to the given worksheet,
    then bold the title (first line) of the Project cell using textFormatRuns."""
    if worksheet is None:
        return

    try:
        card = format_sheet_card(d, blocked_reason)
        worksheet.append_row([d[1], card, d[3], d[7]])

        # Row index of the row we just appended (0-based for the API)
        last_row = len(worksheet.get_all_values())
        title_len = len(card.split('\n')[0])

        # Apply bold to the title portion only (column B = index 1)
        requests = [{
            "updateCells": {
                "rows": [{
                    "values": [{
                        "userEnteredValue": {"stringValue": card},
                        "textFormatRuns": [
                            {"startIndex": 0,          "format": {"bold": True}},
                            {"startIndex": title_len,  "format": {"bold": False}},
                        ]
                    }]
                }],
                "fields": "userEnteredValue,textFormatRuns",
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": last_row - 1,
                    "endRowIndex":   last_row,
                    "startColumnIndex": 1,   # column B — Project
                    "endColumnIndex":   2,
                }
            }
        }]

        worksheet.spreadsheet.batch_update({"requests": requests})
        print(f"Sheet updated: {d[2]}")
    except Exception as e:
        print(f"Sheet write error: {e}")


# ------------ KEYWORDS ------------

EMBEDDED_KEYWORDS = [
    # Microcontrollers & SoCs
    "stm32", "esp32", "nrf5340", "imx8", "jetson",
    # Build systems & RTOS
    "yocto", "buildroot", "freertos", "zephyr", "kernel",
    # Hardware & protocols
    "firmware", "pcb", "altium", "kicad", "schematic", "fpga", "antenna",
    "hardware", "embedded", "electric", "plc", "scada",
    "mqtt", "i2c", "uart", "zigbee",
]

CAD_3D_KEYWORDS = [
    # 3D Modelling & Pipeline
    "3d modeling", "3d model", "3d design", "3d pipeline", "3d printing",
    "3d rendering", "3d artist", "3d environment", "3d world",
    "blender", "3ds max", "cinema 4d", "c4d", "zbrush", "houdini",
    "substance painter", "substance designer",
    "visual effects", "rigging", "sculpting", "texturing",
    "shader", "hlsl", "glsl", "shader graph",
    # Unity
    "unity", "unity3d", "unity 3d", "unity development", "unity game",
    # Unreal Engine
    "unreal engine", "ue4", "ue5", "unreal blueprint",
    "nanite",
    # Game Development
    "godot", "gamemaker", "cocos2d", 
    "game development", "game dev", "game design", "game engine",
    "gameplay", "game mechanic", "game logic", "level design",
    "environment design", "environment artist",
    "character design", "character animation",
    "multiplayer game", "mirror networking",
    "mobile game", "pc game", "console game",
    "virtual reality", "augmented reality", "mixed reality",
    "metaverse",
    # Roblox
    "roblox", "roblox studio",
    # FiveM / GTA modding
    "fivem", "five m", "gta", "qbcore",
    # CAD & Engineering Design
    "solidworks", "fusion 360", "autocad", 
    "revit", "sketchup", "archicad",
    "industrial design", "mechanical design", "mechanical engineering",
    "parametric design", "generative design",
    "ansys", '3d animation', 'after effects'
]

NON_EMBEDDED_KEYWORDS = [
    "wordpress", "shopify", "webflow", "hubspot",
    "wix", "squarespace", "drupal", "joomla", "magento",
    "woocommerce", "prestashop", "opencart",
    "bubble", "framer", "webstudio",
    "salesforce", "zoho", "clickfunnels",
]

NON_CAD3D_KEYWORDS = [
    # Web design & branding
    "web design", "website design", "website redesign", "web redesign",
    "logo design", "brand design", "graphic design",
    "ui design", "ux design", "ui/ux",
    # Marketing & advertising
    "google ads", "facebook ads", "instagram ads",
    "digital marketing",
    "linkedin outreach", "email marketing",
    "social media",
    # CMS & web platforms
    "shopify", "wordpress", "hubspot", "squarespace",
    "wix", "webflow", "weebly",
    # Design tools used in non-3D context
    "photoshop editor", "sketch",
]

FULLSTACK_KEYWORDS = [
    # Frontend frameworks & libraries
    "react", "next.js", "nextjs", "angular", "svelte", "nuxt",
    "tailwind", "bootstrap", "jquery",
    # Backend frameworks
    "node.js", "nodejs", "express", "fastapi", "django", "flask",
    "laravel", "spring boot", "asp.net", ".net core", "dotnet",
    "ruby on rails",
    # Programming languages
    "javascript", "typescript", "python", "java", "php", 
    "golang", "c#", "kotlin", "swift",
    # Databases
    "postgresql", "mysql", "mongodb", "redis", "sqlite",
    "firebase", "supabase", "dynamodb", 
    # Cloud & DevOps
    "azure", "google cloud", "docker", "kubernetes", "terraform",
    "github actions", "ci/cd",
    # AI & Machine Learning
    "flutter", "react native", "ios development", "android development",
    # API & Architecture
    "rest api", "graphql", "websocket", "microservices", "api integration",
    # General web & SaaS
    "full stack", "fullstack", "full-stack",
    "web application", "web development", "web app", "saas",
]


# ------------ HELPERS ------------

async def send_mail(chat_id, content):
    try:
        await bot.send_message(chat_id=chat_id, text=content, parse_mode="HTML")
    except Exception as e:
        print(f"Failed to send message: {e}")


def clean_text(value: str) -> str:
    if not value:
        return ""
    return " ".join(value.split())


# Keywords checked against project TITLE only — any match blocks the job regardless of content
BLOCKED_TITLE_KEYWORDS = [
    'wordpress',
    'webflow',
    'shopify',
    'wix',
    'photoshop',
    'figma',
    'hubspot',
    'translator',
    'logo design',
    'Writer',
]


def should_skip_job(title: str, description: str, skills: str, location: str = "", details: str = "") -> str | None:
    """Returns the matched blocked keyword/combination, or None if the job should not be skipped."""

    # Title-only check — runs before full-content checks
    title_lower = title.lower()
    for keyword in BLOCKED_TITLE_KEYWORDS:
        if keyword in title_lower:
            return f"{keyword} [title]"

    # Single keywords — any one match blocks the job
    blocked_keywords = [
        'virtual assistant',
        'facebook advertising',
        'lead generation specialist',
        'lead generation expert',
        'lead generation va',
        'illustration',
        'email copywriting',
        'content writing',
        'bulgaria',
        'sales manager',
        'supply chain management',
        'resume writing',
        'ukraine',
        'Legal SEO',
        'marketing consultant',
        'youtube video editor',
        'youtube manager',
        'wordpress developer',
        'wordpress plugin developer',
        'wix developer',
        'shopify developer',
        'shopify plugin developer',
        'shopify website build',
        'shopify website redesign',
        'linkedin management',
        'linkedin recruiter',
        'poland',
        'turkey',
        'serbia',
        'gohighlevel expert',
        'go high level expert',
        'ghl website design',
        'growth operator',
        'digital marketing specialist',
        'social media manager',
        'social media management',
        'email marketing specialist',
        'voice actor',
        'graphic designer',
        'france',
    ]

    # Combined keyword groups — ALL keywords in a group must appear together to block
    blocked_combinations = [
        ["salesforce marketing cloud", "sfmc"],
        ["sales", "consultant"],
        ["sales", "executive"],
        ["social media marketing", "communication"],
    ]

    text = f"{title} {description} {skills} {location} {details}".lower()

    for keyword in blocked_keywords:
        if keyword in text:
            return keyword

    for group in blocked_combinations:
        if all(kw in text for kw in group):
            return " + ".join(group)

    return None


def should_skip_hourly_rate(details: str) -> bool:
    """
    Returns True if the job is hourly with a stated max rate below $50/hr.
    Jobs with no rate stated (e.g. "Hourly | Expert | ...") are allowed through.
    """
    match = re.search(r'Hourly:\s*\$[\d.]+\s*-\s*\$([\d.]+)', details, re.IGNORECASE)
    if match:
        max_rate = float(match.group(1))
        return max_rate < 50.0
    return False


def is_embedded_project(title: str, description: str, skills: str, location: str = "", details: str = "") -> bool:
    text = f"{title} {description} {skills} {location} {details}".lower()
    has_embedded_kw     = any(kw in text for kw in EMBEDDED_KEYWORDS)
    has_non_embedded_kw = any(kw in text for kw in NON_EMBEDDED_KEYWORDS)
    return has_embedded_kw and not has_non_embedded_kw


def is_cad3d_project(title: str, description: str, skills: str, location: str = "", details: str = "") -> bool:
    text = f"{title} {description} {skills} {location} {details}".lower()
    has_cad3d_kw     = any(kw in text for kw in CAD_3D_KEYWORDS)
    has_non_cad3d_kw = any(kw in text for kw in NON_CAD3D_KEYWORDS)
    return has_cad3d_kw and not has_non_cad3d_kw


def is_fullstack_project(title: str, description: str, skills: str, location: str = "", details: str = "") -> bool:
    text = f"{title} {description} {skills} {location} {details}".lower()
    return any(kw in text for kw in FULLSTACK_KEYWORDS)


def is_high_value_project(details: str) -> bool:
    """
    Returns True if:
    - Hourly job with max rate >= $80/hr, OR
    - Fixed-price job with budget >= $5,000
    """
    hourly_match = re.search(r'Hourly:\s*\$[\d.]+\s*-\s*\$([\d.]+)', details, re.IGNORECASE)
    if hourly_match:
        return float(hourly_match.group(1)) >= 80.0

    if 'hourly' not in details.lower():
        fixed_match = re.search(r'\$([\d,]+(?:\.\d+)?)', details)
        if fixed_match:
            try:
                amount = float(fixed_match.group(1).replace(',', ''))
                return amount >= 5000
            except ValueError:
                pass

    return False


def get_latest_upwork_file(directory=".") -> str | None:
    candidates = []
    for name in os.listdir(directory):
        if name.lower().startswith("upwork") and name.lower().endswith(".html"):
            full = os.path.join(directory, name)
            if os.path.isfile(full):
                candidates.append(full)

    if not candidates:
        return None

    return max(candidates, key=os.path.getmtime)


def clean_job_url(url: str) -> str:
    """
    Shortens a full Upwork job URL to just the job ID form.
    e.g. https://www.upwork.com/jobs/Some-Long-Title_~022058890701204188187/?referrer=...
      →  https://www.upwork.com/jobs/~022058890701204188187
    """
    match = re.search(r'_~(\d+)', url)
    if match:
        return f"https://www.upwork.com/jobs/~{match.group(1)}"
    return url


# ------------ BLOCKED MESSAGE FORMAT ------------

def format_blocked_message(d, reason: str) -> str:
    title    = html.escape(d[2] or "")
    details  = html.escape(d[6] or "")
    location = html.escape(d[5] or "")
    reason   = html.escape(reason)
    url      = d[3] or ""

    def bi(text: str) -> str:
        return f"<b><i>{text}</i></b>"

    return (
        f"🚫 <b>{title}</b>\n\n"
        f"Details: {bi(details)}\n"
        f"Location: {bi(location)}\n"
        f"Reason: {bi(reason)}\n\n"
        f"<a href=\"{url}\">{url}</a>"
    )


# ------------ MAIN LOOP ------------

async def monitor_upwork():
    total_projects = []

    while True:
        try:
            file_path = get_latest_upwork_file(".")
            if not file_path:
                print("No upwork*.html file found. Waiting...")
                await asyncio.sleep(5)
                continue

            print(f"Using HTML file: {file_path}")

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    html_content = f.read()
            except Exception:
                print("File read error")
                await asyncio.sleep(5)
                continue

            soup = BeautifulSoup(html_content, "html.parser")
            div_elements = soup.find_all(attrs={"data-ev-label": "search_results_impression"})
            div_elements.reverse()

            for div in div_elements:
                project_details = parse_project(div)
                if not project_details:
                    continue

                project_url = project_details[3]

                if project_url not in total_projects:
                    base_title       = project_details[2]
                    location_text    = project_details[5]
                    details_text     = project_details[6]
                    description_text = project_details[7]
                    skills_text      = project_details[8]

                    # Skip blocked keywords
                    matched_keyword = should_skip_job(base_title, description_text, skills_text, location_text, details_text)
                    if matched_keyword:
                        print(f"Skipped ({matched_keyword}): {base_title}")
                        await send_mail(TELEGRAM_OWNER_ID, format_blocked_message(project_details, f'"{matched_keyword}"'))
                        write_to_sheet(ws_blocked, project_details, blocked_reason=f'"{matched_keyword}"')
                        total_projects.append(project_url)
                        continue

                    # Skip hourly jobs with max rate < $50/hr
                    if should_skip_hourly_rate(details_text):
                        print(f"Skipped (hourly rate < $50/hr): {base_title}")
                        await send_mail(TELEGRAM_OWNER_ID, format_blocked_message(project_details, "Hourly rate < $50/hr"))
                        write_to_sheet(ws_blocked, project_details, blocked_reason="Hourly rate < $50/hr")
                        total_projects.append(project_url)
                        continue

                    # Route to the correct Telegram channel and Google Sheet
                    if is_embedded_project(base_title, description_text, skills_text, location_text, details_text):
                        print(f"Embedded project: {base_title}")
                        await send_mail(TELEGRAM_CHAT_ID_EMBEDDED, format_message(project_details))
                        write_to_sheet(ws_embedded, project_details)

                    elif is_cad3d_project(base_title, description_text, skills_text, location_text, details_text):
                        print(f"CAD & 3D project: {base_title}")
                        await send_mail(TELEGRAM_CHAT_ID_CAD3D, format_message(project_details))
                        write_to_sheet(ws_cad3d, project_details)

                    elif is_fullstack_project(base_title, description_text, skills_text, location_text, details_text):
                        print(f"Full stack project: {base_title}")
                        await send_mail(TELEGRAM_CHAT_ID_FULLSTACK, format_message(project_details))
                        write_to_sheet(ws_fullstack, project_details)

                    else:
                        print(f"Not matched (sheet only): {base_title}")
                        write_to_sheet(ws_not_matched, project_details)

                    await asyncio.sleep(1)

                total_projects.append(project_url)

            if len(total_projects) > 200:
                total_projects = total_projects[-200:]

            try:
                os.remove(file_path)
                print(f"Deleted processed file: {file_path}")
            except Exception:
                pass

            await asyncio.sleep(1)

        except Exception as e:
            print("Error:", e)
            await asyncio.sleep(30)


# ------------ PARSING ------------

def parse_project(div):
    try:
        jst = timezone(timedelta(hours=10))
        posted_time = datetime.now(jst).strftime("%m/%d %H:%M")

        title_link = (
            div.find("a", attrs={"data-test": "job-tile-title-link UpLink"})
            or div.find("a", class_="air3-link")
        )

        if not title_link:
            return None

        project_title = clean_text(title_link.get_text(separator=" ", strip=True))

        href = title_link.get("href", "")
        raw_url = href if href.startswith("http") else "https://www.upwork.com" + href
        project_url = clean_job_url(raw_url)

        header = div.find(attrs={"data-test": "JobTileHeader"})
        if header and header.find("small"):
            small = header.find("small")
            spans = small.find_all("span")
            if len(spans) > 1:
                project_posted = clean_text(spans[1].text)
            else:
                project_posted = clean_text(small.text)
        else:
            project_posted = "None"

        payment_el = div.find(attrs={"data-test": "payment-verified"})
        if payment_el:
            badge = payment_el.find(attrs={"data-test": "UpCVerifiedBadge"})
            sr = badge.find("span", class_="sr-only") if badge else None
            raw = sr.text.strip() if sr else ""
            project_verified = raw.capitalize() if raw else "Unknown"
        else:
            project_verified = "Unknown"

        project_verified = clean_text(project_verified)

        spent_el = div.find(attrs={"data-test": "total-spent"})
        if spent_el:
            strong = spent_el.find("strong")
            span = spent_el.find("span")
            if strong and span:
                project_spent = clean_text(f"{strong.text} {span.text}")
            else:
                project_spent = clean_text(spent_el.text)
        else:
            project_spent = "No spent"

        loc_el = div.find(attrs={"data-test": "location"})
        if loc_el:
            sr = loc_el.find("span", class_="sr-only")
            if sr:
                sr.extract()
            project_location = clean_text(loc_el.text)
        else:
            project_location = "None"

        job_info_ul = div.find("ul", attrs={"data-test": "JobInfo"})

        job_type   = ""
        experience = ""
        duration   = ""
        budget     = ""

        if job_info_ul:
            type_el = job_info_ul.find("li", attrs={"data-test": "job-type-label"})
            if type_el:
                job_type = clean_text(type_el.text)

            exp_el = job_info_ul.find("li", attrs={"data-test": "experience-level"})
            if exp_el:
                experience = clean_text(exp_el.text)

            dur_el = job_info_ul.find("li", attrs={"data-test": "duration-label"})
            if dur_el:
                duration = clean_text(dur_el.text)

            fixed_el = job_info_ul.find("li", attrs={"data-test": "is-fixed-price"})
            if fixed_el:
                budget = clean_text(fixed_el.text)

            hourly_el = job_info_ul.find("li", attrs={"data-test": "is-hourly"})
            if hourly_el and not budget:
                budget = clean_text(hourly_el.text)

        details_parts = [x for x in [job_type, experience, budget, duration] if x]
        project_details_info = " | ".join(details_parts)

        desc_el = div.find("div", attrs={"data-test": "UpCLineClamp JobDescription"})
        if desc_el:
            # Preserve line breaks: convert <br> to \n, add \n after block elements
            for tag in desc_el.find_all("br"):
                tag.replace_with("\n")
            for tag in desc_el.find_all(["p", "li", "div"]):
                tag.append("\n")
            raw = desc_el.get_text()
            # Clean each line individually, then collapse 3+ consecutive blank lines to 2
            lines = [" ".join(line.split()) for line in raw.splitlines()]
            description = re.sub(r'\n{3,}', '\n\n', "\n".join(lines)).strip()
        else:
            description = ""
        if len(description) > 3000:
            description = description[:3000]

        skills_el = div.find(attrs={"data-test": "TokenClamp JobAttrs"})
        if skills_el:
            skills = [
                clean_text(span.text)
                for span in skills_el.find_all(attrs={"data-test": "token"})
            ]
            skills_text = ", ".join(skills)
        else:
            skills_text = "No skills"

        return [
            project_posted,        # 0
            posted_time,           # 1
            project_title,         # 2
            project_url,           # 3
            project_spent,         # 4
            project_location,      # 5
            project_details_info,  # 6
            description,           # 7
            skills_text,           # 8
            project_verified,      # 9
        ]

    except Exception as e:
        print("Parse error:", e)
        return None


# ------------ TELEGRAM MESSAGE FORMAT ------------

def format_message(d):
    title    = html.escape(d[2] or "")
    details  = html.escape(d[6] or "")
    location = html.escape(d[5] or "")
    posted   = html.escape(d[1] or "")
    spent    = html.escape(d[4] or "")
    payment  = html.escape(d[9] or "None")
    desc     = html.escape(d[7] or "")
    skills   = html.escape(d[8] or "")
    url      = d[3] or ""  # raw URL, not escaped — used in href

    if is_high_value_project(d[6] or ""):
        title = f"👍 {title}"

    def bi(text: str) -> str:
        return f"<b><i>{text}</i></b>"

    return (
        f"<b>{title}</b>\n\n"
        f"Posted: {bi(posted)}\n"
        f"Details: {bi(details)}\n"
        f"Location: {bi(location)}\n"
        f"Payment: {bi(payment)}\n"
        f"Total Spent: {bi(spent)}\n\n"
        f"Project URL:\n<a href=\"{url}\">{url}</a>\n\n"
        f"Skills:\n{skills}"
    )


if __name__ == '__main__':
    asyncio.run(monitor_upwork())
