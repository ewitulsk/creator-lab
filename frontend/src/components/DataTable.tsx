import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { Table, TBody, TD, TH, THead, TR } from "./ui/primitives";

/** Client-side sortable table on TanStack Table. */
export function DataTable<T>({
  data,
  columns,
  initialSort = [],
  onRowClick,
  rowClassName,
}: {
  data: T[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  columns: ColumnDef<T, any>[];
  initialSort?: SortingState;
  onRowClick?: (row: T) => void;
  rowClassName?: (row: T) => string | undefined;
}) {
  const [sorting, setSorting] = useState<SortingState>(initialSort);
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <Table>
      <THead>
        {table.getHeaderGroups().map((hg) => (
          <TR key={hg.id} className="hover:bg-transparent">
            {hg.headers.map((h) => {
              const sorted = h.column.getIsSorted();
              const meta = h.column.columnDef.meta as { align?: "right" } | undefined;
              return (
                <TH
                  key={h.id}
                  className={cn(
                    h.column.getCanSort() && "cursor-pointer select-none hover:text-foreground",
                    meta?.align === "right" && "text-right",
                  )}
                  onClick={h.column.getToggleSortingHandler()}
                >
                  <span className="inline-flex items-center gap-1">
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    {sorted === "asc" ? <ArrowUp className="size-3" /> : sorted === "desc" ? <ArrowDown className="size-3" /> : null}
                  </span>
                </TH>
              );
            })}
          </TR>
        ))}
      </THead>
      <TBody>
        {table.getRowModel().rows.map((row) => (
          <TR
            key={row.id}
            className={cn(onRowClick && "cursor-pointer", rowClassName?.(row.original))}
            onClick={onRowClick ? () => onRowClick(row.original) : undefined}
          >
            {row.getVisibleCells().map((cell) => {
              const meta = cell.column.columnDef.meta as { align?: "right" } | undefined;
              return (
                <TD key={cell.id} className={cn(meta?.align === "right" && "text-right tabular")}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </TD>
              );
            })}
          </TR>
        ))}
      </TBody>
    </Table>
  );
}
