#!/usr/bin/env python3
"""
Monthly LLM pricing watcher for the Automation Ledger ROI calculator.

Fetches the official pricing pages for Google, OpenAI, and Anthropic,
extracts current per-1M-token prices for the models tracked in
pricing-data.json, and reports any differences.

This script does NOT silently trust what it parses. If a model's prices
look different from last time, it writes them to pricing-data.json AND
writes a plain-English diff to pricing-diff-summary.md -- the GitHub
Actions workflow turns that into a pull request for a human to read
before it goes live. If a price can't be confidently found, the model is
listed under "Could not verify" instead of being guessed at.

FRAGILITY NOTE: this parses the *visible text* of marketing pages that
were not built to be machine-readable. It is written against the page
structure observed in June 2026. If a provider redesigns its pricing
page, extraction for that provider may start coming back empty -- that
will show up as "Could not verify" entries, not as silently wrong
numbers, but it does mean this script needs the occasional five-minute
tune-up. Treat every opened PR as something to read, not something to
rubber-stamp.
"""

import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PricingWatcher/1.0) "
        "monthly internal pricing check for an ROI calculator -- "
        "not for resale or republication"
    )
}
TIMEOUT = 20

ROOT = Path(__file__).parent
DATA_FILE = ROOT / "pricing-data.json"
SUMMARY_FILE = ROOT / "pricing-diff-summary.md"

PAGES = {
    "Google": "https://ai.google.dev/gemini-api/docs/pricing",
    "OpenAI": "https://openai.com/api/pricing/",
    "Anthropic": "https://platform.claude.com/docs/en/about-claude/pricing",
}


def fetch(url):
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


def money(text):
    """Pull the first dollar figure out of a text fragment, e.g. '$1.50' -> 1.50."""
    if not text:
        return None
    m = re.search(r"\$\s*([0-9]+(?:\.[0-9]+)?)", text)
    return float(m.group(1)) if m else None


def section_between(start_tag, end_tag):
    """Yield tags after start_tag up to (but not including) end_tag, in document order."""
    for tag in start_tag.find_all_next():
        if end_tag is not None and tag is end_tag:
            break
        yield tag


# ---------------------------------------------------------------------------
# Anthropic -- docs.claude.com renders a clean table; parse by column.
# Header order: Model | Base Input Tokens | 5m Cache Writes | 1h Cache Writes
#               | Cache Hits & Refreshes | Output Tokens
# ---------------------------------------------------------------------------
def parse_anthropic(soup, found, unparsed):
    target = {
        "Claude Opus 4.8": "claude-opus-4-8",
        "Claude Sonnet 4.6": "claude-sonnet-4-6",
        "Claude Haiku 4.5": "claude-haiku-4-5",
    }

    table = None
    for heading in soup.find_all(["h1", "h2", "h3"]):
        if "model pricing" in heading.get_text(strip=True).lower():
            table = heading.find_next("table")
            break

    if table is None:
        for model_id in target.values():
            unparsed.append(("Anthropic", model_id, "could not find the model pricing table"))
        return

    for row in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) < 6:
            continue
        for label, model_id in target.items():
            if cells[0].strip() == label:
                try:
                    input_price = money(cells[1])
                    cached_price = money(cells[4])  # Cache Hits & Refreshes
                    output_price = money(cells[5])
                    if input_price is None or output_price is None:
                        raise ValueError("missing input or output price")
                    found[model_id] = {
                        "input": input_price,
                        "output": output_price,
                        "cached": cached_price,
                    }
                except Exception as e:
                    unparsed.append(("Anthropic", model_id, f"row found but couldn't read prices ({e})"))

    for model_id in target.values():
        if model_id not in found and not any(u[1] == model_id for u in unparsed):
            unparsed.append(("Anthropic", model_id, "row not found in the table"))


# ---------------------------------------------------------------------------
# OpenAI -- a marketing page, not a table. Each model is a heading followed
# by "Input:", "Cached input:", "Output:" lines a little further down.
# ---------------------------------------------------------------------------
def parse_openai(soup, found, unparsed):
    target = {
        "GPT-5.5": "gpt-5-5",
        "GPT-5.4": "gpt-5-4",
        "GPT-5.4 mini": "gpt-5-4-mini",
        "GPT-5.4 nano": "gpt-5-4-nano",
    }

    headings = soup.find_all(["h2"])
    for idx, h2 in enumerate(headings):
        title = h2.get_text(strip=True)
        if title not in target:
            continue
        model_id = target[title]

        next_h2 = headings[idx + 1] if idx + 1 < len(headings) else None
        window_text = "\n".join(
            t.get_text(" ", strip=True) for t in section_between(h2, next_h2) if t.get_text(strip=True)
        )

        in_m = re.search(r"Input:\s*\$?\s*([0-9.]+)", window_text)
        out_m = re.search(r"Output:\s*\$?\s*([0-9.]+)", window_text)
        cache_m = re.search(r"Cached input:\s*\$?\s*([0-9.]+)", window_text)

        if in_m and out_m:
            found[model_id] = {
                "input": float(in_m.group(1)),
                "output": float(out_m.group(1)),
                "cached": float(cache_m.group(1)) if cache_m else None,
            }
        else:
            unparsed.append(("OpenAI", model_id, "found the heading but not the input/output prices near it"))

    for model_id in target.values():
        if model_id not in found and not any(u[1] == model_id for u in unparsed):
            unparsed.append(("OpenAI", model_id, "heading not found on the page"))


# ---------------------------------------------------------------------------
# Google -- each model is an <h2>, with a "Standard" / "Batch" / "Flex" /
# "Priority" set of sub-tables. We only want "Standard". Some cells pack two
# prices together, e.g. "$2.00, prompts <= 200k tokens   $4.00, prompts >
# 200k tokens" -- those need splitting into the two model variants we track.
# ---------------------------------------------------------------------------
def extract_variant(cell_text, variant):
    if cell_text is None:
        return None
    if variant is None:
        return money(cell_text)
    needle = "<= 200k" if variant == "<=200k" else "> 200k"
    m = re.search(r"\$([0-9.]+)[^$]*?" + re.escape(needle), cell_text)
    return float(m.group(1)) if m else None


def parse_google(soup, found, unparsed):
    # label -> list of (model_id, variant) where variant is None, "<=200k", or ">200k"
    target = {
        "Gemini 3.5 Flash": [("gemini-3-5-flash", None)],
        "Gemini 3.1 Flash-Lite": [("gemini-3-1-flash-lite", None)],
        "Gemini 3.1 Pro Preview": [("gemini-3-1-pro-200", "<=200k"), ("gemini-3-1-pro-200p", ">200k")],
        "Gemini 2.5 Pro": [("gemini-2-5-pro-200", "<=200k"), ("gemini-2-5-pro-200p", ">200k")],
        "Gemini 2.5 Flash": [("gemini-2-5-flash", None)],
    }

    h2s = soup.find_all("h2")
    for idx, h2 in enumerate(h2s):
        title = h2.get_text(strip=True)
        if title not in target:
            continue
        variants = target[title]
        model_ids = [v[0] for v in variants]

        next_h2 = h2s[idx + 1] if idx + 1 < len(h2s) else None
        h3 = next(
            (t for t in section_between(h2, next_h2) if t.name == "h3" and t.get_text(strip=True).lower() == "standard"),
            None,
        )
        if h3 is None:
            for model_id in model_ids:
                unparsed.append(("Google", model_id, "couldn't find a 'Standard' pricing table for this model"))
            continue

        table = h3.find_next("table")
        if table is None or (next_h2 is not None and table not in list(section_between(h2, next_h2))):
            for model_id in model_ids:
                unparsed.append(("Google", model_id, "'Standard' heading found but no table after it"))
            continue

        row_text = {}
        for row in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) >= 3:
                row_text[cells[0].lower()] = cells[2]  # 3rd column = paid-tier price

        input_cell = next((v for k, v in row_text.items() if k.startswith("input price")), None)
        output_cell = next((v for k, v in row_text.items() if k.startswith("output price")), None)
        cache_cell = next((v for k, v in row_text.items() if "caching price" in k), None)

        for model_id, variant in variants:
            try:
                input_price = extract_variant(input_cell, variant)
                output_price = extract_variant(output_cell, variant)
                cached_price = extract_variant(cache_cell, variant) if cache_cell else None
                if input_price is None or output_price is None:
                    raise ValueError("missing input or output price")
                found[model_id] = {
                    "input": input_price,
                    "output": output_price,
                    "cached": cached_price,
                }
            except Exception as e:
                unparsed.append(("Google", model_id, f"table found but couldn't read prices ({e})"))


PARSERS = [
    ("Google", parse_google),
    ("OpenAI", parse_openai),
    ("Anthropic", parse_anthropic),
]


def run(fetch_fn=fetch):
    found = {}
    unparsed = []

    for provider, parser in PARSERS:
        try:
            soup = fetch_fn(PAGES[provider])
            parser(soup, found, unparsed)
        except Exception as e:
            unparsed.append((provider, "all", f"page fetch failed: {e}"))

    existing = json.loads(DATA_FILE.read_text()) if DATA_FILE.exists() else {}
    today = date.today().isoformat()

    changes = []
    for model_id, prices in found.items():
        old = existing.get(model_id, {})
        diff_fields = {}
        for field in ("input", "output", "cached"):
            new_val = prices.get(field)
            old_val = old.get(field)
            if new_val is not None and new_val != old_val:
                diff_fields[field] = {"old": old_val, "new": new_val}
        if diff_fields:
            changes.append({"model_id": model_id, "changes": diff_fields})
            existing[model_id] = {**old, **prices, "verified": today, "source": "auto-check"}

    if changes:
        DATA_FILE.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n")

    lines = [f"# Pricing check -- {today}", ""]
    if changes:
        lines.append("## Detected price changes")
        lines.append("")
        for c in changes:
            lines.append(f"- **{c['model_id']}**")
            for field, vals in c["changes"].items():
                old_disp = vals["old"] if vals["old"] is not None else "n/a"
                lines.append(f"  - {field}: {old_disp} -> {vals['new']}")
        lines.append("")
    else:
        lines.append("No price changes detected.")
        lines.append("")

    if unparsed:
        lines.append("## Could not verify -- check these manually")
        lines.append("")
        for provider, model_id, reason in unparsed:
            lines.append(f"- {provider} / {model_id}: {reason}")
        lines.append("")

    summary = "\n".join(lines)
    SUMMARY_FILE.write_text(summary + "\n")
    print(summary)

    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a") as f:
            f.write(f"changes_detected={'true' if changes else 'false'}\n")

    return {"found": found, "unparsed": unparsed, "changes": changes}


if __name__ == "__main__":
    run()
    sys.exit(0)
