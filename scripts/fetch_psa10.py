#!/usr/bin/env python3
import json
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import date
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Instalar: pip install playwright && python -m playwright install chromium")
    sys.exit(1)

try:
    from playwright_stealth import stealth_sync
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

ROOT = Path(__file__).parent.parent
POKEMON_API = "https://api.pokemontcg.io/v2"
DEBUG = "--debug" in sys.argv


def parse_price(text):
    first = text.strip().split()[0] if text.strip() else ""
    clean = re.sub(r"[^0-9.]", "", first)
    try:
        val = float(clean)
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def slugify(name):
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    return name.strip("-")


def get_set_cards(set_id):
    url = f"{POKEMON_API}/cards?q=set.id:{set_id}&pageSize=250&orderBy=number"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    return data.get("data", [])


def scrape_psa10(page, url):
    psa10_index = None
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        for row in page.query_selector_all("tr"):
            try:
                cells = row.query_selector_all("td")
                if not cells:
                    continue
                row_text = row.inner_text().strip()
                if not row_text:
                    continue
                cols = [c.strip() for c in row_text.split("\t")]
                if DEBUG:
                    print(f"  ROW: {repr(row_text[:120])}")
                if "PSA 10" in cols:
                    psa10_index = cols.index("PSA 10")
                    continue
                if psa10_index is not None:
                    if len(cols) > psa10_index:
                        price = parse_price(cols[psa10_index])
                        if price is not None:
                            return price
                    psa10_index = None
            except Exception:
                continue
    except Exception as e:
        if DEBUG:
            print(f"  Error: {e}")
    return None


def main():
    track_file = ROOT / "to_track.json"
    slugs_file = ROOT / "set_slugs.json"
    prices_file = ROOT / "prices.json"

    with open(track_file) as f:
        to_track = json.load(f)

    with open(slugs_file) as f:
        set_slugs = json.load(f)

    with open(prices_file) as f:
        data = json.load(f)

    prices = data.get("prices", {})
    updated = 0
    manual_failed = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()
        if HAS_STEALTH:
            stealth_sync(page)

        # Entradas manuales de to_track.json
        if to_track:
            print("=== Manual (to_track.json) ===")
        for card_id, url in to_track.items():
            slug = url.split("/")[-1]
            print(f"-> {card_id} ({slug})")
            psa10 = scrape_psa10(page, url)
            if psa10 is not None:
                prices.setdefault(card_id, {})["psa10"] = psa10
                print(f"   OK PSA 10: ${psa10:,.2f}")
                updated += 1
            else:
                manual_failed.append(card_id)
                print(f"   FAIL: no encontrado")
            time.sleep(2)

        # Sets completos de set_slugs.json
        for set_id, pc_slug in set_slugs.items():
            print(f"\n=== Set {set_id} -> {pc_slug} ===")
            try:
                cards = get_set_cards(set_id)
            except Exception as e:
                print(f"  Error al obtener cartas: {e}")
                continue

            for card in cards:
                card_id = card["id"]
                name = card.get("name", "")
                number = card.get("number", "")
                name_slug = slugify(name)
                url = f"https://www.pricecharting.com/game/{pc_slug}/{name_slug}-{number}"
                print(f"-> {card_id} ({name_slug}-{number})")
                if DEBUG:
                    print(f"   URL: {url}")
                psa10 = scrape_psa10(page, url)
                if psa10 is not None:
                    prices.setdefault(card_id, {})["psa10"] = psa10
                    print(f"   OK PSA 10: ${psa10:,.2f}")
                    updated += 1
                else:
                    print(f"   SKIP")
                time.sleep(2)

        browser.close()

    data["prices"] = prices
    data["updated"] = str(date.today())

    with open(prices_file, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\nActualizadas: {updated}")
    if manual_failed:
        print("Manuales fallidas:", ", ".join(manual_failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
