/** Dice helpers — d20 tests and adv/dis stacking.
 * Mirrors `ai_dm.rules.dice.d20_test` and `combine_advantage`.
 *
 * Uses Foundry's `Roll` API when available; falls back to `Math.random`
 * so the module is testable in a non-Foundry context (e.g. node).
 */

export function combineAdvantage(advSources, disSources) {
    const a = Math.max(0, advSources | 0);
    const d = Math.max(0, disSources | 0);
    if (a > 0 && d > 0) return "normal";
    if (a > 0) return "advantage";
    if (d > 0) return "disadvantage";
    return "normal";
}

function rollOne() {
    return 1 + Math.floor(Math.random() * 20);
}

async function _foundryD20(advantage) {
    if (typeof Roll === "undefined") {
        if (advantage === "advantage") return Math.max(rollOne(), rollOne());
        if (advantage === "disadvantage") return Math.min(rollOne(), rollOne());
        return rollOne();
    }
    let expr = "1d20";
    if (advantage === "advantage") expr = "2d20kh1";
    else if (advantage === "disadvantage") expr = "2d20kl1";
    const r = await new Roll(expr).evaluate({ async: true });
    return Number(r.total);
}

/**
 * Unified d20 test: ability check / save / attack roll.
 *
 * Returns `{ roll, modifier, total, advantage, crit, fumble, dc, target,
 * success }`. For attacks (or any call with `ac`), nat 20 = auto-hit
 * and nat 1 = auto-miss; otherwise total >= dc.
 */
export async function d20Test({
    modifier = 0,
    dc = null,
    ac = null,
    advantage = "normal",
    isAttack = false,
} = {}) {
    const roll = await _foundryD20(advantage);
    const total = roll + Number(modifier);
    const target = ac != null ? ac : dc;
    const crit = roll === 20;
    const fumble = roll === 1;
    let success = null;
    if (isAttack || ac != null) {
        if (crit) success = true;
        else if (fumble) success = false;
        else if (target != null) success = total >= target;
    } else if (dc != null) {
        success = total >= dc;
    }
    return { roll, modifier: Number(modifier), total, advantage, crit, fumble,
             dc, target, success };
}

