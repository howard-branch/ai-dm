/** Death-save state machine — mirrors `ai_dm.rules.death_saves`. */
import { srd } from "./core_loader.js";

function cfg() {
    return srd()?.death_saves ?? {
        dc: 10, successes_to_stable: 3, failures_to_die: 3,
        nat20_heals_to: 1, nat1_failures: 2,
        damage_at_zero_failures: 1, crit_at_zero_failures: 2,
        massive_damage_threshold_factor: 2,
    };
}

export function newTrack() {
    return { successes: 0, failures: 0, stable: false, dead: false };
}

export function rollDeathSave(track, naturalRoll) {
    const C = cfg();
    const nat = naturalRoll | 0;
    if (nat === 20) {
        Object.assign(track, newTrack());
        return { roll: 20, success: true, crit: true, fumble: false,
                 track, healedTo: C.nat20_heals_to, becameStable: false, died: false };
    }
    if (nat === 1) {
        track.failures = Math.min(C.failures_to_die, track.failures + C.nat1_failures);
    } else if (nat >= C.dc) {
        track.successes = Math.min(C.successes_to_stable, track.successes + 1);
    } else {
        track.failures = Math.min(C.failures_to_die, track.failures + 1);
    }
    let died = false, becameStable = false;
    if (track.failures >= C.failures_to_die) { track.dead = true; died = true; }
    else if (track.successes >= C.successes_to_stable) { track.stable = true; becameStable = true; }
    return { roll: nat, success: nat >= C.dc && nat !== 1, crit: false,
             fumble: nat === 1, track, healedTo: null, becameStable, died };
}

export function damageAtZero(track, { crit = false } = {}) {
    const C = cfg();
    const add = crit ? C.crit_at_zero_failures : C.damage_at_zero_failures;
    track.failures = Math.min(C.failures_to_die, track.failures + add);
    track.stable = false;
    if (track.failures >= C.failures_to_die) track.dead = true;
    return track;
}

export function isMassiveDamage(amount, maxHp) {
    const C = cfg();
    if (!(maxHp > 0)) return false;
    return (amount | 0) >= (maxHp | 0) * C.massive_damage_threshold_factor;
}

