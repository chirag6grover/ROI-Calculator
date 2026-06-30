# Pricing watcher — setup guide

This folder makes the Automation Ledger calculator's prices check
themselves once a month, instead of you checking them by hand.

## What's in here

- `check_pricing.py` — visits the three official pricing pages, reads off
  the current prices, and compares them to `pricing-data.json`.
- `pricing-data.json` — the current snapshot the calculator reads. Starts
  seeded with the June 2026 prices from your original sheet.
- `requirements.txt` — the two Python packages the script needs.
- `.github/workflows/update-pricing.yml` — runs the script automatically
  on the 1st of every month (and any time you trigger it by hand).
- `test_check_pricing.py` + `fixtures/` — a small test suite that checks
  the parsing logic still works. Re-run this (`python3
  test_check_pricing.py`) any time you tweak `check_pricing.py`, e.g.
  after a provider redesigns its pricing page and extraction starts
  coming back empty for them.

## Setup (about 10 minutes, one time)

1. **Create a GitHub repo** (or use one you already have). It needs to be
   **public** — the calculator reads `pricing-data.json` directly from
   GitHub in the browser, and that only works without a login for public
   repos. If you'd rather keep things private, that's still possible, it
   just needs a small proxy instead of a direct fetch — ask and this can
   be set up that way instead.

2. **Copy this whole `pricing-watcher` folder, including the hidden
   `.github` folder inside it, into the root of that repo.** GitHub only
   picks up workflows that live at `.github/workflows/` in the repo root,
   so the nesting matters.

3. **Push it.** GitHub Actions is on by default for new repos — nothing
   else to flip on.

4. **Grab your raw data URL.** It will look like:

   ```
   https://raw.githubusercontent.com/<your-username>/<your-repo>/main/pricing-watcher/pricing-data.json
   ```

5. **Open `roi-calculator.html`**, find this line near the top of the
   `<script>` block:

   ```js
   var PRICING_FEED_URL = "";
   ```

   and paste your URL in between the quotes. Save the file. That's the
   only edit needed — the calculator will now fetch this file every time
   it's opened, layering it on top of its built-in defaults (and falling
   back to those defaults if the fetch ever fails, e.g. you're offline).

6. **Test it once manually:** in your repo, go to the *Actions* tab →
   *Monthly LLM pricing check* → *Run workflow*. It'll run in about 30
   seconds. If any prices differ from `pricing-data.json`, you'll see a
   new pull request with a plain-English summary of what changed and
   where it found it.

7. From here it just runs itself on the 1st of every month. When a PR
   shows up, skim it (should take under a minute) and merge it. The
   calculator picks up the change automatically the next time anyone
   opens it — no further edits needed.

## Why review-before-merge, not silent auto-apply

These pricing pages are marketing pages, not APIs — they weren't built to
be machine-readable, and OpenAI's and Google's pages in particular mix
several pricing tiers and inconsistent layouts per model. The script is
written defensively (it lists anything it couldn't confidently read under
"Could not verify" rather than guessing), but no scraper against a page
like this is bulletproof forever. A wrong number silently feeding a
client-facing ROI number is the one failure mode worth a 60-second human
check against, so changes land as a PR, not a direct commit.

## If extraction starts failing

If a provider redesigns their pricing page, that provider's models will
start showing up under "Could not verify" in the PR body instead of
"Detected price changes" — the script is written to flag uncertainty
rather than write a guess. When that happens:

1. Open the page in a browser and see what changed.
2. Update the matching `parse_<provider>()` function in
   `check_pricing.py`.
3. Run `python3 test_check_pricing.py` to check your fix didn't break
   anything else, then commit.

This will need attention every so often — it's the honest tradeoff for
not paying for a commercial pricing-tracking service.
