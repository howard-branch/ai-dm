/** Ability scores & modifiers — mirrors `ai_dm.rules.abilities`. */
import { srd } from "./core_loader.js";

export const ABILITIES = ["str", "dex", "con", "int", "wis", "cha"];

export function modifier(score) {
    const data = srd()?.abilities ?? { score_min: 1, score_max: 30 };
    const s = Number(score);
    if (!Number.isFinite(s) || s < data.score_min || s > data.score_max) {
        throw new RangeError(`ability score ${score} out of range`);
    }
    return Math.floor((s - 10) / 2);
}

export function abilityMods(scores) {
    const out = {};
    for (const ab of ABILITIES) {
        out[ab] = modifier(scores?.[ab] ?? 10);
    }
    return out;
}

export function savingThrowMods(scores, { proficiencyBonus = 0, proficientIn = [] } = {}) {
    const prof = new Set((proficientIn || []).map((s) => s.toLowerCase()));
    const mods = abilityMods(scores);
    const out = {};
    for (const ab of ABILITIES) {
        out[ab] = mods[ab] + (prof.has(ab) ? Number(proficiencyBonus) : 0);
    }
    return out;
}

