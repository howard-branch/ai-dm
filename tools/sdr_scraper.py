#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup

BASE = "https://www.dndbeyond.com"

def get_page(url):
    r = requests.get(url)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

def extract_spells():
    soup = get_page(BASE + "/srd/spells")

    spells = []

    for link in soup.select("a[href^='/spells/']"):
        name = link.text.strip()
        href = link.get("href")

        spell_url = BASE + href
        spell_soup = get_page(spell_url)

        content = spell_soup.select_one(".content-container")
        text = content.get_text("\n", strip=True) if content else ""

        spells.append({
            "name": name,
            "url": spell_url,
            "text": text
        })

    return spells


if __name__ == "__main__":
    spells = extract_spells()

    import json
    with open("srd_spells_raw.json", "w") as f:
        json.dump(spells, f, indent=2)