import sys
from pathlib import Path
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
import check_pricing as cp

FIX = Path(__file__).parent / "fixtures"

EXPECTED = {
    "claude-opus-4-8":     {"input": 5.0,  "output": 25.0, "cached": 0.50},
    "claude-sonnet-4-6":   {"input": 3.0,  "output": 15.0, "cached": 0.30},
    "claude-haiku-4-5":    {"input": 1.0,  "output": 5.0,  "cached": 0.10},
    "gpt-5-5":             {"input": 5.0,  "output": 30.0, "cached": 0.50},
    "gpt-5-4":             {"input": 2.5,  "output": 15.0, "cached": 0.25},
    "gpt-5-4-mini":        {"input": 0.75, "output": 4.5,  "cached": 0.075},
    "gpt-5-4-nano":        {"input": 0.20, "output": 1.25, "cached": 0.02},
    "gemini-3-5-flash":        {"input": 1.50, "output": 9.00,  "cached": 0.15},
    "gemini-3-1-flash-lite":   {"input": 0.25, "output": 1.50,  "cached": 0.025},
    "gemini-3-1-pro-200":      {"input": 2.00, "output": 12.00, "cached": 0.20},
    "gemini-3-1-pro-200p":     {"input": 4.00, "output": 18.00, "cached": 0.40},
    "gemini-2-5-pro-200":      {"input": 1.25, "output": 10.00, "cached": 0.125},
    "gemini-2-5-pro-200p":     {"input": 2.50, "output": 15.00, "cached": 0.25},
    "gemini-2-5-flash":        {"input": 0.30, "output": 2.50,  "cached": 0.03},
}


def load(name):
    return BeautifulSoup((FIX / name).read_text(), "lxml")


def main():
    found, unparsed = {}, []
    cp.parse_anthropic(load("anthropic.html"), found, unparsed)
    cp.parse_openai(load("openai.html"), found, unparsed)
    cp.parse_google(load("google.html"), found, unparsed)

    failures = 0
    for model_id, expected in EXPECTED.items():
        got = found.get(model_id)
        if got is None:
            print(f"FAIL  {model_id}: not found at all")
            failures += 1
            continue
        for field, exp_val in expected.items():
            got_val = got.get(field)
            if got_val is None or abs(got_val - exp_val) > 1e-9:
                print(f"FAIL  {model_id}.{field}: expected {exp_val}, got {got_val}")
                failures += 1

    extra = set(found) - set(EXPECTED)
    if extra:
        print(f"FAIL  unexpected models extracted (should not have been picked up): {extra}")
        failures += len(extra)

    print(f"\n{len(EXPECTED) - failures if failures < len(EXPECTED) else 0} checks effectively passed, {failures} failures")
    print(f"unparsed entries logged: {len(unparsed)} -> {unparsed}")

    if failures:
        sys.exit(1)
    print("\nALL GOOD")


if __name__ == "__main__":
    main()
