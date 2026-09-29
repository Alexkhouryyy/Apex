// Sampled motion for study subjects, written by scripts/build_study_models.py:
// per part, a turn about an axis through a pivot, a uniform scale about that
// pivot, an offset and a glow, sampled over one cycle (u = 0..1, both ends
// included). A car engine's crank, rods, pistons, cams and valves; a heart's
// chambers and valves. Pure functions, so timing is testable without a browser.

export const hasTracks = motion => !!motion && !!motion.tracks && typeof motion.tracks === 'object'
  && Number.isInteger(motion.samples) && motion.samples > 0;

// Cycle fraction after `seconds` of playing.
export function advance(u, seconds, motion) {
  const cycle = motion.cycle_seconds > 0 ? motion.cycle_seconds : 1;
  return (((u + seconds / cycle) % 1) + 1) % 1;
}

const lerp = (a, b, f) => a + (b - a) * f;

// The pose of one track at cycle fraction u (linear between samples).
export function sampleTrack(track, u, samples) {
  const x = Math.min(1, Math.max(0, u)) * samples, i = Math.min(samples - 1, Math.floor(x)), f = x - i;
  const at = list => lerp(list[i], list[i + 1], f);
  return {
    angle: track.angle ? at(track.angle) : 0,
    offset: track.offset ? [0, 1, 2].map(k => lerp(track.offset[i][k], track.offset[i + 1][k], f)) : [0, 0, 0],
    scale: track.scale ? at(track.scale) : 1,
    glow: track.glow ? Math.max(0, at(track.glow)) : 0,
  };
}

// What is happening at u, for the caption ("Power 4 · Compression 2 …").
export function phaseAt(motion, u) {
  return (motion.phases || []).find(p => u >= p.from && u < p.to)?.text || '';
}
