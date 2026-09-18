import { expect, describe, it } from 'vitest';
import en from '../../locales/en.json';
import vi from '../../locales/vi.json';

describe('locale dictionaries', () => {
  it('translates the days unit in Vietnamese', () => {
    expect((vi as Record<string, { unit: Record<string, string> }>).modbus.unit.days).toBe('ngày');
  });

  it('keeps the days unit English in English', () => {
    expect((en as Record<string, { unit: Record<string, string> }>).modbus.unit.days).toBe('days');
  });
});