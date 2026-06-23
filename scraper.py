import asyncio
import base64
import os
import random
import re
import urllib.parse
from urllib.parse import urlparse, parse_qs
import pandas as pd
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# Configuration
DEFAULT_PROXY = {
    "server": "http://185.66.15.49:8000",
    "username": "Sh6VaG",
    "password": "KXZm4M"
}

MAX_DETAILS = None  # Set to a number to limit detail card scraping during tests, or None for all

def parse_proxy_string(proxy_str):
    """Parses a proxy string into Playwright proxy format."""
    proxy_str = proxy_str.strip()
    if not proxy_str or proxy_str.startswith("#"):
        return None
    # Support http://username:password@ip:port
    try:
        if "@" in proxy_str:
            cleaned = proxy_str
            if "://" in cleaned:
                cleaned = cleaned.split("://")[1]
            credentials, server = cleaned.split("@")
            username, password = credentials.split(":")
            return {
                "server": f"http://{server}",
                "username": username,
                "password": password
            }
        # Support ip:port:username:password
        parts = proxy_str.split(":")
        if len(parts) == 4:
            ip, port, username, password = parts
            return {
                "server": f"http://{ip}:{port}",
                "username": username,
                "password": password
            }
        # Support standard http://ip:port or ip:port
        server = proxy_str
        if not server.startswith("http://") and not server.startswith("https://"):
            server = f"http://{server}"
        return {"server": server}
    except Exception as e:
        print(f"[Warning] Failed to parse proxy string '{proxy_str}': {e}")
        return None

def load_proxies(filename):
    """Loads list of proxies from filename, returns list of dicts."""
    if not os.path.exists(filename):
        return []
    proxies = []
    with open(filename, 'r', encoding='utf-8') as f:
        for line in f:
            parsed = parse_proxy_string(line)
            if parsed:
                proxies.append(parsed)
    return proxies

PROXIES = load_proxies("proxies.txt")
if not PROXIES:
    PROXIES = [DEFAULT_PROXY]
    print(f"[Init] No proxies loaded from proxies.txt. Using default proxy.")
else:
    print(f"[Init] Loaded {len(PROXIES)} proxies from proxies.txt.")

def load_list_from_file(filename, defaults):
    """Loads a list of strings from a file, creating it with defaults if not found."""
    if not os.path.exists(filename):
        with open(filename, 'w', encoding='utf-8') as f:
            for item in defaults:
                f.write(f"{item}\n")
        print(f"[Init] Created {filename} with default values.")
        return defaults
    
    items = []
    with open(filename, 'r', encoding='utf-8') as f:
        for line in f:
            line_str = line.strip()
            # Ignore comments and empty lines
            if line_str and not line_str.startswith("#"):
                items.append(line_str)
    return items

CITIES = load_list_from_file("cities.txt", ["kaluga"])
KEYWORDS = load_list_from_file("keywords.txt", ["магазин медтехники"])

def decode_2gis_url(url):
    """Attempts to decode the destination URL from a 2GIS redirect URL."""
    try:
        # Check path parameters for base64 encoded URL
        parts = url.split('/')
        for part in parts:
            if len(part) > 10 and not any(c in part for c in ['?', '&', '=', '.', ':']):
                try:
                    padded = part + '=' * (4 - len(part) % 4)
                    decoded = base64.b64decode(padded).decode('utf-8', errors='ignore')
                    cleaned = re.search(r'(https?://[^\s\x00-\x1F\x7F]+)', decoded)
                    if cleaned:
                        return cleaned.group(1)
                except Exception:
                    pass
                    
        # Check query parameters
        parsed = urlparse(url)
        if parsed.query.startswith("http://") or parsed.query.startswith("https://"):
            return parsed.query
        qs = parse_qs(parsed.query)
        for key in ['url', 'to', 'link']:
            if key in qs:
                return qs[key][0]
    except Exception:
        pass
    return None

def extract_website_from_links(links):
    """Filters and decodes the official website from extracted external links."""
    social_domains = [
        "vk.com", "ok.ru", "t.me", "instagram.com", "facebook.com", 
        "youtube.com", "twitter.com", "wa.me", "viber.click", "whatsapp.com", "viber.ru"
    ]
    
    for text, href in links:
        decoded = None
        if "link.2gis.ru" in href or "redirect.2gis.com" in href:
            decoded = decode_2gis_url(href)
        elif href.startswith("http://") or href.startswith("https://"):
            decoded = href
            
        if decoded:
            try:
                parsed_decoded = urlparse(decoded)
                domain = parsed_decoded.netloc.lower()
                if domain.startswith("www."):
                    domain = domain[4:]
                
                # Check if it's social media or 2gis itself
                if any(social in domain for social in social_domains):
                    continue
                if "2gis" in domain or "otello" in domain:
                    continue
                    
                return decoded
            except Exception:
                pass
    return None

async def bypass_warning(page, correct_url):
    """Detects and bypasses the 2GIS browser compatibility warning screen."""
    btn = await page.query_selector("#acceptRiskButton")
    if btn:
        print("[Info] Outdated browser warning screen detected. Bypassing...")
        try:
            # Click the warning bypass button and wait for it to navigate
            await asyncio.gather(
                page.wait_for_load_state("load", timeout=20000),
                btn.click()
            )
        except Exception as bypass_err:
            print(f"[Warning] Timeout/error waiting for warning bypass navigation: {bypass_err}. Proceeding anyway...")
        
        # Navigate back to the correctly encoded search URL
        print(f"[Info] Navigating back to the correctly encoded URL: {correct_url}")
        try:
            await page.goto(correct_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(5000)
        except Exception as goto_err:
            print(f"[Warning] Navigation to search URL timed out: {goto_err}. Trying to proceed...")
            await page.wait_for_timeout(5000)
    else:
        print("[Info] No warning screen detected.")

async def extract_firms(page):
    """Extracts business names and relative links from search results cards, preserving order."""
    links = await page.eval_on_selector_all(
        "div._1kf6gff a[href*='/firm/']",
        "elements => elements.map(el => ({ href: el.getAttribute('href') || '', name: el.innerText.trim() }))"
    )
    
    firms = []
    seen_on_page = set()
    for l in links:
        href = l['href']
        name = l['name']
        if name and "/firm/" in href:
            clean_href = href.split('?')[0]
            if clean_href not in seen_on_page:
                seen_on_page.add(clean_href)
                firms.append((name, href))
    return firms

async def scrape_query(page, city, keyword):
    """Performs sequential pagination scraping for a single city + keyword query."""
    encoded_query = urllib.parse.quote(keyword)
    base_url = f"https://2gis.ru/{city}/search/{encoded_query}"
    
    print(f"\n[Scraper] Starting crawl for city='{city}' | keyword='{keyword}'")
    print(f"[Scraper] Base URL: {base_url}")
    
    # 1. Load the first page
    try:
        print("[Page 1] Navigating to first page...")
        await page.goto(base_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3000)
        await bypass_warning(page, base_url)
    except Exception as goto_err:
        print(f"[Error] Failed to navigate to first page: {goto_err}")
        return []
        
    # Parse total places from the "Места [число]" header to set hard page limits
    total_places = None
    try:
        total_places = await page.evaluate(r"""() => {
            const elements = Array.from(document.querySelectorAll('*'));
            let bestMatch = null;
            for (const el of elements) {
                const text = el.innerText || '';
                const match = text.match(/Места\s*(\d+)/);
                if (match) {
                    if (!bestMatch || el.querySelectorAll('*').length < bestMatch.querySelectorAll('*').length) {
                        bestMatch = el;
                    }
                }
            }
            if (bestMatch) {
                const match = bestMatch.innerText.match(/Места\s*(\d+)/);
                if (match) return parseInt(match[1]);
            }
            return null;
        }""")
    except Exception as parse_err:
        print(f"[Warning] Failed to parse total places count: {parse_err}")

    if total_places:
        import math
        max_pages = math.ceil(total_places / 12) + 1
        print(f"[Scraper] Total matching places found: {total_places}. Page limit calculated: {max_pages}")
    else:
        max_pages = 100
        print(f"[Scraper] Could not find total places count. Using safety page limit: {max_pages}")

    all_collected_firms = []
    seen_hrefs = set()
    page_num = 1
    
    while page_num <= max_pages:
        print(f"\n--- PAGE {page_num} ---")
        print(f"Current URL: {page.url}")
        
        try:
            # Scroll container to load lazy cards
            print(f"[Page {page_num}] Scrolling listing container...")
            await page.evaluate("""async () => {
                const container = document.querySelector('div._1667t0u') || document.querySelector('div._15gu4wr') || document.querySelector('div._8hh56jx');
                if (container) {
                    for (let i = 0; i < 3; i++) {
                        container.scrollTop = container.scrollHeight;
                        await new Promise(resolve => setTimeout(resolve, 600));
                    }
                }
            }""")
            
            # Check for the "No exact matches" text in 2GIS (marks the end of results)
            no_match = await page.query_selector("text='Точных совпадений нет'")
            if no_match:
                print(f"[Page {page_num}] 'No exact matches' message detected. Stopping crawl.")
                break
                
            # Extract listings on current page
            firms = await extract_firms(page)
            print(f"[Page {page_num}] Found {len(firms)} listings.")
            
            if len(firms) == 0:
                print(f"[Page {page_num}] No listings found on this page. Stopping crawl.")
                break
                
            # Loop detection: check if all listings on this page have already been scraped
            page_hrefs = {href.split('?')[0] for name, href in firms}
            if page_hrefs.issubset(seen_hrefs):
                print(f"[Page {page_num}] Loop detected (all listings already scraped). Stopping crawl.")
                break
                
            # Add page listings to our collected list (preserving order)
            for name, href in firms:
                clean_href = href.split('?')[0]
                if clean_href not in seen_hrefs:
                    seen_hrefs.add(clean_href)
                    all_collected_firms.append((name, clean_href))
            print(f"[Page {page_num}] Cumulative unique listings: {len(seen_hrefs)}")
            
            # Stop if we collected all expected listings
            if total_places and len(seen_hrefs) >= total_places:
                print(f"[Page {page_num}] Reached target places count ({len(seen_hrefs)} >= {total_places}). Stopping crawl.")
                break
                
            # Locate Next button dynamically (no hardcoded obfuscated class names)
            next_btn_handle = await page.evaluate_handle("""() => {
                const pageLink = document.querySelector('a[href*="/page/"]') || document.querySelector('a[href*="/search/"]');
                if (!pageLink) return null;
                
                let container = pageLink.parentElement;
                while (container && !container.querySelector('svg')) {
                    container = container.parentElement;
                }
                if (!container) return null;
                
                const svgs = Array.from(container.querySelectorAll('svg'));
                for (const svg of svgs) {
                    const transform = svg.style.transform || '';
                    if (transform.includes('-90deg') || transform.includes('270deg')) {
                        return svg.closest('div') || svg;
                    }
                }
                return null;
            }""")
            
            next_btn = next_btn_handle.as_element()
            if not next_btn:
                print(f"[Page {page_num}] Next page button not found. Stopping crawl.")
                break
                
            # Check if Next button is disabled (inactive grey style)
            is_disabled = await page.evaluate("""(el) => {
                const style = window.getComputedStyle(el);
                const svg = el.querySelector('svg');
                const svgStyle = svg ? window.getComputedStyle(svg) : null;
                const color = style.color || '';
                const svgColor = svgStyle ? svgStyle.color : '';
                return color.includes('242, 242, 242') || svgColor.includes('242, 242, 242');
            }""", next_btn)
            
            if is_disabled:
                print(f"[Page {page_num}] Next button is disabled (inactive). Stopping crawl.")
                break
                
            print(f"[Page {page_num}] Clicking next page button...")
            old_url = page.url
            old_first_href = firms[0][1] if firms else ""
            
            await next_btn.click()
            
            # Wait for dynamic page transition
            transition_success = False
            for _ in range(25):  # wait up to 5 seconds
                await page.wait_for_timeout(200)
                current_firms = await extract_firms(page)
                current_first_href = current_firms[0][1] if current_firms else ""
                if page.url != old_url or current_first_href != old_first_href:
                    transition_success = True
                    break
                    
            if not transition_success:
                print(f"[Page {page_num}] Page transition timed out (button is likely inactive). Stopping crawl.")
                break
                
            page_num += 1
            await page.wait_for_timeout(1000)
            
        except Exception as page_err:
            print(f"[Error] Failed to scrape page {page_num}: {page_err}")
            break
            
    return all_collected_firms

async def scrape_firm_details(page, firm_href):
    """Scrapes detail fields from a single business card page. Propagates navigation errors."""
    detail_url = f"https://2gis.ru{firm_href}"
    print(f"[Details] Navigating to: {detail_url}")
    
    details = {
        "name": "None",
        "description": "None",
        "address": "None",
        "website": "None",
        "phones": [],
        "email": "None"
    }
    
    # We let navigation exceptions propagate so that main() can capture them and log the status.
    # We use wait_until="commit" to return control as soon as response headers are received,
    # and then wait for "h1" to render. This dramatically speeds up card details load times.
    await page.goto(detail_url, wait_until="commit", timeout=35000)
    try:
        await page.wait_for_selector("h1", timeout=10000)
    except Exception:
        pass
    await page.wait_for_timeout(500)
    
    # Locate the main contact details container (which is stable via data-rack="true")
    container = await page.query_selector("div[data-rack='true']")
    
    # Extract fields with safe defaults in case of element/parsing exceptions
    try:
        name_el = await page.query_selector("h1")
        if name_el:
            details["name"] = (await name_el.inner_text()).strip()
    except Exception:
        pass
        
    try:
        # Description is always a div immediately following h1 (h1 + div). Fallback to old obfuscated class.
        desc_el = await page.query_selector("h1 + div")
        if not desc_el:
            desc_el = await page.query_selector("div._1idnaau")
        if desc_el:
            details["description"] = (await desc_el.inner_text()).strip()
    except Exception:
        pass
        
    # Use the contacts container as search root to prevent leaking website/address/email from ads
    root_el = container if container else page
    
    try:
        address_el = await root_el.query_selector("a[href*='/geo/']")
        if address_el:
            details["address"] = (await address_el.inner_text()).strip()
    except Exception:
        pass
        
    try:
        email_el = await root_el.query_selector("a[href^='mailto:']")
        if email_el:
            details["email"] = (await email_el.inner_text()).strip()
    except Exception:
        pass
        
    try:
        # Extract website link ONLY from the contacts container
        all_links = await root_el.eval_on_selector_all(
            "a",
            "elements => elements.map(el => [el.innerText.trim(), el.getAttribute('href') || ''])"
        )
        website = extract_website_from_links(all_links)
        if website:
            details["website"] = website
    except Exception:
        pass
        
    try:
        phone_data = await page.evaluate("""() => {
            const container = document.querySelector("div[data-rack='true']");
            const root = container || document;
            const links = Array.from(root.querySelectorAll("a[href^='tel:']"));
            const validPhones = [];
            
            for (const el of links) {
                // Check if it's an ad via data-ps attribute
                const isPromo = el.closest('[data-ps="true"]') !== null || el.getAttribute('data-ps') === 'true';
                
                // Check if any parent div (up to 3 levels) contains the label "Реклама"
                let hasAdLabel = false;
                let parent = el.parentElement;
                for (let i = 0; i < 3; i++) {
                    if (!parent || parent.tagName === 'BODY') break;
                    if (parent.innerText && parent.innerText.includes('Реклама')) {
                        hasAdLabel = true;
                        break;
                    }
                    parent = parent.parentElement;
                }
                
                if (isPromo || hasAdLabel) {
                    continue;
                }
                
                const href = el.getAttribute('href') || '';
                const phoneNum = href.replace('tel:', '').trim();
                if (phoneNum) {
                    validPhones.push(phoneNum);
                }
            }
            return validPhones;
        }""")
        
        seen_phones = set()
        for phone_num in phone_data:
            if phone_num and phone_num not in seen_phones:
                seen_phones.add(phone_num)
                details["phones"].append(phone_num)
    except Exception:
        pass
        
    return details

def save_results_to_excel(records, filename="2gis_results.xlsx"):
    """Saves the list of scraped records to an Excel spreadsheet dynamically."""
    if not records:
        return
    try:
        df = pd.DataFrame(records)
        # Ensure all expected columns are present
        expected_cols = ["City", "Query", "name", "url", "description", "address", "website", "email", "phones", "status"]
        for col in expected_cols:
            if col not in df.columns:
                df[col] = "None"
                
        df = df[expected_cols]
        
        russian_columns = {
            "City": "Город",
            "Query": "Поисковый запрос",
            "name": "Название",
            "url": "Ссылка на карточку",
            "description": "Описание",
            "address": "Адрес",
            "website": "Сайт",
            "email": "Email",
            "phones": "Телефоны",
            "status": "Статус сбора"
        }
        df = df.rename(columns=russian_columns)
        
        # Convert list of phones to comma-separated string
        df["Телефоны"] = df["Телефоны"].apply(lambda x: ", ".join(x) if isinstance(x, list) else x)
        
        df.to_excel(filename, index=False)
    except Exception as e:
        print(f"[Error] Failed to write to Excel: {e}")

async def details_worker(worker_id, proxy, queue, scraped_records, excel_filename, lock):
    """Worker task that consumes cards from the queue and scrapes them using a dedicated proxy context."""
    print(f"[Worker {worker_id}] Starting with proxy {proxy['server']}...")
    
    async with async_playwright() as p:
        browser = None
        context = None
        page = None
        
        async def init_browser():
            nonlocal browser, context, page
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            browser = await p.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            
            # Block heavy media, images, fonts, and trackers to speed up and optimize loading
            async def block_resources(route):
                url = route.request.url.lower()
                resource_type = route.request.resource_type
                if resource_type in ["image", "font", "media"]:
                    await route.abort()
                    return
                blocked_domains = [
                    "mc.yandex.ru", "google-analytics", "doubleclick", "mail.ru", "vk.com", "facebook"
                ]
                if any(d in url for d in blocked_domains):
                    await route.abort()
                    return
                await route.continue_()
                
            await context.route("**/*", block_resources)
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)
            
            # Navigate to standard page to bypass warning screen for this proxy session
            try:
                base_url = "https://2gis.ru/"
                await page.goto(base_url, wait_until="domcontentloaded", timeout=25000)
                await bypass_warning(page, base_url)
            except Exception as e:
                print(f"[Worker {worker_id}] Warning bypass failed or timed out: {e}. Trying to proceed...")
                
        await init_browser()
        
        consecutive_failures = 0
        while True:
            task = await queue.get()
            if task is None:  # Sentinel to shutdown worker
                queue.task_done()
                break
                
            city, keyword, name, href, idx, total_count = task
            card_url = f"https://2gis.ru{href}"
            print(f"[Worker {worker_id}] [{idx}/{total_count}] Processing: {name} | Proxy: {proxy['server']}")
            
            try:
                details = await scrape_firm_details(page, href)
                
                # Fallback to name collected during listing crawl if title loading failed
                if details["name"] == "None" or not details["name"]:
                    details["name"] = name
                    
                record = {
                    "City": city,
                    "Query": keyword,
                    "name": details["name"],
                    "url": card_url,
                    "description": details["description"],
                    "address": details["address"],
                    "website": details["website"],
                    "email": details["email"],
                    "phones": details["phones"],
                    "status": "Успешно"
                }
                
                consecutive_failures = 0
                
                # Thread-safe excel save
                async with lock:
                    scraped_records.append(record)
                    save_results_to_excel(scraped_records, excel_filename)
                    
                print("==================================================")
                print(f"[Worker {worker_id}] Город:    {city}")
                print(f"[Worker {worker_id}] Запрос:   {keyword}")
                print(f"[Worker {worker_id}] Название: {record['name']}")
                print(f"[Worker {worker_id}] Ссылка:   {record['url']}")
                print(f"[Worker {worker_id}] Описание: {record['description']}")
                print(f"[Worker {worker_id}] Адрес:    {record['address']}")
                print(f"[Worker {worker_id}] Сайт:     {record['website']}")
                print(f"[Worker {worker_id}] Email:    {record['email']}")
                print(f"[Worker {worker_id}] Телефоны: {', '.join(record['phones']) if record['phones'] else 'None'}")
                print(f"[Worker {worker_id}] Статус:   {record['status']}")
                print("==================================================")
                
                # Human-like delay
                delay = random.uniform(1.0, 2.0)
                await asyncio.sleep(delay)
                
            except Exception as err:
                print(f"[Worker {worker_id}] [Error] Failed card {name} with proxy {proxy['server']}: {err}")
                consecutive_failures += 1
                
                # Re-queue task so another working proxy can pick it up
                print(f"[Worker {worker_id}] Re-queueing task for card {name}...")
                queue.put_nowait(task)
                
                # Refresh browser session if consecutive failures happen
                if consecutive_failures >= 2:
                    print(f"[Worker {worker_id}] Re-initializing browser context due to multiple failures...")
                    try:
                        await init_browser()
                        consecutive_failures = 0
                    except Exception as init_err:
                        print(f"[Worker {worker_id}] Re-init failed: {init_err}")
                        await asyncio.sleep(5)  # cool down
                        
            queue.task_done()
            
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
            
    print(f"[Worker {worker_id}] Stopped.")

async def main():
    print(f"[Init] Cities to scrape: {CITIES}")
    print(f"[Init] Keywords to scrape: {KEYWORDS}")
    print(f"[Init] Total proxies: {len(PROXIES)}")
    
    all_scraped_records = []
    excel_filename = "2gis_results.xlsx"
    lock = asyncio.Lock()
    
    # We use one main browser context to crawl the listing pagination.
    # We use the first proxy for it.
    async with async_playwright() as p:
        print(f"[Init] Launching crawl-master browser with proxy {PROXIES[0]['server']}...")
        master_browser = await p.chromium.launch(headless=True, proxy=PROXIES[0])
        master_context = await master_browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        
        # Block heavy media, images, fonts, and trackers to speed up and optimize loading
        async def block_resources(route):
            url = route.request.url.lower()
            resource_type = route.request.resource_type
            if resource_type in ["image", "font", "media"]:
                await route.abort()
                return
            blocked_domains = [
                "mc.yandex.ru", "google-analytics", "doubleclick", "mail.ru", "vk.com", "facebook"
            ]
            if any(d in url for d in blocked_domains):
                await route.abort()
                return
            await route.continue_()
            
        await master_context.route("**/*", block_resources)
        master_page = await master_context.new_page()
        await Stealth().apply_stealth_async(master_page)
        
        # Loop over cities and keywords
        for city in CITIES:
            for keyword in KEYWORDS:
                try:
                    # 1. Crawl search results to get firm list
                    firm_links = await scrape_query(master_page, city, keyword)
                    print(f"\n[Master] Found {len(firm_links)} unique business links. Starting parallel details extraction...")
                    
                    if not firm_links:
                        continue
                        
                    # 2. Setup asyncio.Queue for details workers
                    queue = asyncio.Queue()
                    crawl_links = firm_links[:MAX_DETAILS] if MAX_DETAILS else firm_links
                    
                    for idx, (name, href) in enumerate(crawl_links):
                        queue.put_nowait((city, keyword, name, href, idx + 1, len(crawl_links)))
                        
                    # 3. Launch worker tasks (one per proxy)
                    workers = []
                    for worker_id, proxy in enumerate(PROXIES):
                        workers.append(asyncio.create_task(
                            details_worker(worker_id + 1, proxy, queue, all_scraped_records, excel_filename, lock)
                        ))
                        
                    # Wait for all queue items to be processed
                    await queue.join()
                    
                    # Send sentinels to shut down workers
                    for _ in range(len(PROXIES)):
                        await queue.put(None)
                        
                    # Wait for all workers to shut down
                    await asyncio.gather(*workers)
                    
                    print(f"\n[Finished] Scraped details for city='{city}' | query='{keyword}'. Total collected so far: {len(all_scraped_records)}")
                    
                except Exception as search_err:
                    print(f"[Error] Failed to search city='{city}' | query='{keyword}': {search_err}")
                    # Log search failure in Excel
                    record = {
                        "City": city,
                        "Query": keyword,
                        "name": "[Ошибка поиска]",
                        "url": "None",
                        "description": "None",
                        "address": "None",
                        "website": "None",
                        "email": "None",
                        "phones": [],
                        "status": f"Ошибка поиска: {search_err}"
                    }
                    async with lock:
                        all_scraped_records.append(record)
                        save_results_to_excel(all_scraped_records, excel_filename)
                        
        await master_browser.close()
        
    print(f"\n[Finished] Batch scraping complete. Output file is {excel_filename}")

if __name__ == "__main__":
    asyncio.run(main())
