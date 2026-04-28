/** Armor & shields — mirrors `ai_dm.rules.armor`. */
import { srd } from "./core_loader.js";

export const UNARMORED_AC = 10;

function _armor() { return srd()?.armor?.armors ?? []; }

export function getArmor(key) { return _armor().find((a) => a.key === key) ?? null; }
export function allArmor()    { return [..._armor()]; }

function _dex(mode, dexMod) {
    if (mode === "add") return Number(dexMod);
    if (mode === "add_max_2") return Math.min(Number(dexMod), 2);
    return 0;
}

export function computeAc(armor, dexMod, { shield = null } = {}) {
    let ac;
    if (!armor) {
        ac = UNARMORED_AC + Number(dexMod);
    } else if (armor.ac?.dex === "flat") {
        ac = UNARMORED_AC + Number(dexMod) + Number(armor.ac.base);
    } else {
        ac = Number(armor.ac.base) + _dex(armor.ac.dex, dexMod);
    }
    if (shield && shield.ac?.dex === "flat") ac += Number(shield.ac.base);
    return ac;
}

export function meetsStrengthRequirement(armor, str) {
    if (!armor || armor.strength_req == null) return true;
    return Number(str) >= Number(armor.strength_req);
}

export function imposesStealthDisadvantage(armor) {
    return Boolean(armor?.stealth_disadvantage);
}

