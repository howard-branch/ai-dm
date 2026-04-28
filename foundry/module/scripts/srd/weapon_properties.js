/** Weapon properties glossary — mirrors `ai_dm.rules.weapons` (properties side). */
import { srd } from "./core_loader.js";

export const WEAPON_PROPERTIES = [
    "ammunition", "finesse", "heavy", "light", "loading",
    "range", "reach", "thrown", "two_handed", "versatile",
];

export function weaponProperties() {
    return srd()?.weapon_properties?.properties ?? [];
}

export function getWeaponProperty(key) {
    return weaponProperties().find((p) => p.key === key) ?? null;
}

