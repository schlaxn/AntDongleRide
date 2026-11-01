// WebAudio cues: a short beep for the 3-2-1 countdown, a longer tone when the phase changes.
// The AudioContext is created on the first user gesture (Start), as browsers require.

let ctx = null;
export function unlockAudio() {
  try {
    if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === "suspended") ctx.resume();
  } catch (_) { /* audio unavailable: silently skip */ }
}

function tone(freq, durationS, gain = 0.25) {
  if (!ctx) return;
  const osc = ctx.createOscillator();
  const g = ctx.createGain();
  osc.frequency.value = freq;
  g.gain.setValueAtTime(0, ctx.currentTime);
  g.gain.linearRampToValueAtTime(gain, ctx.currentTime + 0.01);
  g.gain.linearRampToValueAtTime(0, ctx.currentTime + durationS);
  osc.connect(g).connect(ctx.destination);
  osc.start();
  osc.stop(ctx.currentTime + durationS + 0.02);
}

export const beepCountdown = () => tone(880, 0.12);
export const beepPhaseChange = () => tone(1320, 0.5, 0.35);
