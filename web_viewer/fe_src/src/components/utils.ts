export function fixedIfNeed(num: number, fixedNumber = 1) {
  if (num > 0) {
    return num.toFixed(fixedNumber);
  }
  return num;
}

export const MWH_UNIT_CLASS = "unit-mwh";

export interface IFormattedTotal {
  value: number | string;
  unit: string;
  unitClassName?: string;
}

export function formatTotalValue(kwh: number): IFormattedTotal {
  if (kwh > 1000) {
    return {
      value: fixedIfNeed(kwh / 1000),
      unit: " MWh",
      unitClassName: MWH_UNIT_CLASS,
    };
  }
  return { value: fixedIfNeed(kwh), unit: " kWh" };
}

export const roundTo = (num: number, fixedNumber = 1): number => {
  const numToFixed = Math.pow(10, fixedNumber);
  return Math.round(num * numToFixed) / numToFixed;
};