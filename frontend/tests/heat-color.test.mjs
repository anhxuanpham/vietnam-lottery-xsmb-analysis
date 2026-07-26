import assert from "node:assert/strict";
import test from "node:test";

import { heatCellColors } from "../heat-color.ts";

const PANEL_BACKGROUND = [255, 253, 247];
const WCAG_AA_MINIMUM = 4.5;

function parseCssColor(value) {
  const hexMatch = /^#([0-9a-f]{6})$/i.exec(value);
  if (hexMatch) {
    return {
      rgb: [0, 2, 4].map((offset) => Number.parseInt(hexMatch[1].slice(offset, offset + 2), 16)),
      alpha: 1,
    };
  }
  const rgbMatch = /^rgba?\(([^)]+)\)$/.exec(value);
  assert.ok(rgbMatch, `unparseable CSS color: ${value}`);
  const parts = rgbMatch[1].split(",").map((part) => Number(part.trim()));
  assert.ok(parts.length === 3 || parts.length === 4, `unexpected channel count: ${value}`);
  assert.ok(parts.every((part) => Number.isFinite(part)), `non-numeric channel: ${value}`);
  return { rgb: parts.slice(0, 3), alpha: parts.length === 4 ? parts[3] : 1 };
}

function compositeOverPanel(color) {
  return color.rgb.map((channel, index) =>
    color.alpha * channel + (1 - color.alpha) * PANEL_BACKGROUND[index]
  );
}

function linearChannel(value) {
  const scaled = value / 255;
  return scaled <= 0.03928 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4;
}

function relativeLuminance([red, green, blue]) {
  return 0.2126 * linearChannel(red) + 0.7152 * linearChannel(green) + 0.0722 * linearChannel(blue);
}

function contrastRatio(left, right) {
  const lighter = Math.max(left, right);
  const darker = Math.min(left, right);
  return (lighter + 0.05) / (darker + 0.05);
}

function effectiveCell(intensity) {
  const { backgroundColor, color } = heatCellColors(intensity);
  const background = compositeOverPanel(parseCssColor(backgroundColor));
  const text = compositeOverPanel(parseCssColor(color));
  return {
    backgroundLuminance: relativeLuminance(background),
    textLuminance: relativeLuminance(text),
  };
}

test("every intensity keeps WCAG AA text contrast against the composited background", () => {
  for (let step = 0; step <= 100; step += 1) {
    const intensity = step / 100;
    const { backgroundLuminance, textLuminance } = effectiveCell(intensity);
    const ratio = contrastRatio(textLuminance, backgroundLuminance);
    assert.ok(
      ratio >= WCAG_AA_MINIMUM,
      `intensity ${intensity.toFixed(2)} has contrast ${ratio.toFixed(3)} < ${WCAG_AA_MINIMUM}`,
    );
  }
});

test("effective background gets monotonically hotter as intensity rises", () => {
  let previous = Number.POSITIVE_INFINITY;
  for (let step = 0; step <= 100; step += 1) {
    const intensity = step / 100;
    const { backgroundLuminance } = effectiveCell(intensity);
    assert.ok(
      backgroundLuminance <= previous + 1e-12,
      `intensity ${intensity.toFixed(2)} is lighter than the previous step`,
    );
    previous = backgroundLuminance;
  }
});

test("keeps the current light-end look and clamps out-of-range intensities", () => {
  assert.deepEqual(heatCellColors(0), {
    backgroundColor: "rgba(224, 58, 36, 0.12)",
    color: "#171714",
  });
  assert.equal(heatCellColors(0.55).color, "#171714");
  assert.equal(heatCellColors(0.56).color, "#fffdf7");
  assert.deepEqual(heatCellColors(-1), heatCellColors(0));
  assert.deepEqual(heatCellColors(2), heatCellColors(1));
  assert.deepEqual(heatCellColors(Number.NaN), heatCellColors(0));
});
