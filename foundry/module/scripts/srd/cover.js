/** Cover — mirrors `ai_dm.rules.cover`. */
import { srd } from "./core_loader.js";

export const COVER = ["none", "half", "three_quarters", "total"];

function _level(key) {
    const data = srd()?.cover?.levels ?? [];
    return data.find((l) => l.key === key) ?? data.find((l) => l.key === "none") ?? {};
}

export function acBonus(cover) {
    const v = _level(cover).ac;
    return v == null ? 0 : Number(v);
}

export function dexSaveBonus(cover) {
    const v = _level(cover).save;
    return v == null ? 0 : Number(v);
}

export function blocks(cover) {
    return Boolean(_level(cover).blocks);
}

export function applyToTargetAc(targetAc, { cover = "none" } = {}) {
    return Number(targetAc) + acBonus(cover);
}

