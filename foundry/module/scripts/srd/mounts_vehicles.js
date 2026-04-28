/** Mounts, draft animals, tack and vehicles — mirrors `ai_dm.rules.mounts`. */
import { srd } from "./core_loader.js";

function _entries() { return srd()?.mounts_vehicles?.entries ?? []; }

export function getEntry(key) { return _entries().find((e) => e.key === key) ?? null; }
export function allEntries()  { return [..._entries()]; }

