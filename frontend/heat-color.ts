export type HeatCellColors = {
  backgroundColor: string;
  color: string;
};

// Every effective background (rgba composited over the #fffdf7 panel) must keep
// WCAG AA text contrast >= 4.5:1; tests/heat-color.test.mjs sweeps the ramp.
// Dark text passes on the translucent light-red segment up to 0.55 (worst case
// 7.12:1 at alpha 0.604). Hotter cells switch to light text over a solid ramp
// from #c83420 (5.20:1) to #8f2013 (8.66:1); #e03a24 itself only reaches 4.30:1.
const DARK_TEXT = "#171714";
const LIGHT_TEXT = "#fffdf7";
const DARK_TEXT_MAX_INTENSITY = 0.55;
const DARK_SEGMENT_FROM = { red: 200, green: 52, blue: 32 };
const DARK_SEGMENT_TO = { red: 143, green: 32, blue: 19 };

function mixChannel(from: number, to: number, position: number): number {
  return Math.round(from + (to - from) * position);
}

export function heatCellColors(intensity: number): HeatCellColors {
  const clamped = Number.isFinite(intensity) ? Math.min(1, Math.max(0, intensity)) : 0;
  if (clamped <= DARK_TEXT_MAX_INTENSITY) {
    return {
      backgroundColor: `rgba(224, 58, 36, ${0.12 + clamped * 0.88})`,
      color: DARK_TEXT,
    };
  }
  const position = (clamped - DARK_TEXT_MAX_INTENSITY) / (1 - DARK_TEXT_MAX_INTENSITY);
  const red = mixChannel(DARK_SEGMENT_FROM.red, DARK_SEGMENT_TO.red, position);
  const green = mixChannel(DARK_SEGMENT_FROM.green, DARK_SEGMENT_TO.green, position);
  const blue = mixChannel(DARK_SEGMENT_FROM.blue, DARK_SEGMENT_TO.blue, position);
  return {
    backgroundColor: `rgb(${red}, ${green}, ${blue})`,
    color: LIGHT_TEXT,
  };
}
