import os
import re
import json
import html as html_lib
import asyncio
import dotenv
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from telegram import Bot

# Load environment variables
dotenv.load_dotenv()

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_OWNER_ID = "8170918959"   # personal chat — receives job view alerts
bot = Bot(token=TELEGRAM_TOKEN)

# Per-job notification cooldown:
# Stores {job_url: datetime of last notification sent}.
# Prevents repeated alerts while the client is still within the 6-min window.
# Once 6+ minutes pass since the last notification, the job is eligible again.
NOTIFY_COOLDOWN_MINUTES = 6
_notified_cache: dict[str, datetime] = {}


def should_notify(job_url: str) -> bool:
    """Return True if no notification was sent for this job in the last 6 minutes."""
    last = _notified_cache.get(job_url)
    if last is None:
        return True
    elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 60
    return elapsed >= NOTIFY_COOLDOWN_MINUTES


def mark_notified(job_url: str) -> None:
    """Record that a notification was just sent for this job."""
    _notified_cache[job_url] = datetime.now(timezone.utc)


# ------------ TIME HELPERS ------------

def parse_time_to_minutes(time_str: str) -> int | None:
    """
    Converts a relative time string to total minutes.
        "just now"        →  0
        "10 seconds ago"  →  0
        "3 mins ago"      →  3
        "2 hours ago"     →  120
        "3 days ago"      →  4320
    Returns None if unparseable.
    """
    s = time_str.lower().strip()
    if "just now" in s:
        return 0
    if "second" in s:
        return 0
    m = re.search(r'(\d+)\s*min', s)
    if m:
        return int(m.group(1))
    m = re.search(r'(\d+)\s*hour', s)
    if m:
        return int(m.group(1)) * 60
    m = re.search(r'(\d+)\s*day', s)
    if m:
        return int(m.group(1)) * 1440
    m = re.search(r'(\d+)\s*week', s)
    if m:
        return int(m.group(1)) * 10080
    m = re.search(r'(\d+)\s*month', s)
    if m:
        return int(m.group(1)) * 43200
    return None


def format_posted_time(iso_str: str) -> str:
    """Convert ISO UTC datetime to a relative 'X ago' string."""
    try:
        posted = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        diff   = datetime.now(timezone.utc) - posted
        mins   = int(diff.total_seconds() / 60)
        if mins < 60:
            return f"{mins} minute{'s' if mins != 1 else ''} ago"
        hours = mins // 60
        if hours < 24:
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        days = hours // 24
        return f"{days} day{'s' if days != 1 else ''} ago"
    except Exception:
        return iso_str


# ------------ NUXT DATA EXTRACTOR ------------

def extract_nuxt_fields(html_content: str) -> dict:
    """
    Parses the __NUXT_DATA__ JSON blob embedded in the page.
    The blob is a flat referenced array: numeric values in objects
    are indices back into the array rather than literal values.

    Extracts:
        total_applicants  — exact proposal count (int)
        posted_at         — ISO datetime string when job was posted
        device            — OS/device used when client posted the job (e.g. "Windows", "Macintosh")
        browser           — browser used when client posted the job (e.g. "Chrome", "Edge")
    """
    result = {
        "total_applicants": None,
        "posted_at":        None,
        "device":           None,
        "browser":          None,
    }

    soup   = BeautifulSoup(html_content, "html.parser")
    script = soup.find("script", id="__NUXT_DATA__")
    if not script or not script.string:
        return result

    try:
        data = json.loads(script.string)
    except (json.JSONDecodeError, TypeError):
        return result

    def resolve(idx):
        if isinstance(idx, int) and 0 <= idx < len(data):
            return data[idx]
        return idx

    for item in data:
        if not isinstance(item, dict):
            continue

        # Exact proposal count
        if "totalApplicants" in item and result["total_applicants"] is None:
            val = resolve(item["totalApplicants"])
            if isinstance(val, int):
                result["total_applicants"] = val

        # Posted timestamp
        if "postedOn" in item and result["posted_at"] is None:
            val = resolve(item["postedOn"])
            if isinstance(val, str) and "T" in val:
                result["posted_at"] = val

        # Device + browser (present in the segmentationData customFields object)
        if "browser" in item and "device" in item and result["browser"] is None:
            b = resolve(item["browser"])
            d = resolve(item["device"])
            if isinstance(b, str) and len(b) < 60:
                result["browser"] = b
            if isinstance(d, str) and len(d) < 60:
                result["device"] = d

    return result


# ------------ FILENAME EXTRACTOR ------------

def extract_job_id_from_filename(filename: str) -> str | None:
    """
    Extracts the Upwork job ID from filenames like:
        www_upwork_com_jobs_022057470485750284141_2026-06-06T04-27-06.html
    Returns the numeric job ID string, or None if the pattern doesn't match.
    """
    name = os.path.basename(filename)
    m    = re.search(r'www_upwork_com_jobs_(\d+)_', name)
    return m.group(1) if m else None


# ------------ HTML PARSER ------------

def parse_job_page(html_content: str, filename: str = "") -> dict | None:
    """
    Parses a single Upwork job page HTML.
    Returns None if "Last viewed by client" is absent
    (client has not checked proposals yet — nothing to notify about).
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # ── Title ──────────────────────────────────────────────────────────────
    title_el  = soup.find("span", class_="text-base flex-1")
    job_title = title_el.get_text(strip=True) if title_el else ""
    if not job_title:
        page_title = soup.find("title")
        job_title  = page_title.get_text(strip=True).split(" - ")[0] if page_title else "Unknown Title"

    # ── URL ────────────────────────────────────────────────────────────────
    job_url = "URL not found"
    job_uid = extract_job_id_from_filename(filename)
    if job_uid:
        job_url = f"https://www.upwork.com/jobs/~{job_uid}"
    else:
        uid_el = soup.find(attrs={"job-uid": True})
        if uid_el:
            job_url = f"https://www.upwork.com/jobs/~{uid_el['job-uid']}"

    # ── Posted time (HTML relative text — overridden by NUXT exact time later) ──
    posted_text  = ""
    posted_line  = soup.find("div", class_="posted-on-line")
    if posted_line:
        span = posted_line.find("span")
        if span:
            posted_text = span.get_text(strip=True)

    # ── Preferred location (Worldwide / US Only / etc.) ───────────────────
    preferred_location = ""
    loc_div = soup.find("div", attrs={"data-v-e2247b69": True, "data-v-61aba1f8": True})
    if loc_div:
        p = loc_div.find("p")
        if p:
            preferred_location = p.get_text(strip=True)

    # ── Summary / description (truncated to 300 chars) ────────────────────
    summary      = ""
    desc_section = soup.find("div", attrs={"data-test": "Description"})
    if desc_section:
        p = desc_section.find("p")
        if p:
            raw     = " ".join(p.get_text(separator=" ").split())
            summary = (raw[:500] + "...") if len(raw) > 500 else raw

    # ── Job detail items (<ul class="features list-unstyled m-0">) ─────────
    job_type      = ""   # Hourly / Fixed-price
    hours_per_week = ""  # Less than 30 hrs/week
    duration      = ""   # 1 to 3 months
    exp_level     = ""   # Intermediate / Expert
    rate          = ""   # $23.00 – $55.00

    features_ul = soup.select_one("ul.features.list-unstyled.m-0")
    if features_ul:
        for li in features_ul.find_all("li"):
            icon_el = li.find(attrs={"data-cy": True})
            if not icon_el:
                continue
            cy        = icon_el.get("data-cy", "")
            strong    = li.find("strong")
            desc_div  = li.find("div", class_="description")
            strong_t  = strong.get_text(strip=True)   if strong   else ""
            desc_t    = desc_div.get_text(strip=True)  if desc_div else ""

            if cy == "clock-hourly":
                hours_per_week = strong_t
                job_type       = desc_t          # "Hourly"

            elif cy == "duration2":
                dur_span = li.find("span", class_=lambda c: c and "d-none" in c and "d-lg-inline" in c)
                duration = dur_span.get_text(strip=True) if dur_span else strong_t

            elif cy == "expertise":
                exp_level = strong_t

            elif cy == "clock-timelog":
                price_strongs = [s.get_text(strip=True) for s in li.find_all("strong")]
                if len(price_strongs) == 2:
                    rate = f"{price_strongs[0]} – {price_strongs[1]}"
                elif price_strongs:
                    rate = price_strongs[0]
                if not job_type:
                    job_type = desc_t

    # Project type (Ongoing project / One-time project)
    project_type = ""
    seg_ul = soup.select_one("ul.segmentations")
    if seg_ul:
        for li in seg_ul.find_all("li"):
            strong = li.find("strong")
            span   = li.find("span")
            if strong and span and "project type" in strong.get_text(strip=True).lower():
                project_type = span.get_text(strip=True)

    # ── Skills ─────────────────────────────────────────────────────────────
    skills_required = []
    skills_optional = []
    skills_section  = soup.find("section", attrs={"data-v-3b2c2248": True})
    if skills_section:
        for div in skills_section.select("div.span-md-12"):
            header_el = div.find("strong")
            header    = header_el.get_text(strip=True).lower() if header_el else ""
            skills_list = div.find("div", class_="skills-list")
            if not skills_list:
                continue
            badges = [
                el.get_text(strip=True)
                for el in skills_list.select("div.air3-line-clamp")
                if el.get_text(strip=True)
            ]
            if "mandatory" in header:
                skills_required = badges
            elif "nice" in header:
                skills_optional = badges

    # ── About client ───────────────────────────────────────────────────────
    client_location   = ""
    client_stats      = ""
    client_spend      = ""
    client_hires      = ""
    client_rating     = ""
    client_industry   = ""
    client_company_size = ""
    client_member     = ""
    payment_verified  = False
    phone_verified    = False

    about = soup.find("div", attrs={"data-test": "about-client-container"})
    if about:
        payment_verified = bool(about.find("div", class_=lambda c: c and "payment-verified" in c.split()))

        for strong in about.find_all("strong"):
            if "phone" in strong.get_text(strip=True).lower():
                phone_verified = True
                break

        # Rating (e.g. "5.00 of 2 reviews")
        rating_div = about.find("div", attrs={"data-testid": "buyer-rating"})
        if rating_div:
            rating_span = rating_div.find("span", class_=lambda c: c and "nowrap" in c.split())
            if rating_span:
                client_rating = rating_span.get_text(strip=True)

        loc_li = about.find("li", attrs={"data-qa": "client-location"})
        if loc_li:
            s = loc_li.find("strong")
            client_location = s.get_text(strip=True) if s else ""

        stats_li = about.find("li", attrs={"data-qa": "client-job-posting-stats"})
        if stats_li:
            s_strong = stats_li.find("strong")
            s_div    = stats_li.find("div")
            parts    = [p.get_text(strip=True) for p in [s_strong, s_div] if p]
            client_stats = " · ".join(parts)

        # Total spent + hires (optional — only present for clients with history)
        spend_strong = about.find("strong", attrs={"data-qa": "client-spend"})
        if spend_strong:
            client_spend = spend_strong.get_text(strip=True)
        hires_div = about.find("div", attrs={"data-qa": "client-hires"})
        if hires_div:
            client_hires = hires_div.get_text(strip=True)

        # Industry + company size (optional)
        industry_strong = about.find("strong", attrs={"data-qa": "client-company-profile-industry"})
        if industry_strong:
            client_industry = industry_strong.get_text(strip=True)
        size_div = about.find("div", attrs={"data-qa": "client-company-profile-size"})
        if size_div:
            client_company_size = size_div.get_text(strip=True)

        member_li = about.find("li", attrs={"data-qa": "client-contract-date"})
        if member_li:
            client_member = member_li.get_text(strip=True)

    # ── Activity section ───────────────────────────────────────────────────
    activity: dict[str, str] = {}
    last_viewed_value: str | None = None

    for item in soup.find_all("li", class_="ca-item"):
        title_span = item.find("span", class_="title")
        value_el   = item.find(class_="value")
        if not title_span or not value_el:
            continue
        label = " ".join(title_span.get_text().split()).rstrip(":")
        value = " ".join(value_el.get_text().split())
        activity[label] = value
        if "last viewed" in label.lower():
            last_viewed_value = value

    # Guard: no "Last viewed by client" → client hasn't opened proposals yet
    if last_viewed_value is None:
        return None

    # ── NUXT_DATA enrichment ───────────────────────────────────────────────
    nuxt = extract_nuxt_fields(html_content)

    # Exact proposal count overrides HTML range label
    if nuxt["total_applicants"] is not None and "Proposals" in activity:
        activity["Proposals"] = str(nuxt["total_applicants"])

    # Exact posted time overrides HTML relative text
    if nuxt["posted_at"]:
        posted_text = format_posted_time(nuxt["posted_at"])

    return {
        "title":              job_title,
        "url":                job_url,
        "posted":             posted_text,
        "preferred_location": preferred_location,
        "summary":            summary,
        "job_type":           job_type,
        "hours_per_week":     hours_per_week,
        "duration":           duration,
        "exp_level":          exp_level,
        "rate":               rate,
        "project_type":       project_type,
        "skills_required":    skills_required,
        "skills_optional":    skills_optional,
        "client_location":    client_location,
        "client_stats":       client_stats,
        "client_spend":       client_spend,
        "client_hires":       client_hires,
        "client_rating":      client_rating,
        "client_industry":    client_industry,
        "client_company_size": client_company_size,
        "client_member":      client_member,
        "payment_verified":   payment_verified,
        "phone_verified":     phone_verified,
        "device":             nuxt["device"],
        "browser":            nuxt["browser"],
        "last_viewed":        last_viewed_value,
        "last_viewed_minutes": parse_time_to_minutes(last_viewed_value),
        "activity":           activity,
    }


# ------------ TELEGRAM NOTIFICATION ------------

def e(text) -> str:
    """Escape a value for Telegram HTML parse mode."""
    return html_lib.escape(str(text))


async def send_notification(job: dict) -> None:
    lines = []

    # ── Header ────────────────────────────────────────────────────────────
    lines += [
        "👀 <b>Client just viewed proposals!</b>",
        "",
        f"<b>{e(job['title'])}</b>",
    ]

    # ── Posted · Device · Location ────────────────────────────────────────
    lines.append("")
    if job["posted"]:
        lines.append(f"<b>Posted:</b> {e(job['posted'])}")
    device_parts = [p for p in [job.get("device"), job.get("browser")] if p]
    if device_parts:
        lines.append(f"<b>Device:</b> {e(' · '.join(device_parts))}")
    if job["preferred_location"]:
        lines.append(f"<b>Location:</b> {e(job['preferred_location'])}")

    # ── Summary ───────────────────────────────────────────────────────────
    if job["summary"]:
        lines += ["", "<b>Summary</b>", e(job["summary"])]

    # ── Job details ───────────────────────────────────────────────────────
    detail_lines = []
    if job["job_type"] and job["hours_per_week"]:
        detail_lines.append(f"  • <b>Type:</b> {e(job['job_type'])}  ·  {e(job['hours_per_week'])}")
    elif job["job_type"]:
        detail_lines.append(f"  • <b>Type:</b> {e(job['job_type'])}")
    if job["rate"]:
        suffix = " /hr" if "hourly" in job["job_type"].lower() else ""
        detail_lines.append(f"  • <b>Rate:</b> {e(job['rate'])}{suffix}")
    if job["duration"]:
        detail_lines.append(f"  • <b>Duration:</b> {e(job['duration'])}")
    if job["exp_level"]:
        detail_lines.append(f"  • <b>Level:</b> {e(job['exp_level'])}")
    if job["project_type"]:
        detail_lines.append(f"  • <b>Project:</b> {e(job['project_type'])}")

    if detail_lines:
        lines += ["", "<b>Job Details</b>"] + detail_lines

    # ── Skills ────────────────────────────────────────────────────────────
    if job["skills_required"] or job["skills_optional"]:
        lines += ["", "<b>Skills</b>"]
        if job["skills_required"]:
            lines.append(f"  • <b>Required:</b> {e(', '.join(job['skills_required']))}")
        if job["skills_optional"]:
            lines.append(f"  • <b>Optional:</b> {e(', '.join(job['skills_optional']))}")

    # ── About client ──────────────────────────────────────────────────────
    client_lines = []

    verified_badges = []
    if job["payment_verified"]:
        verified_badges.append("✅ Payment verified")
    if job["phone_verified"]:
        verified_badges.append("📱 Phone verified")

    loc_line = "  • " + e(job["client_location"]) if job["client_location"] else ""
    if verified_badges:
        loc_line = (loc_line + "  ·  " if loc_line else "  • ") + "  ·  ".join(verified_badges)
    if loc_line:
        client_lines.append(loc_line)
    if job["client_rating"]:
        client_lines.append(f"  • ★ {e(job['client_rating'])}")
    if job["client_stats"]:
        client_lines.append(f"  • {e(job['client_stats'])}")
    if job["client_spend"] or job["client_hires"]:
        spend_parts = [p for p in [job["client_spend"], job["client_hires"]] if p]
        client_lines.append(f"  • {e('  ·  '.join(spend_parts))}")
    if job["client_industry"] or job["client_company_size"]:
        company_parts = [p for p in [job["client_industry"], job["client_company_size"]] if p]
        client_lines.append(f"  • {e('  ·  '.join(company_parts))}")
    if job["client_member"]:
        client_lines.append(f"  • {e(job['client_member'])}")

    if client_lines:
        lines += ["", "<b>About Client</b>"] + client_lines

    # ── Activity ──────────────────────────────────────────────────────────
    lines += ["", "<b>Activity</b>"]
    for label, value in job["activity"].items():
        lines.append(f"  • {e(label)}: <b>{e(value)}</b>")

    # ── Job link ──────────────────────────────────────────────────────────
    lines += ["", f"🔗 <a href=\"{job['url']}\">{e(job['url'])}</a>"]

    message = "\n".join(lines)

    try:
        await bot.send_message(chat_id=TELEGRAM_OWNER_ID, text=message, parse_mode="HTML")
        print(f"✅ Notification sent — last viewed {job['last_viewed']}")
    except Exception as ex:
        print(f"❌ Telegram error: {ex}")


# ------------ FILE HELPERS ------------

def get_latest_job_file(directory: str = ".") -> str | None:
    candidates = []
    for name in os.listdir(directory):
        low = name.lower()
        # Accept any .html file except upwork*.html (those belong to main.py)
        if low.endswith(".html") and not low.startswith("upwork"):
            full = os.path.join(directory, name)
            if os.path.isfile(full):
                candidates.append(full)
    return max(candidates, key=os.path.getmtime) if candidates else None


# ------------ MAIN LOOP ------------

async def monitor_jobs() -> None:
    print("Job monitor started — watching for *.html files (excluding upwork*.html)...")

    while True:
        try:
            file_path = get_latest_job_file(".")

            if not file_path:
                await asyncio.sleep(30)
                continue

            print(f"\nFound: {file_path}")

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    html_content = f.read()
            except Exception as ex:
                print(f"File read error: {ex}")
                await asyncio.sleep(30)
                continue

            job = parse_job_page(html_content, file_path)

            if job is None:
                print("⏭  No 'Last viewed by client' found — client hasn't checked proposals yet.")
            else:
                minutes = job["last_viewed_minutes"]
                print(f"🕐 Last viewed: {job['last_viewed']} ({minutes} min ago)")

                if minutes is not None and minutes < 6:
                    if should_notify(job["url"]):
                        await send_notification(job)
                        mark_notified(job["url"])
                    else:
                        print(f"⏸  Already notified for this job within {NOTIFY_COOLDOWN_MINUTES} min — skipping.")
                else:
                    print(f"⏳ {minutes} min ago — outside 6-minute window, no notification sent.")

            try:
                os.remove(file_path)
                print(f"🗑  Deleted: {file_path}")
            except Exception:
                pass

        except Exception as ex:
            print(f"Error: {ex}")

        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(monitor_jobs())
