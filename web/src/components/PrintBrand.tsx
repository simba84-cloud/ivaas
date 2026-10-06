/** The top of a printed report: the Liquid logo, the report's name, and the magenta
 *  rule under them, as on the PDFs the API renders. Shown only on paper; the foot of
 *  every page (brand and page number) comes from the @page rule in index.css. */
export function PrintBrand({ title }: { title: string }) {
  return (
    <div className="mb-5 hidden items-end justify-between border-b-2 border-accent pb-2 print:flex" data-testid="print-brand">
      <img src="/logo-liquid.png" alt="Liquid Intelligent Technologies" className="h-9 w-auto" />
      <div className="text-right">
        <div className="text-sm font-bold text-brand">{title}</div>
        <div className="text-[10px] text-muted">IVaaS crate counting</div>
      </div>
    </div>
  );
}
