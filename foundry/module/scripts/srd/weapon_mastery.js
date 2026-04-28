/** Weapon mastery (2024 SRD) — mirrors `ai_dm.rules.weapon_mastery`. */
import { srd } from "./core_loader.js";

export const MASTERY_KEYS = ["cleave", "graze", "nick", "push", "sap", "slow", "topple", "vex"];

export function getMastery(key) {
    const list = srd()?.weapon_mastery?.masteries ?? [];
    return list.find((m) => m.key === key) ?? null;
}

export function masteryCountFor(classKey, level) {
    const table = srd()?.weapon_mastery?.class_progression?.[String(classKey).toLowerCase()];
    if (!table) return 0;
    let best = 0;
    for (const [k, n] of Object.entries(table)) {
        if (Number(k) <= Number(level)) best = Math.max(best, Number(n));
    }
    return best;
}

/** Compute the structured effect of a mastery on a single attack.
 *  Returns `{ key, effects: [...] }`; `key === null` if unknown.
 */
export function applyMastery(name, ctx = {}) {
    const { hit = false, damage = 0, weapon = null, target = null,
            attackAbilityMod = 0, proficiencyBonus = 0 } = ctx;
    if (!name || !MASTERY_KEYS.includes(name)) return { key: null, effects: [] };
    const out = { key: name, effects: [] };
    const targetId = target?.actor_id ?? null;
    const dmgType = weapon?.damage?.type ?? null;

    switch (name) {
        case "cleave":
            if (hit && weapon?.properties?.includes("heavy")) {
                out.effects.push({ kind: "cleave", weapon_die: weapon?.damage?.dice, damage_type: dmgType });
            }
            break;
        case "graze":
            if (!hit) {
                const bonus = Math.max(0, Number(attackAbilityMod));
                if (bonus) out.effects.push({ kind: "graze_damage", amount: bonus, target_id: targetId, damage_type: dmgType });
            }
            break;
        case "nick":
            out.effects.push({ kind: "nick_extra_attack_in_action" });
            break;
        case "push":
            if (hit) out.effects.push({ kind: "push", target_id: targetId, distance_ft: 10, max_size: "large" });
            break;
        case "sap":
            if (hit) out.effects.push({ kind: "disadvantage_on_next_attack", target_id: targetId });
            break;
        case "slow":
            if (hit && damage > 0) out.effects.push({ kind: "speed_reduction", target_id: targetId, amount_ft: -10 });
            break;
        case "topple":
            if (hit) {
                const dc = 8 + Number(attackAbilityMod) + Number(proficiencyBonus);
                out.effects.push({ kind: "request_save", target_id: targetId, ability: "con", dc, on_fail: "prone" });
            }
            break;
        case "vex":
            if (hit && damage > 0) out.effects.push({ kind: "advantage_on_next_attack", target_id: targetId });
            break;
    }
    return out;
}

