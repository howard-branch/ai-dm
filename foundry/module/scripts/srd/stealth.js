/** Hiding & invisibility — mirrors `ai_dm.rules.stealth`. */
import { srd } from "./core_loader.js";

function _data() {
    return srd()?.stealth ?? {
        break_triggers: ["attack", "cast_spell", "speak_loud", "leave_cover"],
        invisible_grants: { attacker_advantage: true, target_disadvantage: true },
        unseen_attacker_advantage: true,
    };
}

export function BREAK_TRIGGERS() {
    return [..._data().break_triggers];
}

export function breaksOn(actionKind) {
    return _data().break_triggers.includes(actionKind);
}

export function maybeBreak(actor, actionKind) {
    if (!actor?.hidden) return false;
    if (!breaksOn(actionKind)) return false;
    actor.hidden = false;
    return true;
}

export function attackAdvantage({
    attackerInvisible = false, attackerUnseen = false,
    targetInvisible = false, targetUnseen = false,
} = {}) {
    const d = _data();
    const adv = (attackerInvisible && d.invisible_grants?.attacker_advantage)
        || (attackerUnseen && d.unseen_attacker_advantage);
    const dis = (targetInvisible && d.invisible_grants?.target_disadvantage)
        || targetUnseen;
    if (adv && !dis) return "advantage";
    if (dis && !adv) return "disadvantage";
    return "normal";
}

