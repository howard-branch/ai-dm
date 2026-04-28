/** Weapons — mirrors `ai_dm.rules.weapons`. */
import { srd } from "./core_loader.js";

function _weapons() { return srd()?.weapons?.weapons ?? []; }

export function getWeapon(key) {
    return _weapons().find((w) => w.key === key) ?? null;
}

export function allWeapons() { return [..._weapons()]; }

export function isFinesse(w)    { return Array.isArray(w?.properties) && w.properties.includes("finesse"); }
export function isLight(w)      { return Array.isArray(w?.properties) && w.properties.includes("light"); }
export function isHeavy(w)      { return Array.isArray(w?.properties) && w.properties.includes("heavy"); }
export function isThrown(w)     { return Array.isArray(w?.properties) && w.properties.includes("thrown"); }
export function isTwoHanded(w)  { return Array.isArray(w?.properties) && w.properties.includes("two_handed"); }
export function isRanged(w)     { return typeof w?.category === "string" && w.category.endsWith("ranged"); }
export function hasReach(w)     { return Array.isArray(w?.properties) && w.properties.includes("reach"); }

export function damageFor(w, { twoHanded = false } = {}) {
    if (!w?.damage) return null;
    if (twoHanded && w.damage.versatile) return { dice: w.damage.versatile, type: w.damage.type };
    return { dice: w.damage.dice, type: w.damage.type };
}

export function attackRange(w) {
    if (w?.range) return [Number(w.range.normal), Number(w.range.long)];
    const reach = hasReach(w) ? 10 : 5;
    return [reach, reach];
}

