/** Grapple & shove — mirrors `ai_dm.rules.grapple`. */
import { srd } from "./core_loader.js";

const SIZES = ["tiny", "small", "medium", "large", "huge", "gargantuan"];

function _data() {
    return srd()?.grapple_shove ?? {
        grapple: { max_size_diff: 1, attacker_skill: "athletics", defender_skills: ["athletics", "acrobatics"] },
        shove: { options: ["push_5ft", "prone"] },
    };
}

export function MAX_SIZE_DIFF() { return Number(_data().grapple.max_size_diff || 1); }
export function SHOVE_OPTIONS() { return [..._data().shove.options]; }

export function sizeIndex(size) {
    const i = SIZES.indexOf(String(size || "medium").toLowerCase());
    return i < 0 ? SIZES.indexOf("medium") : i;
}

export function sizeAllows(attacker, target) {
    return (sizeIndex(target?.size) - sizeIndex(attacker?.size)) <= MAX_SIZE_DIFF();
}

export function attemptGrapple(attacker, target, { attackerRoll, defenderRoll, attackerMod = 0, defenderMod = 0 }) {
    if (!sizeAllows(attacker, target)) {
        return { success: false, attacker_total: 0, defender_total: 0, reason: "target too large" };
    }
    const at = Number(attackerRoll) + Number(attackerMod);
    const dt = Number(defenderRoll) + Number(defenderMod);
    return { success: at > dt, attacker_total: at, defender_total: dt };
}

export function attemptShove(attacker, target, { mode = "push_5ft", attackerRoll, defenderRoll, attackerMod = 0, defenderMod = 0 }) {
    if (!SHOVE_OPTIONS().includes(mode)) {
        return { success: false, mode, attacker_total: 0, defender_total: 0, reason: `unknown mode ${mode}` };
    }
    if (!sizeAllows(attacker, target)) {
        return { success: false, mode, attacker_total: 0, defender_total: 0, reason: "target too large" };
    }
    const at = Number(attackerRoll) + Number(attackerMod);
    const dt = Number(defenderRoll) + Number(defenderMod);
    return { success: at > dt, mode, attacker_total: at, defender_total: dt };
}

