import os
import re
import html
import dotenv
import asyncio
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from telegram import Bot

# Load environment variables
dotenv.load_dotenv()

# Telegram Bot
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID_EMBEDDED = os.getenv("TELEGRAM_CHAT_ID_EMBEDDED")
TELEGRAM_CHAT_ID_FULLSTACK = os.getenv("TELEGRAM_CHAT_ID_FULLSTACK")
bot = Bot(token=TELEGRAM_TOKEN)

EMBEDDED_KEYWORDS = [
    "stm32", "esp32", "nrf5340", "imx8", "jetson", "yocto", "buildroot",
    "firmware", "pcb", "altium", "kicad", "freertos", "zephyr", "mqtt",
    "i2c", "uart", "zigbee", "kernel", "embedded", "hardware", "schematic",
    "antenna", "fpga", "electric", "scada",
]

NON_EMBEDDED_KEYWORDS = [
    "wordpress", "shopify", "webflow", "hubspot",
    "wix", "squarespace", "drupal", "joomla", "magento",
    "woocommerce", "prestashop", "opencart",
    "bubble", "framer", "webstudio",
    "salesforce", "zoho", "clickfunnels",
]


async def send_mail(chat_id, content):
    try:
        await bot.send_message(chat_id=chat_id, text=content, parse_mode="HTML")
    except Exception as e:
        print(f"Failed to send message: {e}")


def clean_text(value: str) -> str:
    if not value:
        return ""
    return " ".join(value.split())


def should_skip_job(title: str, description: str, skills: str, location: str = "", details: str = "") -> bool:
    blocked_keywords = [
        'virtual assistant',
        'graphic design',
        'social media',
        'data entry',
        'facebook advertising',
        'lead generation specialist',
        'lead generation expert',
        'lead generation va',
        'microsoft excel',
        'google ads',
        'adobe photoshop',
        'illustration',
        'email copywriting',
        'content writing',
        'bulgaria',
        'sales manager',
        'supply chain management',
        'resume writing',
        'ukraine',
        'marketing consultant',
        'youtube video editor'
    ]
    text = f"{title} {description} {skills} {location} {details}".lower()
    return any(keyword in text for keyword in blocked_keywords)


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
    has_embedded_kw = any(kw in text for kw in EMBEDDED_KEYWORDS)
    has_non_embedded_kw = any(kw in text for kw in NON_EMBEDDED_KEYWORDS)
    return has_embedded_kw and not has_non_embedded_kw


def is_high_value_project(details: str) -> bool:
    """
    Returns True if:
    - Hourly job with max rate > $80/hr, OR
    - Fixed-price job with budget > $5,000
    """
    # Hourly with stated rate range: take the max (right-hand) value
    hourly_match = re.search(r'Hourly:\s*\$[\d.]+\s*-\s*\$([\d.]+)', details, re.IGNORECASE)
    if hourly_match:
        return float(hourly_match.group(1)) >= 80.0

    # Fixed price: find a dollar amount (no hourly range present)
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
    return url  # fallback to original if pattern not found


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
                    base_title = project_details[2]
                    location_text = project_details[5]
                    details_text = project_details[6]
                    description_text = project_details[7]
                    skills_text = project_details[8]

                    # Skip blocked keywords
                    if should_skip_job(base_title, description_text, skills_text, location_text, details_text):
                        print(f"Skipped (blocked keyword): {base_title}")
                        total_projects.append(project_url)
                        continue

                    # Skip hourly jobs with max rate < $50/hr
                    if should_skip_hourly_rate(details_text):
                        print(f"Skipped (hourly rate < $50/hr): {base_title}")
                        total_projects.append(project_url)
                        continue

                    # Route to the correct Telegram channel
                    if is_embedded_project(base_title, description_text, skills_text, location_text, details_text):
                        chat_id = TELEGRAM_CHAT_ID_EMBEDDED
                        print(f"Embedded project: {base_title}")
                    else:
                        chat_id = TELEGRAM_CHAT_ID_FULLSTACK
                        print(f"Full stack project: {base_title}")

                    await send_mail(chat_id, format_message(project_details))
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

        job_type = ""
        experience = ""
        duration = ""
        budget = ""

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
        description = clean_text(desc_el.text) if desc_el else ""
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
            project_verified       # 9
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
        """Wrap text in bold + italic HTML tags."""
        return f"<b><i>{text}</i></b>"

    return (
        f"<b>{title}</b>\n\n"
        f"Posted: {bi(posted)}\n"
        f"Details: {bi(details)}\n"
        f"Location: {bi(location)}\n"
        f"Payment: {bi(payment)}\n"
        f"Total Spent: {bi(spent)}\n\n"
        f"Description:\n{desc}\n\n"
        f"Project URL:\n<a href=\"{url}\">{url}</a>\n\n"
        f"Skills:\n{skills}"
    )


if __name__ == '__main__':
    asyncio.run(monitor_upwork())
