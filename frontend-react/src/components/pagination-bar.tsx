import { useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface PaginationBarProps {
  page: number;
  pages: number;
  onPageChange: (page: number) => void;
}

export function PaginationBar({ page, pages, onPageChange }: PaginationBarProps) {
  const [jumpValue, setJumpValue] = useState(String(page));

  useEffect(() => {
    setJumpValue(String(page));
  }, [page]);

  if (pages <= 1) {
    return null;
  }

  const jumpPage = Number(jumpValue);
  const canJump = jumpValue.trim() !== '' && Number.isInteger(jumpPage) && jumpPage >= 1 && jumpPage <= pages;

  return (
    <nav aria-label="论文结果分页" className="mt-8 flex flex-col items-center justify-center gap-3 rounded-2xl bg-white/80 p-4 shadow-sm ring-1 ring-black/5 sm:flex-row">
      <Button
        variant="outline"
        onClick={() => onPageChange(page - 1)}
        disabled={page <= 1}
        className="w-full rounded-xl sm:w-auto"
      >
        <ChevronLeft className="mr-1 h-4 w-4" />
        上一页
      </Button>

      <div aria-live="polite" aria-atomic="true" className="text-sm text-[#596579]">第 {page} / {pages} 页</div>

      <form
        aria-label="跳转到指定页"
        className="flex w-full items-center gap-2 sm:w-auto"
        onSubmit={(event) => {
          event.preventDefault();
          if (canJump) {
            onPageChange(jumpPage);
          }
        }}
      >
        <Input
          type="number"
          aria-label={`页码，共 ${pages} 页`}
          inputMode="numeric"
          enterKeyHint="go"
          min={1}
          max={pages}
          step={1}
          required
          value={jumpValue}
          onChange={(event) => setJumpValue(event.target.value)}
          className="h-10 w-full rounded-xl bg-white sm:w-24"
        />
        <Button
          variant="outline"
          type="submit"
          className="rounded-xl"
          disabled={!canJump}
        >
          跳转
        </Button>
      </form>

      <Button
        variant="outline"
        onClick={() => onPageChange(page + 1)}
        disabled={page >= pages}
        className="w-full rounded-xl sm:w-auto"
      >
        下一页
        <ChevronRight className="ml-1 h-4 w-4" />
      </Button>
    </nav>
  );
}
