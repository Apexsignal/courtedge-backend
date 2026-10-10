import { getStore } from "@netlify/blobs"
import type { Config } from "@netlify/functions"

// Každá zpráva je samostatný blob s klíčem "RRRR-MM-DD/<id>", takže se
// souběžné zápisy nepřepisují a den je přímo v klíči.
const store = () => getStore({ name: "zpravy", consistency: "strong" })

const DAY = /^\d{4}-\d{2}-\d{2}$/
const KEY = /^\d{4}-\d{2}-\d{2}\/[a-z0-9-]+$/

function clean(v: unknown, max: number) {
  return typeof v === "string" ? v.trim().slice(0, max) : ""
}

export default async (req: Request) => {
  const s = store()

  if (req.method === "GET") {
    const { blobs } = await s.list()
    const items = await Promise.all(
      blobs.map(async b => ({ key: b.key, ...(await s.get(b.key, { type: "json" })) }))
    )
    return Response.json(items.filter(i => i.cas), { headers: { "cache-control": "no-store" } })
  }

  if (req.method === "POST") {
    let body: Record<string, unknown>
    try { body = await req.json() } catch { return new Response("Neplatná data", { status: 400 }) }
    const komu = clean(body.komu, 120)
    const cas = clean(body.cas, 16)
    const popis = clean(body.popis, 2000)
    const day = cas.slice(0, 10)
    if (!komu || !popis || !DAY.test(day)) return new Response("Vyplň komu, čas a popisek", { status: 400 })
    const key = `${day}/${Date.now().toString(36)}-${crypto.randomUUID().slice(0, 8)}`
    const item = { komu, cas, popis, vytvoreno: new Date().toISOString() }
    await s.setJSON(key, item)
    return Response.json({ key, ...item }, { status: 201 })
  }

  if (req.method === "DELETE") {
    const key = new URL(req.url).searchParams.get("key") || ""
    if (!KEY.test(key)) return new Response("Neplatný klíč", { status: 400 })
    await s.delete(key)
    return new Response(null, { status: 204 })
  }

  return new Response("Metoda není povolena", { status: 405 })
}

export const config: Config = { path: "/api/zpravy" }
