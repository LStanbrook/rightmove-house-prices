"""Edinburgh postcode districts -> recognisable neighbourhood names.

Rightmove listings carry an ``outcode`` (EH1-EH17 for the core city). These are
the common local names people actually use for each district, for labelling the
dashboard. Approximate — several districts span more than one neighbourhood.
"""
from __future__ import annotations

AREA_NAMES: dict[str, str] = {
    "EH1": "Old Town & city centre",
    "EH2": "New Town",
    "EH3": "New Town, West End & Stockbridge",
    "EH4": "Comely Bank, Blackhall & Cramond",
    "EH5": "Trinity, Granton & Newhaven",
    "EH6": "Leith",
    "EH7": "Easter Road, Abbeyhill & Restalrig",
    "EH8": "Newington, Holyrood & Meadowbank",
    "EH9": "Marchmont, Grange & Newington",
    "EH10": "Morningside & Bruntsfield",
    "EH11": "Gorgie & Dalry",
    "EH12": "Corstorphine & Murrayfield",
    "EH13": "Colinton",
    "EH14": "Currie, Juniper Green & Wester Hailes",
    "EH15": "Portobello & Joppa",
    "EH16": "Liberton & Craigmillar",
    "EH17": "Gilmerton & Moredun",
}


def area_label(outcode: str | None) -> str:
    if not outcode:
        return "Unknown"
    name = AREA_NAMES.get(outcode.upper())
    return f"{outcode.upper()} · {name}" if name else outcode.upper()


def area_short(outcode: str | None) -> str:
    """Just the neighbourhood name, prefixed with the outcode for disambiguation."""
    if not outcode:
        return "Unknown"
    name = AREA_NAMES.get(outcode.upper())
    return name or outcode.upper()
