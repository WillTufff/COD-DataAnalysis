// Pure slug helpers, kept free of any server-only imports so client components
// can build player and team links without pulling in the database layer.

export function playerSlug(handle: string): string {
  return handle.toLowerCase();
}

export function teamSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
