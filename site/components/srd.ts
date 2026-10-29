// The SRD PDF is served from public/. Its PDF page numbers match the page
// numbers printed in the SRD footer, which is what the index stores.
export const SRD_PDF = "/SRD_CC_v5.2.1.pdf";

export const srdPageHref = (page: number) => `${SRD_PDF}#page=${page}`;

export const pages = (start: number, end: number) =>
  start === end ? `p.${start}` : `pp.${start}–${end}`;
