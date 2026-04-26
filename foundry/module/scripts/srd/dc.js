/** Difficulty classes — mirrors `ai_dm.rules.dc`. */
import { srd } from "./core_loader.js";

export function namedDC(name) {
    const table = srd()?.dcs?.named ?? {};
    const key = String(name).trim().toLowerCase();
    if (!(key in table)) throw new Error(`unknown DC ${name}`);
    return table[key];
}

export function spellSaveDC(proficiencyBonus, abilityMod) {
    return 8 + Number(proficiencyBonus) + Number(abilityMod);
}

export function spellAttackBonus(proficiencyBonus, abilityMod) {
    return Number(proficiencyBonus) + Number(abilityMod);
}

