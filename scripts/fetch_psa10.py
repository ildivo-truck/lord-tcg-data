#!/usr/bin/env python3
import asyncio
import json
import re
import sys
import unicodedata
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("Instalar: pip install playwright && python -m playwright install chromium")
    sys.exit(1)

ROOT = Path(__file__).parent.parent
POKEMON_API = "https://api.pokemontcg.io/v2"
DEBUG = "--debug" in sys.argv
CONCURRENCY = 5   # páginas del browser corriendo en paralelo
STALE_DAYS = 6    # re-scrapear cartas no actualizadas en los últimos 6 días


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


async def scrape_psa10(page, url):
    psa10_index = None
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        rows = await page.query_selector_all("tr")
        for row in rows:
            try:
                cells = await row.query_selector_all("td")
                if not cells:
                    continue
                row_text = (await row.inner_text()).strip()
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


async def process_card(sem, context, card_id, url, prices, today_str):
    async with sem:
        page = await context.new_page()
        try:
            psa10 = await scrape_psa10(page, url)
            if psa10 is not None:
                prices.setdefault(card_id, {})["psa10"] = psa10
                prices[card_id]["last_updated"] = today_str
                print(f"   OK {card_id}: ${psa10:,.2f}")
                return True
            else:
                if DEBUG:
                    print(f"   SKIP {card_id}")
                return False
        finally:
            await page.close()
            await asyncio.sleep(0.5)


async def amain():
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
    today = date.today()
    today_str = str(today)
    cutoff = today - timedelta(days=STALE_DAYS)

    def is_fresh(card_id):
        lu = prices.get(card_id, {}).get("last_updated")
        if not lu:
            return False
        try:
            return datetime.fromisoformat(lu).date() >= cutoff
        except ValueError:
            return False

    tasks = []
    manual_ids = set()
    skipped = 0

    # Entradas manuales de to_track.json
    for card_id, url in to_track.items():
        manual_ids.add(card_id)
        if is_fresh(card_id):
            skipped += 1
        else:
            tasks.append((card_id, url))

    # Sets completos de set_slugs.json
    for set_id, pc_slug in set_slugs.items():
        try:
            cards = get_set_cards(set_id)
        except Exception as e:
            print(f"Error en set {set_id}: {e}")
            continue
        for card in cards:
            card_id = card["id"]
            if is_fresh(card_id):
                skipped += 1
                continue
            name_slug = slugify(card.get("name", ""))
            number = card.get("number", "")
            url = f"https://www.pricecharting.com/game/{pc_slug}/{name_slug}-{number}"
            tasks.append((card_id, url))

    print(f"Total: {len(tasks)} cartas a scrapear | {skipped} salteadas (actualizadas recientemente)")

    sem = asyncio.Semaphore(CONCURRENCY)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )

        results = await asyncio.gather(
            *[process_card(sem, ctx, cid, url, prices, today_str) for cid, url in tasks]
        )
        await browser.close()

    updated = sum(1 for r in results if r)

    # Verificar manuales fallidas
    manual_failed = [
        cid for cid, _ in tasks
        if cid in manual_ids and not prices.get(cid, {}).get("psa10")
    ]

    data["prices"] = prices
    data["updated"] = today_str
    with open(prices_file, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\nActualizadas: {updated} | Sin precio: {len(tasks) - updated} | Salteadas: {skipped}")
    if manual_failed:
        print("Manuales fallidas:", ", ".join(manual_failed))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(amain())
