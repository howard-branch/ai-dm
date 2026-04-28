/** Currency — mirrors `ai_dm.rules.currency`. */
import { srd } from "./core_loader.js";

export const COIN_KEYS = ["cp", "sp", "ep", "gp", "pp"];

function _gpValue() {
    const data = srd()?.currency?.coins ?? [];
    const out = {};
    for (const c of data) out[c.key] = Number(c.gp_value);
    return out;
}

export function totalGp(coins) {
    const v = _gpValue();
    let gp = 0;
    for (const k of COIN_KEYS) gp += Number(coins?.[k] || 0) * (v[k] ?? 0);
    return Math.round(gp * 10000) / 10000;
}

export function coinWeight(coins) {
    const perPound = Number(srd()?.currency?.coins_per_pound ?? 50);
    let n = 0;
    for (const k of COIN_KEYS) n += Number(coins?.[k] || 0);
    return Math.round((n / perPound) * 10000) / 10000;
}

