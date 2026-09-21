// Only http(s) links are safe to put in an href: source URLs come from web
// search results (and roadmap guides), so a `javascript:` or `data:` URL would
// execute on click (23.5e). Anything else renders as plain text.
export function safeUrl(url: string | undefined | null): string | null {
  if (!url) return null;
  try {
    // ABSOLUTE parse (no base): resolving against our own origin would turn a
    // garbage source like "not a url" into a link to our own site.
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;   // relative, malformed, or not a URL at all
  }
}
