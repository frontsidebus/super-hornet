#!/usr/bin/env python3
"""Seed the Super Hornet ChromaDB knowledge base with Star Citizen data.

Usage:
    PYTHONPATH=orchestrator python3 tools/seed_knowledge.py
"""

from __future__ import annotations

import hashlib
import logging
import sys

import chromadb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("seed_knowledge")

CHROMADB_URL = "http://localhost:8000"
COLLECTION_NAME = "hornet_knowledge"

# ---------------------------------------------------------------------------
# Star Citizen knowledge documents
# ---------------------------------------------------------------------------

SHIPS = [
    (
        "Anvil F7C-M Super Hornet: The Super Hornet is a medium fighter and the "
        "military-spec variant of the F7C Hornet line. It features 2 seats (pilot "
        "and turret operator), with 2xS3 and 2xS2 weapon hardpoints for strong "
        "firepower. It has good shields for its size class, making it a durable "
        "dogfighter. The Super Hornet is a solid choice for bounty hunting and "
        "combat missions. Its twin-seat configuration allows a co-pilot to manage "
        "the turret independently."
    ),
    (
        "Anvil Carrack: The Carrack is a large exploration ship designed for "
        "long-range expeditions. It supports a crew of 4, and comes equipped "
        "with a medbay for healing and respawning, a vehicle hangar that fits a "
        "Pisces snub ship, a drone bay, and advanced scanning capabilities. Its "
        "large fuel reserves and quantum fuel capacity make it ideal for deep-space "
        "exploration. The Carrack is one of the most self-sufficient ships in the "
        "game."
    ),
    (
        "Drake Cutlass Black: The Cutlass Black is a medium multi-role ship and "
        "one of the most popular starter upgrades in Star Citizen. It has 2xS3 "
        "pilot-controlled weapons and an S3 turret for a co-pilot. With 46 SCU "
        "of cargo space, a side door for vehicle loading, and decent combat "
        "capability, it excels as an all-rounder for trading, bounty hunting, "
        "and general missions."
    ),
    (
        "RSI Constellation Andromeda: The Constellation Andromeda is a multi-crew "
        "ship with serious firepower. It mounts 4xS4 gimballed weapons on the "
        "pilot hardpoints, plus a manned turret, and carries a large missile "
        "payload. It also docks a Merlin snub fighter for additional support. "
        "The Andromeda offers 96 SCU of cargo space, making it viable for trade "
        "runs as well as combat operations."
    ),
    (
        "MISC Prospector: The Prospector is the entry-level solo mining ship. It "
        "mounts an S1 mining laser and has 32 SCU of ore capacity in its saddle "
        "bags. Pilots use the mining laser to fracture rocks and then extract "
        "valuable materials. The Prospector is the go-to ship for players getting "
        "into the mining profession. Upgradable mining heads like the Lancet or "
        "Helix can improve performance on different rock types."
    ),
    (
        "Aegis Gladius: The Gladius is a light fighter known for its agility and "
        "speed. It carries 3xS3 weapon hardpoints and is considered one of the "
        "most competitive dogfighters in arena and PvP combat. Its small profile "
        "and excellent maneuverability make it hard to hit, though it has lighter "
        "shields and armor compared to medium fighters like the Super Hornet."
    ),
]

LOCATIONS = [
    (
        "New Babbage on microTech: New Babbage is a high-tech city with a clean, "
        "modern aesthetic. The Commons is the main shopping area with weapons, "
        "armor, and clothing stores. Ships are spawned at the Aspire Grand hangar. "
        "microTech as a planet features snowy landscapes and is home to microTech "
        "corporation. New Babbage is known for its visually striking architecture "
        "and pleasant atmosphere."
    ),
    (
        "Lorville on Hurston: Lorville is a sprawling industrial city dominated "
        "by Hurston Dynamics. The CBD (Central Business District) is the main "
        "shopping hub. L19 Admin is where you pick up missions and handle "
        "administrative tasks. Ships are spawned at Teasa Spaceport. The city "
        "has a gritty, industrial feel with heavy security presence and restricted "
        "areas."
    ),
    (
        "Area18 on ArcCorp: Area18 is an urban cityscape on the planet-wide "
        "metropolis of ArcCorp. Riker Memorial Spaceport handles ship spawning. "
        "The main shopping area includes weapon and armor stores. IO-North Tower "
        "is a notable landmark. Area18 has a dense, cyberpunk-inspired aesthetic "
        "with towering buildings and neon lighting."
    ),
    (
        "Orison on Crusader: Orison is a unique landing zone built on floating "
        "platforms in the upper atmosphere of the gas giant Crusader. Ships are "
        "spawned at August Dunlow Spaceport. Orison is known for its beautiful "
        "views of clouds and sunsets. The platform layout means traversal can take "
        "longer than other landing zones, but the scenic beauty is unmatched."
    ),
    (
        "GrimHEX on asteroid Yela: GrimHEX is a pirate haven and outlaw station "
        "built into an asteroid orbiting Yela. Unlike lawful landing zones, "
        "GrimHEX has no armistice enforcement, meaning players can engage in "
        "combat within the station. It features a black market for purchasing "
        "illicit goods, and serves as a spawn point for players with criminal "
        "ratings. It is accessible to all players but caution is advised."
    ),
]

TRADING = [
    (
        "Star Citizen Trading Basics: The core principle of trading is to buy "
        "commodities low at their source and sell them high at destinations with "
        "demand. The website UEX Corp (uexcorp.space) provides real-time price "
        "data and trade route suggestions based on current server economics."
    ),
    (
        "High-Value Trade Commodities: Laranite and Agricium are among the most "
        "profitable commodities for trade runs. They offer high profit margins "
        "but require significant initial capital investment. Cargo capacity is "
        "measured in SCU (Standard Cargo Units), and larger ships like the "
        "Constellation or Caterpillar can carry more SCU for bigger profits per run."
    ),
    (
        "Trade Route Safety: Watch for interdiction events on trade routes, "
        "especially when carrying valuable cargo. NPC pirates and player pirates "
        "may pull you out of quantum travel. Flying with an escort or in a group "
        "reduces risk. Avoid predictable routes and consider less-trafficked paths "
        "for high-value cargo."
    ),
]

COMBAT = [
    (
        "Power Triangle Management: The power triangle lets you distribute power "
        "between weapons, shields, and engines using the F5 through F8 keys. "
        "F5 resets to default balanced distribution. F6 boosts weapons for more "
        "damage and faster recharge. F7 boosts shields for stronger protection "
        "and faster regeneration. F8 boosts engines for higher speed and "
        "acceleration. Managing the triangle is essential for competitive combat."
    ),
    (
        "Missiles and Countermeasures: Lock onto a target with middle mouse "
        "button, then fire missiles with the same button once locked. "
        "Countermeasures are critical for defense: press H to deploy chaff, "
        "which is effective against IR (infrared) tracking missiles. Press J "
        "to deploy flares or noise, which counters EM (electromagnetic) tracking "
        "missiles. Deploy countermeasures and break line of sight for best results."
    ),
    (
        "Advanced Flight Combat: Decoupled mode (V key) allows you to aim and "
        "shoot independently of your flight vector, which is powerful for strafing "
        "runs. SCM speed is your standard maneuvering speed and is sustainable. "
        "Afterburner provides a major speed boost but drains hydrogen fuel rapidly. "
        "Use afterburner for repositioning, then return to SCM for sustained "
        "engagements."
    ),
]

MINING = [
    (
        "Mining Ship and Equipment: The MISC Prospector is the entry-level mining "
        "ship for solo operators. Use the ship scanner (V key) to find mineable "
        "asteroid clusters and surface deposits. The scanner will highlight rocks "
        "and show their composition. Look for rocks with high percentages of "
        "valuable materials."
    ),
    (
        "Mining Laser Operation: Use the mining laser to fracture rocks by "
        "managing the energy level within the green optimal zone. If instability "
        "rises too high, the rock can explode and damage your ship. Throttle the "
        "laser power carefully and use consumables like Surge or Stampede modules "
        "to help control difficult rocks."
    ),
    (
        "Quantanium Mining: Quantanium is the most valuable mineable resource in "
        "Star Citizen but it is volatile. Once extracted, a 15-minute timer starts "
        "before it explodes, destroying your ship. You must reach a refinery "
        "station quickly. Refine your ore at stations like ARC-L1 or HUR-L2. "
        "Refineries process raw ore into refined materials that sell for higher "
        "prices. Different refinery methods trade speed for yield efficiency."
    ),
]

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("Connecting to ChromaDB at %s ...", CHROMADB_URL)
    try:
        client = chromadb.HttpClient(host="localhost", port=8000)
        client.heartbeat()
    except Exception as exc:
        log.error("Cannot connect to ChromaDB at %s: %s", CHROMADB_URL, exc)
        sys.exit(1)

    log.info("Connected. Getting or creating collection '%s' ...", COLLECTION_NAME)
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    # Build the full list of documents with metadata.
    all_docs: list[tuple[str, dict[str, str]]] = []

    for text in SHIPS:
        all_docs.append((text, {"category": "ships", "source": "seed_data"}))
    for text in LOCATIONS:
        all_docs.append((text, {"category": "locations", "source": "seed_data"}))
    for text in TRADING:
        all_docs.append((text, {"category": "trading", "source": "seed_data"}))
    for text in COMBAT:
        all_docs.append((text, {"category": "combat", "source": "seed_data"}))
    for text in MINING:
        all_docs.append((text, {"category": "mining", "source": "seed_data"}))

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, str]] = []

    for i, (text, meta) in enumerate(all_docs):
        doc_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        doc_id = f"seed_{meta['category']}_{doc_hash}"
        ids.append(doc_id)
        documents.append(text)
        metadatas.append(meta)

    log.info("Upserting %d documents across %d categories ...",
             len(ids),
             len({m["category"] for m in metadatas}))

    # Upsert so re-running the script is idempotent.
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

    total = collection.count()
    log.info("Done. Total documents in '%s': %d", COLLECTION_NAME, total)


if __name__ == "__main__":
    main()
