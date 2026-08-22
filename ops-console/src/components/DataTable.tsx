import { flexRender, getCoreRowModel, getSortedRowModel, useReactTable, type ColumnDef, type SortingState } from '@tanstack/react-table';
import { useState } from 'react';
import { downloadCsv } from '../utils';

export default function DataTable<T extends object>({
  data,
  columns,
  exportName,
  exportData,
  onRowClick,
  getRowClassName,
}: {
  data: T[];
  columns: ColumnDef<T>[];
  exportName: string;
  exportData?: Array<Record<string, unknown>>;
  onRowClick?: (row: T) => void;
  getRowClassName?: (row: T) => string | undefined;
}) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <section className="table-shell">
      <div className="table-toolbar">
        <span>{data.length.toLocaleString('ko-KR')}건</span>
        <button
          className="button subtle"
          type="button"
          disabled={!data.length}
          onClick={() => downloadCsv(exportName, exportData ?? data as Array<Record<string, unknown>>)}
        >
          CSV 내보내기
        </button>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {group.headers.map((header) => (
                  <th key={header.id}>
                    {header.isPlaceholder ? null : (
                      <button
                        className="sort-button"
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        disabled={!header.column.getCanSort()}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {{ asc: ' ↑', desc: ' ↓' }[header.column.getIsSorted() as string] || ''}
                      </button>
                    )}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => {
              const rowClassName = [onRowClick ? 'clickable-row' : '', getRowClassName?.(row.original) || '']
                .filter(Boolean)
                .join(' ');
              return (
              <tr key={row.id} className={rowClassName} onClick={() => onRowClick?.(row.original)}>
                {row.getVisibleCells().map((cell) => (
                  <td
                    key={cell.id}
                    data-label={
                      typeof cell.column.columnDef.header === 'string'
                        ? cell.column.columnDef.header
                        : cell.column.id
                    }
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
