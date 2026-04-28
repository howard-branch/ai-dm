/** Concentration — mirrors `ai_dm.rules.concentration`. */
import { srd } from "./core_loader.js";

function _data() {
    return srd()?.concentration ?? {
        save: "con", min_dc: 10,
        broken_by: ["incapacitated", "killed", "second_concentration"],
        auto_drop_at_zero_hp: true,
    };
}

export const SAVE_ABILITY = "con";

export function MIN_DC() { return Number(_data().min_dc || 10); }
export function BROKEN_BY() { return [..._data().broken_by]; }

export function dcForDamage(amount) {
    return Math.max(MIN_DC(), Math.floor(Number(amount) / 2));
}

export function rollSave({ damage, roll, modifier = 0 }) {
    const dc = dcForDamage(damage);
    const total = Number(roll) + Number(modifier);
    return { success: total >= dc, total, dc, broken: total < dc };
}

export function onCondition(actor, condition) {
    const broken = new Set([..._data().broken_by, "incapacitated", "unconscious", "stunned", "paralyzed", "petrified"]);
    if (!broken.has(condition)) return false;
    if (actor?.concentration == null) return false;
    actor.concentration = null;
    return true;
}

