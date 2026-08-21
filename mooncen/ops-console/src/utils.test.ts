import { describe, expect, it } from 'vitest';
import { csvCellText } from './utils';

describe('csvCellText', () => {
  it.each(['=1+1', '+cmd', '-2+3', '@SUM(A1:A2)', '  =HYPERLINK("x")', '\t+1']) (
    'neutralizes spreadsheet formulas in string cells: %s',
    (value) => {
      expect(csvCellText(value)).toBe(`'${value}`);
    },
  );

  it('keeps numeric values numeric and serializes provider count maps', () => {
    expect(csvCellText(-1)).toBe('-1');
    expect(csvCellText({ PROVIDER_A: 0 })).toBe('{"PROVIDER_A":0}');
  });
});
