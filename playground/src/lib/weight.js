export function convertWeight(value, from, to, precision = 2) {
  let result;
  if (from === "g" && to === "oz") {
    result = value / 28.3495;
  } else if (from === "oz" && to === "g") {
    result = value * 28.3495;
  } else {
    throw new Error(`Unsupported weight conversion: ${from} to ${to}`);
  }
  return Number(result.toFixed(precision));
}
