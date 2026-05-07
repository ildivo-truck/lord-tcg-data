#!/usr/bin/env python3
import json
import re
import sys
import time
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


def parse_price(text):
    # Take only the first token before any space (ignores "+$73.76" delta suffix)
    first = text.strip().split()[0] if text.strip() else ""
    clean = re.sub(r"[^0-9.]", "", first)
    try:
        val = float(clean)
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def get_psa10(url):
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
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            debug = "--debug" in sys.argv

            header_cols = None
            psa10_index = None

            for row in page.query_selector_all("tr"):
                try:
                    cells = row.query_selector_all("td")
                    if not cells:
                        continue
                    row_text = row.inner_text().strip()
                    if not row_text:
                        continue

                    cols = [c.strip() for c in row_text.split("\t")]

                    if debug:
                        print(f"  ROW: {repr(row_text[:120])}")

                    # Find the header row that contains "PSA 10"
                    if "PSA 10" in cols:
                        header_cols = cols
                        psa10_index = cols.index("PSA 10")
                        if debug:
                            print(f"  -> Header encontrado, PSA 10 en columna {psa10_index}")
                        continue

                    # Next data row after header — extract PSA 10 price
                    if psa10_index is not None:
                        if len(cols) > psa10_index:
                            price = parse_price(cols[psa10_index])
                            if price is not None:
                                browser.close()
                                return price
                        psa10_index = None  # reset and keep looking

                except Exception:
                    continue

        except Exception as e:
            print(f"    Error: {e}")
        finally:
            try:
                browser.close()
            except Exception:
                pass

    return None


def main():
    track_file = ROOT / "to_track.json"
    prices_file = ROOT / "prices.json"

    with open(track_file) as f:
        to_track = json.load(f)

    with open(prices_file) as f:
        data = json.load(f)

    prices = data.get("prices", {})
    updated = 0
    failed = []

    for card_id, url in to_track.items():
        slug = url.split("/")[-1]
        print(f"-> {card_id} ({slug})")
        psa10 = get_psa10(url)

        if psa10 is not None:
            if card_id not in prices:
                prices[card_id] = {}
            prices[card_id]["psa10"] = psa10
            print(f"   OK PSA 10: ${psa10:,.2f}")
            updated += 1
        else:
            failed.append(card_id)
            print(f"   FAIL: no encontrado")

        time.sleep(2)

    data["prices"] = prices
    data["updated"] = str(date.today())

    with open(prices_file, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\nActualizadas: {updated} | Fallidas: {len(failed)}")
    if failed:
        print("Fallidas:", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
