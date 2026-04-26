/** Conditions catalog — mirrors `ai_dm.rules.conditions`. */
import { srd } from "./core_loader.js";

function records() {
    return srd()?.conditions?.conditions ?? [];
}

export function allConditions() {
    return records().map((r) => r.key);
}

export function conditionLabel(key) {
    return records().find((r) => r.key === key)?.label ?? key;
}

export function effects(key) {
    return { ...(records().find((r) => r.key === key)?.effects ?? {}) };
}

/** Expand with implied conditions (e.g. unconscious ⇒ prone, incapacitated). */
export function implied(conditions) {
    const out = new Set(conditions);
    let changed = true;
    while (changed) {
        changed = false;
        for (const c of [...out]) {
            const ef = effects(c);
            if (ef.incapacitated_implied && !out.has("incapacitated")) {
                out.add("incapacitated"); changed = true;
            }
            if (ef.prone_implied && !out.has("prone")) {
                out.add("prone"); changed = true;
            }
        }
    }
    return out;
}

const _hasAny = (set, key) => [...set].some((c) => effects(c)[key]);

export function attackerMod(conditions) {
    const cs = implied(conditions);
    return {
        advantage: _hasAny(cs, "attacker_advantage") && !_hasAny(cs, "attacker_disadvantage"),
        disadvantage: _hasAny(cs, "attacker_disadvantage") && !_hasAny(cs, "attacker_advantage"),
    };
}

export function targetMod(conditions) {
    const cs = implied(conditions);
    const adv = _hasAny(cs, "target_advantage") || _hasAny(cs, "target_advantage_melee");
    const dis = _hasAny(cs, "target_disadvantage");
    return {
        advantage: adv && !dis,
        disadvantage: dis && !adv,
    };
}

export function autoFailSaves(conditions) {
    const out = new Set();
    for (const c of implied(conditions)) {
        for (const ab of effects(c).auto_fail_saves || []) out.add(ab);
    }
    return out;
}

export function speedZero(conditions) {
    return _hasAny(implied(conditions), "speed_zero");
}

export function critOn5ft(conditions) {
    return _hasAny(implied(conditions), "attacks_within_5ft_crit");
}

