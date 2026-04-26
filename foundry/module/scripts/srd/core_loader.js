/**
 * Loader for the SRD 5.2 core JSON catalog shared with the Python
 * runtime. The same files at `assets/srd5_2/core/` are mirrored into
 * the Foundry module's `assets/srd5_2/core/` folder by the
 * `scripts/sync_foundry_assets.py` build step so they're served at
 * `/modules/ai-dm-bridge/assets/srd5_2/core/`.
 *
 * Single source of truth — never duplicate values inline; always read
 * from this loader so a contract test (`test_srd_python_foundry_sync`)
 * can prove the two runtimes agree.
 */

const MODULE_ID = "ai-dm-bridge";
const BASE = `/modules/${MODULE_ID}/assets/srd5_2/core`;
const FILES = [
    "abilities", "proficiency", "dcs", "damage_types",
    "conditions", "exhaustion", "death_saves",
];

let _cache = null;
let _loading = null;

export async function loadSrdCore() {
    if (_cache) return _cache;
    if (_loading) return _loading;
    _loading = (async () => {
        const out = {};
        for (const name of FILES) {
            const res = await fetch(`${BASE}/${name}.json`);
            if (!res.ok) throw new Error(`failed to load SRD ${name}: ${res.status}`);
            out[name] = await res.json();
        }
        _cache = out;
        globalThis.AI_DM_SRD = out;
        return out;
    })();
    return _loading;
}

/** Sync accessor (returns null if `loadSrdCore` has not been awaited yet). */
export function srd() {
    return _cache;
}

