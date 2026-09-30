// Approximate English names for board colors, for labels and screen readers.
export function colorName(hex: string): string {
  const match = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!match) return "colored";
  const value = parseInt(match[1], 16);
  const [r, g, b] = [(value >> 16) & 255, (value >> 8) & 255, value & 255].map((channel) => channel / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const light = (max + min) / 2;
  const delta = max - min;
  const sat = delta === 0 ? 0 : delta / (1 - Math.abs(2 * light - 1));
  if (sat < 0.18 || delta < 0.08) return light > 0.85 ? "white" : light < 0.18 ? "black" : "gray";
  let hue =
    max === r ? ((g - b) / delta) % 6 : max === g ? (b - r) / delta + 2 : (r - g) / delta + 4;
  hue = (hue * 60 + 360) % 360;
  if (hue < 15 || hue >= 345) return light > 0.75 ? "pink" : light < 0.28 ? "maroon" : "red";
  if (hue < 42) return light < 0.35 || sat < 0.4 ? "brown" : light > 0.75 ? "peach" : "orange";
  if (hue < 68) return light < 0.3 ? "olive" : "yellow";
  if (hue < 90) return "lime";
  if (hue < 160) return light < 0.25 ? "dark green" : "green";
  if (hue < 195) return light < 0.35 ? "teal" : "cyan";
  if (hue < 255) return light < 0.25 ? "navy" : light > 0.72 ? "light blue" : "blue";
  if (hue < 290) return light > 0.72 ? "lavender" : "purple";
  return light < 0.35 ? "purple" : light > 0.72 ? "pink" : "magenta";
}

export function capitalized(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
