/** Adventuring gear / packs — mirrors `ai_dm.rules.adventuring_gear`. */
import { srd } from "./core_loader.js";

function _items() { return srd()?.adventuring_gear?.items ?? []; }

export function getGear(key) { return _items().find((g) => g.key === key) ?? null; }
export function allGear()    { return [..._items()]; }

/** Flatten a pack to its [{ref, qty}] contents; non-pack returns [{ref:key, qty:1}]. */
export function expandPack(key) {
    const rec = getGear(key);
    if (!rec || rec.category !== "pack") return [{ ref: key, qty: 1 }];
    return [...(rec.contents ?? [])];
}

