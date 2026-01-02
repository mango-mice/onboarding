export function convertTemperature(value, from, to, precision = 2) {
  let result;
  if (from === "C" && to === "F") {
    result = value * (9 / 5) + 32;
  } else if (from === "F" && to === "C") {
    result = (value - 32) * (5 / 9);
  } else {
    throw new Error(`Unsupported temperature conversion: ${from} to ${to}`);
  }
  return Number(result.toFixed(precision));
}
