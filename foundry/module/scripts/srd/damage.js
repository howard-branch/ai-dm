/** Damage / healing / temp HP / resistance / vulnerability / immunity.
 * Mirrors `ai_dm.rules.damage`.
 */
import { srd } from "./core_loader.js";

export function damageTypes() {
    return [...(srd()?.damage_types?.types ?? [])];
}

export function applyModifiers(amount, damageType, {
    resistances = [], vulnerabilities = [], immunities = [],
} = {}) {
    if (!(amount > 0)) return 0;
    const imm = new Set(immunities);
    if (imm.has(damageType)) return 0;
    const res = new Set(resistances);
    const vuln = new Set(vulnerabilities);
    const inRes = res.has(damageType);
    const inVuln = vuln.has(damageType);
    if (inRes && inVuln) return amount;
    if (inVuln) return amount * 2;
    if (inRes) return Math.floor(amount / 2);
    return amount;
}

/**
 * Apply pre-modified damage to a plain object target with `{hp, max_hp,
 * temp_hp}` fields. Mutates and returns an outcome record.
 */
export function applyDamage(target, amount, damageType = "untyped") {
    const requested = Math.max(0, amount | 0);
    const hpBefore = Number(target.hp ?? 0);
    if (requested === 0) {
        return { requested: 0, absorbedByTempHp: 0, dealt: 0,
                 hpBefore, hpAfter: hpBefore, droppedToZero: false, damageType };
    }
    const temp = Number(target.temp_hp ?? 0);
    const absorbed = Math.min(temp, requested);
    if (absorbed) target.temp_hp = temp - absorbed;
    const remaining = requested - absorbed;
    const hpAfter = Math.max(0, hpBefore - remaining);
    target.hp = hpAfter;
    return {
        requested, absorbedByTempHp: absorbed, dealt: remaining,
        hpBefore, hpAfter, droppedToZero: hpBefore > 0 && hpAfter === 0,
        damageType,
    };
}

export function applyHealing(target, amount) {
    if (!(amount > 0)) return Number(target.hp ?? 0);
    const hp = Number(target.hp ?? 0);
    const maxHp = Number(target.max_hp ?? hp + amount);
    const newHp = Math.min(maxHp || (hp + amount), hp + amount);
    target.hp = newHp;
    return newHp;
}

/** Temp HP do not stack — take the higher of current and incoming. */
export function grantTempHp(target, amount) {
    if (!(amount > 0)) return Number(target.temp_hp ?? 0);
    const cur = Number(target.temp_hp ?? 0);
    const next = Math.max(cur, amount | 0);
    target.temp_hp = next;
    return next;
}

