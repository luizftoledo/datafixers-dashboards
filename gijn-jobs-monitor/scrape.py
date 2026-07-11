#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright


JOBS_URL = "https://gijn.org/jobs/"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Locations are eligible when they are UK-based, or remote without a non-UK restriction.
UK_KEYWORDS = (
    "united kingdom",
    "uk",
    "london",
    "england",
    "scotland",
    "wales",
    "northern ireland",
    "oxford",
    "cambridge",
    "manchester",
    "edinburgh",
    "glasgow",
    "belfast",
    "cardiff",
    "bristol",
    "birmingham",
    "leeds",
    "liverpool",
    "newcastle",
    "sheffield",
    "nottingham",
    "brighton",
    "various in the uk",
)
REMOTE_EXCLUDE_PATTERNS = (
    r"\bin (?:the )?(?:u\.?s\.?|usa|united states)\b",
    r"\b(?:u\.?s\.?|usa)[ -]?(?:only|based)\b",
    r"\b(?:africa|asia|europe|latin america|middle east|oceania)\b",
    r"\b(?:india|canada|australia|new zealand|germany|france|spain|italy|brazil|mexico|"
    r"argentina|colombia|chile|peru|japan|china|singapore|philippines|indonesia|"
    r"south korea|nigeria|kenya|south africa|uganda|tanzania|rwanda|egypt|israel|"
    r"turkey|ukraine|poland|netherlands|sweden|norway|denmark|finland|switzerland|"
    r"ireland)\b",
)


def normalized_text(value: str) -> str:
    return " ".join(value.split())


def has_uk_indicator(location: str) -> bool:
    location = location.lower()
    return any(
        re.search(r"\buk\b", location) if keyword == "uk" else keyword in location
        for keyword in UK_KEYWORDS
    )


def is_eligible(location: str) -> bool:
    if has_uk_indicator(location):
        return True
    location = location.lower()
    return "remote" in location and not any(
        re.search(pattern, location, re.IGNORECASE) for pattern in REMOTE_EXCLUDE_PATTERNS
    )


def scrape_jobs() -> tuple[int, list[dict[str, str]]]:
    # The site blocks plain HTTP clients with Cloudflare, but renders in Chromium.
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    launch_args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if proxy_url:
        # This sandbox's outbound TLS-inspecting proxy resets the connection on
        # Chromium's TLS 1.3 ClientHello (post-quantum hybrid key share); TLS 1.2
        # avoids that. Also route explicitly via --proxy-server since env vars
        # alone aren't picked up reliably by the launched browser process.
        launch_args.append(f"--proxy-server={proxy_url}")
        launch_args.append("--ssl-version-max=tls1.2")
    with sync_playwright() as playwright:
        try:
            # Explicit executable_path avoids Playwright silently substituting
            # the headless_shell binary, which hits the TLS reset above even
            # with the same launch args.
            browser = playwright.chromium.launch(
                headless=True, executable_path=playwright.chromium.executable_path, args=launch_args
            )
        except Exception as error:
            raise RuntimeError("Could not launch Chromium for GIJN scraping") from error
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            response = page.goto(JOBS_URL, wait_until="domcontentloaded", timeout=30_000)
            if response is None or not response.ok:
                status = "no response" if response is None else f"HTTP {response.status}"
                raise RuntimeError(f"Could not load {JOBS_URL}: {status}")

            try:
                page.wait_for_selector("main .tax__postsgrid a.jobpreview", timeout=30_000)
            except Exception as error:
                raise RuntimeError("Job listing selector was not found on the GIJN page") from error

            container = page.query_selector("main .tax__postsgrid")
            if container is None:
                raise RuntimeError("Job results container was not found on the GIJN page")
            job_nodes = container.query_selector_all("a.jobpreview")
            if not job_nodes:
                raise RuntimeError("No job listings found; the GIJN page structure may have changed")

            jobs = []
            for node in job_nodes:
                title_node = node.query_selector("h3")
                details = node.query_selector_all(":scope > div")
                href = node.get_attribute("href")
                if title_node is None or len(details) < 3 or not href:
                    raise RuntimeError("A GIJN job listing is missing an expected title, detail, or URL")

                deadline = normalized_text(details[2].inner_text())
                jobs.append(
                    {
                        "title": normalized_text(title_node.inner_text()),
                        "company": normalized_text(details[0].inner_text()),
                        "location": normalized_text(details[1].inner_text()),
                        "deadline": re.sub(r"^Deadline:\s*", "", deadline, flags=re.IGNORECASE),
                        "url": urljoin(JOBS_URL, href),
                    }
                )
            return len(jobs), jobs
        finally:
            browser.close()


def load_previous_urls(state_file: Path) -> set[str]:
    if not state_file.exists():
        return set()
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read state file {state_file}: {error}") from error
    if not isinstance(state, list):
        raise RuntimeError(f"State file {state_file} must contain a JSON list")
    return {item["url"] for item in state if isinstance(item, dict) and isinstance(item.get("url"), str)}


def write_state(state_file: Path, jobs: list[dict[str, str]]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(jobs, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor new UK and unrestricted remote GIJN jobs.")
    parser.add_argument("--state-file", default="gijn-jobs-monitor/state.json")
    parser.add_argument("--dry-run", action="store_true", help="Do not update the state file.")
    args = parser.parse_args()
    state_file = Path(args.state_file)

    try:
        previous_urls = load_previous_urls(state_file)
        total_posts, jobs = scrape_jobs()
        eligible = [job for job in jobs if is_eligible(job["location"])]
        new_jobs = [job for job in eligible if job["url"] not in previous_urls]
        if not args.dry_run:
            write_state(state_file, eligible)
    except Exception as error:
        print(f"GIJN jobs monitor failed: {error}", file=sys.stderr)
        return 1

    print(json.dumps({"total_posts": total_posts, "eligible": eligible, "new_jobs": new_jobs}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
